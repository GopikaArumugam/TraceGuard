"""
Customer Support Refund Agent
==============================
A real LangGraph + LiteLLM agent that:
  1. Calls `get_customer_account` tool  -> fetches customer account status from DB
  2. Calls `get_order_details` tool     -> fetches the order and amount from DB
  3. Calls `get_fraud_score` tool       -> fetches the fraud risk score from DB
  4. The LLM reasons over the results and makes a real APPROVED / DENIED decision.
"""
import os
import time
import json
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, START, END
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from litellm import completion
from app.config import settings
from app.db import SessionLocal
from app.models import CustomerAccount, OrderRecord


# ---------------------------------------------------------------------------
# 1.  Agent State
# ---------------------------------------------------------------------------
class RefundAgentState(TypedDict):
    customer_id: str
    order_id: str
    refund_amount: float
    messages: List[Dict[str, Any]]
    thoughts: List[str]
    final_decision: str


# ---------------------------------------------------------------------------
# 2.  Tools  (each one queries the real SQLite database)
# ---------------------------------------------------------------------------
@tool
def get_customer_account(customer_id: str) -> Dict[str, Any]:
    """Fetch the customer's account status, tier, and trust score from the CRM database."""
    db = SessionLocal()
    try:
        account = db.query(CustomerAccount).filter(
            CustomerAccount.customer_id == customer_id
        ).first()
        if not account:
            raise ValueError(
                f"Support Error: Customer '{customer_id}' not found in the CRM system."
            )
        return {
            "customer_id": account.customer_id,
            "full_name": account.full_name,
            "account_status": account.account_status,
            "account_tier": account.account_tier,
            "trust_score": account.trust_score,
            "prior_refund_count": account.prior_refund_count,
        }
    finally:
        db.close()


@tool
def get_order_details(order_id: str, customer_id: str) -> Dict[str, Any]:
    """Fetch order details: purchase date, delivery status, and total value from the orders database."""
    db = SessionLocal()
    try:
        order = db.query(OrderRecord).filter(
            OrderRecord.order_id == order_id,
            OrderRecord.customer_id == customer_id,
        ).first()
        if not order:
            raise ValueError(
                f"Support Error: Order '{order_id}' for customer '{customer_id}' not found in the orders database."
            )
        return {
            "order_id": order.order_id,
            "purchase_date": str(order.purchase_date),
            "delivery_status": order.delivery_status,
            "total_value": order.total_value,
            "payment_method": order.payment_method,
            "days_since_purchase": order.days_since_purchase,
        }
    finally:
        db.close()


@tool
def get_fraud_score(customer_id: str, order_id: str) -> Dict[str, Any]:
    """
    Fetch the fraud risk score for this customer-order combination.
    Score is 0-100. Scores above 70 are high risk and require manual review.
    """
    db = SessionLocal()
    try:
        order = db.query(OrderRecord).filter(
            OrderRecord.order_id == order_id,
            OrderRecord.customer_id == customer_id,
        ).first()
        if not order:
            return {"fraud_risk_score": 50, "flagged": True, "reason": "Order not found"}
        return {
            "fraud_risk_score": order.fraud_risk_score,
            "flagged": order.fraud_risk_score > 70,
            "reason": "High velocity refund pattern" if order.fraud_risk_score > 70 else "Low risk transaction",
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3.  Tool schemas for LiteLLM function calling
# ---------------------------------------------------------------------------
REFUND_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_customer_account",
            "description": "Fetch the customer account status, tier, and trust score.",
            "parameters": {
                "type": "object",
                "properties": {"customer_id": {"type": "string"}},
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_details",
            "description": "Fetch order details: purchase date, delivery status, and total value.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "customer_id": {"type": "string"},
                },
                "required": ["order_id", "customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fraud_score",
            "description": "Fetch the fraud risk score for a customer-order pair.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "order_id": {"type": "string"},
                },
                "required": ["customer_id", "order_id"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# 4.  Graph Nodes
# ---------------------------------------------------------------------------
def refund_brain_node(state: RefundAgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Central LLM reasoning node — calls Gemini to decide which tools to invoke."""
    messages = state["messages"].copy()

    configurable = config.get("configurable", {})
    max_refund_window_days = configurable.get("max_refund_window_days", 30)
    max_auto_refund_amount = configurable.get("max_auto_refund_amount", 500.0)
    max_fraud_score = configurable.get("max_fraud_score", 70)
    target_model = configurable.get("llm_model", "gemini/gemini-3.5-flash")

    system_msg = messages[0].copy()
    system_msg["content"] = (
        f"You are a professional Customer Support Refund Agent.\n"
        f"Your objective: evaluate a refund request of ${state['refund_amount']:.2f} "
        f"for Order ID '{state['order_id']}' from Customer ID '{state['customer_id']}'.\n\n"
        f"You must call these tools in order:\n"
        f"1. `get_customer_account` (requires customer_id) — verify the account is active.\n"
        f"2. `get_order_details` (requires order_id, customer_id) — verify the order and delivery.\n"
        f"3. `get_fraud_score` (requires customer_id, order_id) — check fraud risk.\n\n"
        f"Refund Approval Rules:\n"
        f"1. Account Status must be 'Active'. If not Active, DENY.\n"
        f"2. Order must belong to this customer. If order not found, DENY.\n"
        f"3. Purchase Window: days_since_purchase must be <= {max_refund_window_days} days. If older, DENY.\n"
        f"4. Refund Amount: The requested refund (${state['refund_amount']:.2f}) must not exceed the order's total_value. If it does, DENY.\n"
        f"5. Auto-approval Cap: Refund amounts above ${max_auto_refund_amount:.2f} require manual review — DENY with reason 'Exceeds automated approval limit'.\n"
        f"6. Fraud Risk: fraud_risk_score must be <= {max_fraud_score}. If score is higher, DENY.\n"
        f"7. If all rules pass, APPROVE the refund.\n\n"
        f"When you reach a final decision, write exactly:\n"
        f"   Thought: <your detailed reasoning covering all rules>\n"
        f"   Decision: <APPROVED or DENIED>\n"
    )
    messages[0] = system_msg

    # Build model pool
    model_pool = [{"model": target_model}]
    if settings.GEMINI_API_KEY:
        model_pool.append({"model": "gemini/gemini-3.5-flash"})
    if settings.GROQ_API_KEY:
        model_pool.append({"model": "groq/llama-3.1-8b-instant"})

    response_msg = None
    last_err = None

    for item in model_pool:
        model_name = item["model"]
        if "gemini" in model_name:
            api_key = settings.GEMINI_API_KEY
        elif "groq" in model_name:
            api_key = settings.GROQ_API_KEY
        else:
            api_key = settings.OPENAI_API_KEY
        if not api_key:
            continue

        for attempt in range(2):
            try:
                response = completion(
                    model=model_name,
                    messages=messages,
                    tools=REFUND_TOOL_SCHEMAS,
                    temperature=0.0,
                    api_key=api_key,
                )
                response_msg = response.choices[0].message
                break
            except Exception as e:
                last_err = e
                if "503" in str(e) or "overloaded" in str(e).lower():
                    time.sleep(2 ** attempt)
                else:
                    break
        if response_msg:
            break
    else:
        raise last_err or ValueError("No valid LLM API key available.")

    new_message = {
        "role": "assistant",
        "content": response_msg.content or "",
    }
    if response_msg.get("tool_calls"):
        new_message["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in response_msg.tool_calls
        ]

    messages.append(new_message)
    thoughts = state.get("thoughts", []).copy()
    final_decision = state.get("final_decision", "PENDING")

    content = response_msg.content or ""
    for line in content.split("\n"):
        if "Thought:" in line:
            thoughts.append(line.split("Thought:")[-1].strip())
        if "Decision:" in line:
            d = line.split("Decision:")[-1].strip().upper()
            if "APPROVED" in d:
                final_decision = "APPROVED"
            elif "DENIED" in d:
                final_decision = "DENIED"

    return {"messages": messages, "thoughts": thoughts, "final_decision": final_decision}


def refund_tools_node(state: RefundAgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Tool execution node — runs whichever tools the LLM requested."""
    messages = state["messages"].copy()
    last_msg = messages[-1]
    tool_calls = last_msg.get("tool_calls", [])

    tool_map = {
        "get_customer_account": get_customer_account,
        "get_order_details": get_order_details,
        "get_fraud_score": get_fraud_score,
    }

    for tc in tool_calls:
        func_name = tc["function"]["name"]
        args = json.loads(tc["function"]["arguments"])
        result = tool_map.get(func_name, lambda **_: {"error": f"Unknown tool: {func_name}"}).invoke(args)
        messages.append({
            "role": "tool",
            "tool_call_id": tc.get("id", "call_id"),
            "name": func_name,
            "content": json.dumps(result),
        })

    return {"messages": messages}


def refund_route_next(state: RefundAgentState) -> str:
    last_msg = state["messages"][-1]
    if last_msg.get("tool_calls"):
        return "refund_tools_node"
    return END


# ---------------------------------------------------------------------------
# 5.  Compile the graph
# ---------------------------------------------------------------------------
refund_workflow = StateGraph(RefundAgentState)
refund_workflow.add_node("refund_brain", refund_brain_node)
refund_workflow.add_node("refund_tools_node", refund_tools_node)
refund_workflow.add_edge(START, "refund_brain")
refund_workflow.add_conditional_edges(
    "refund_brain",
    refund_route_next,
    {"refund_tools_node": "refund_tools_node", END: END},
)
refund_workflow.add_edge("refund_tools_node", "refund_brain")

refund_agent_executor = refund_workflow.compile()
