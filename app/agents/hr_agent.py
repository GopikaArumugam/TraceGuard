"""
HR Leave Approval Agent
=======================
A real LangGraph + LiteLLM agent that:
  1. Calls `get_employee_record` tool  -> fetches employee status from DB
  2. Calls `get_leave_balance` tool    -> fetches remaining leave days from DB
  3. Calls `check_team_calendar` tool  -> fetches overlapping leaves from DB
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
from app.models import EmployeeRecord, LeaveRecord


# ---------------------------------------------------------------------------
# 1.  Agent State
# ---------------------------------------------------------------------------
class HRAgentState(TypedDict):
    employee_id: str
    leave_type: str
    leave_days: int
    messages: List[Dict[str, Any]]
    thoughts: List[str]
    final_decision: str


# ---------------------------------------------------------------------------
# 2.  Tools  (each one queries the real SQLite database)
# ---------------------------------------------------------------------------
@tool
def get_employee_record(employee_id: str) -> Dict[str, Any]:
    """Fetch the employee's HR record: name, department, and active employment status."""
    db = SessionLocal()
    try:
        record = db.query(EmployeeRecord).filter(
            EmployeeRecord.employee_id == employee_id
        ).first()
        if not record:
            raise ValueError(
                f"HR Error: Employee '{employee_id}' not found in the HR system."
            )
        return {
            "employee_id": record.employee_id,
            "full_name": record.full_name,
            "department": record.department,
            "employment_status": record.employment_status,
            "joining_date": str(record.joining_date),
        }
    finally:
        db.close()


@tool
def get_leave_balance(employee_id: str, leave_type: str) -> Dict[str, Any]:
    """Fetch the employee's current leave balance for the requested leave type."""
    db = SessionLocal()
    try:
        record = db.query(LeaveRecord).filter(
            LeaveRecord.employee_id == employee_id,
            LeaveRecord.leave_type == leave_type,
        ).first()
        if not record:
            # Default: no leave of this type configured
            return {
                "leave_type": leave_type,
                "annual_allocated": 0,
                "days_taken": 0,
                "current_balance": 0,
            }
        return {
            "leave_type": record.leave_type,
            "annual_allocated": record.annual_allocated,
            "days_taken": record.days_taken,
            "current_balance": record.annual_allocated - record.days_taken,
        }
    finally:
        db.close()


@tool
def check_team_calendar(department: str) -> Dict[str, Any]:
    """
    Check how many team members in the department currently have approved leave.
    Returns coverage ratio so the agent can verify minimum staffing policy.
    """
    db = SessionLocal()
    try:
        total_in_dept = db.query(EmployeeRecord).filter(
            EmployeeRecord.department == department,
            EmployeeRecord.employment_status == "Active",
        ).count()

        # Count employees already on leave in the same department
        on_leave = db.query(LeaveRecord).join(
            EmployeeRecord, LeaveRecord.employee_id == EmployeeRecord.employee_id
        ).filter(
            EmployeeRecord.department == department,
            LeaveRecord.currently_on_leave == True,
        ).count()

        coverage = round((total_in_dept - on_leave) / max(total_in_dept, 1), 3)
        return {
            "department": department,
            "total_active_members": total_in_dept,
            "currently_on_leave": on_leave,
            "available_coverage_ratio": coverage,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3.  Tool schemas for LiteLLM function calling
# ---------------------------------------------------------------------------
HR_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_employee_record",
            "description": "Fetch the employee's HR record: name, department, and employment status.",
            "parameters": {
                "type": "object",
                "properties": {"employee_id": {"type": "string"}},
                "required": ["employee_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_leave_balance",
            "description": "Fetch the employee's current leave balance for a given leave type.",
            "parameters": {
                "type": "object",
                "properties": {
                    "employee_id": {"type": "string"},
                    "leave_type": {"type": "string"},
                },
                "required": ["employee_id", "leave_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_team_calendar",
            "description": "Check the department's current leave coverage ratio.",
            "parameters": {
                "type": "object",
                "properties": {"department": {"type": "string"}},
                "required": ["department"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# 4.  Graph Nodes
# ---------------------------------------------------------------------------
def hr_brain_node(state: HRAgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Central LLM reasoning node — calls Gemini to decide which tools to invoke."""
    messages = state["messages"].copy()

    configurable = config.get("configurable", {})
    min_coverage = configurable.get("min_coverage_ratio", 0.60)
    target_model = configurable.get("llm_model", "gemini/gemini-3.5-flash")

    system_msg = messages[0].copy()
    system_msg["content"] = (
        f"You are a professional HR Leave Approval Agent.\n"
        f"Your objective: evaluate a leave request for Employee ID '{state['employee_id']}' "
        f"who is requesting {state['leave_days']} days of {state['leave_type']} leave.\n\n"
        f"You must call these tools in order:\n"
        f"1. `get_employee_record` (requires employee_id) — verify the employee exists and is Active.\n"
        f"2. `get_leave_balance` (requires employee_id, leave_type) — check available balance.\n"
        f"3. `check_team_calendar` (requires department from step 1) — check team coverage.\n\n"
        f"Approval Rules:\n"
        f"1. Employment Status must be 'Active'. If not Active, DENY immediately.\n"
        f"2. Leave Balance: current_balance must be >= requested days ({state['leave_days']}). If insufficient, DENY.\n"
        f"3. Team Coverage: available_coverage_ratio after granting leave must be >= {min_coverage:.0%}. "
        f"   (Estimate: subtract 1 person from available, recalculate ratio. If it drops below {min_coverage:.0%}, DENY.)\n"
        f"4. If all 3 rules pass, APPROVE the leave.\n\n"
        f"When you reach a final decision, write exactly:\n"
        f"   Thought: <your detailed reasoning covering all 3 rules>\n"
        f"   Decision: <APPROVED or DENIED>\n"
    )
    messages[0] = system_msg

    # Build model pool
    env_model = os.getenv("GEMINI_MODEL", "gemini/gemini-1.5-flash")
    candidate_models = []
    if env_model:
        candidate_models.append(env_model)
    if target_model and target_model not in candidate_models:
        candidate_models.append(target_model)
    for fallback in ["gemini/gemini-1.5-flash", "gemini/gemini-2.0-flash", "gemini/gemini-3.5-flash"]:
        if fallback not in candidate_models:
            candidate_models.append(fallback)
    if settings.GROQ_API_KEY:
        candidate_models.append("groq/llama-3.1-8b-instant")

    model_pool = [{"model": m} for m in candidate_models]

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

        if "gemini" in model_name and api_key:
            os.environ["GEMINI_API_KEY"] = api_key

        for attempt in range(2):
            try:
                response = completion(
                    model=model_name,
                    messages=messages,
                    tools=HR_TOOL_SCHEMAS,
                    temperature=0.0,
                    api_key=api_key,
                )
                response_msg = response.choices[0].message
                break
            except Exception as e:
                last_err = e
                print(f"[HR Agent] Model {model_name} failed: {e}. Falling back...")
                if attempt == 1:
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


def hr_tools_node(state: HRAgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Tool execution node — runs whichever tools the LLM requested."""
    messages = state["messages"].copy()
    last_msg = messages[-1]
    tool_calls = last_msg.get("tool_calls", [])

    tool_map = {
        "get_employee_record": get_employee_record,
        "get_leave_balance": get_leave_balance,
        "check_team_calendar": check_team_calendar,
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


def hr_route_next(state: HRAgentState) -> str:
    last_msg = state["messages"][-1]
    if last_msg.get("tool_calls"):
        return "hr_tools_node"
    return END


# ---------------------------------------------------------------------------
# 5.  Compile the graph
# ---------------------------------------------------------------------------
hr_workflow = StateGraph(HRAgentState)
hr_workflow.add_node("hr_brain", hr_brain_node)
hr_workflow.add_node("hr_tools_node", hr_tools_node)
hr_workflow.add_edge(START, "hr_brain")
hr_workflow.add_conditional_edges(
    "hr_brain",
    hr_route_next,
    {"hr_tools_node": "hr_tools_node", END: END},
)
hr_workflow.add_edge("hr_tools_node", "hr_brain")

hr_agent_executor = hr_workflow.compile()
