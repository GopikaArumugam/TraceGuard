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
from app.models import ApplicantProfile

# 1. Define the Agent State
class AgentState(TypedDict):
    user_id: str
    email: str
    phone: str
    requested_amount: float
    messages: List[Dict[str, Any]]
    thoughts: List[str]
    final_decision: str

# 2. Define Tools using the LangChain @tool decorator
# These tools strictly query the database. If the record is missing, they throw an exception.
@tool
def get_credit_profile(user_id: str, email: str) -> Dict[str, Any]:
    """Fetch credit bureau report. Contains credit score and report details.
    """
    db = SessionLocal()
    try:
        profile = db.query(ApplicantProfile).filter(ApplicantProfile.user_id == user_id).first()
        if not profile:
            raise ValueError(f"Underwriting Error: Applicant profile for '{user_id}' not found in the credit database.")
        
        return {
            "full_name": "Alice Smith",
            "email_address": email,
            "credit_score": profile.credit_score,
            "report_date": "2026-07-30"
        }
    finally:
        db.close()

@tool
def get_active_debts(user_id: str) -> Dict[str, Any]:
    """Fetch active banking outstanding loan and monthly debt profile.
    """
    db = SessionLocal()
    try:
        profile = db.query(ApplicantProfile).filter(ApplicantProfile.user_id == user_id).first()
        if not profile:
            raise ValueError(f"Underwriting Error: Debt record for user '{user_id}' not found in the banking ledger database.")
        
        return {
            "active_credit_lines": 3,
            "debts_total": profile.debts_total,
            "missed_payments_last_12m": profile.missed_payments_last_12m
        }
    finally:
        db.close()

@tool
def get_income_profile(user_id: str) -> Dict[str, Any]:
    """Fetch verified monthly gross income, employment status, and job length.
    """
    db = SessionLocal()
    try:
        profile = db.query(ApplicantProfile).filter(ApplicantProfile.user_id == user_id).first()
        if not profile:
            raise ValueError(f"Underwriting Error: Employment record for user '{user_id}' not found in the verified income database.")
        
        return {
            "employer_name": "Acme Global Corp",
            "employment_status": profile.employment_status,
            "monthly_gross_income": profile.monthly_gross_income,
            "length_of_employment_years": profile.length_of_employment_years
        }
    finally:
        db.close()

# Tool schemas definition for LiteLLM tool calling
TOOLS_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_credit_profile",
            "description": "Fetch credit bureau report. Contains credit score and report details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "email": {"type": "string"}
                },
                "required": ["user_id", "email"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_debts",
            "description": "Fetch active banking outstanding loan and monthly debt profile.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"}
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_income_profile",
            "description": "Fetch verified monthly gross income, employment status, and job length.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"}
                },
                "required": ["user_id"]
            }
        }
    }
]

# 3. Define Graph Nodes

def agent_brain_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Node 1: The central LLM brain.
    """
    messages = state["messages"].copy()
    
    # Read dynamic policy overrides from config
    configurable = config.get("configurable", {})
    min_score = configurable.get("credit_score_threshold", 580)
    max_dti = configurable.get("max_dti_ratio", 0.45)
    min_job_years = configurable.get("min_employment_years", 1.0)
    target_model = configurable.get("llm_model", "gemini/gemini-3.5-flash")
    
    system_msg = messages[0].copy()
    system_msg["content"] = (
        f"You are a professional bank loan underwriter agent.\n"
        f"Your objective is to evaluate a loan request for User ID '{state['user_id']}' requesting ${state['requested_amount']}.\n\n"
        f"You must use the following tools to fetch the necessary information:\n"
        f"1. `get_credit_profile` (requires user_id, email)\n"
        f"2. `get_active_debts` (requires user_id)\n"
        f"3. `get_income_profile` (requires user_id)\n\n"
        f"Underwriting & Affordability Rules (5 Criteria to Consider):\n"
        f"1. Credit Score: If Credit Score is below {min_score}, DENY immediately (subprime borrower). Stop execution.\n"
        f"2. Employment Status: Must be exactly 'Employed'. If 'Unemployed', DENY immediately. Stop execution.\n"
        f"3. Length of Employment: Must be at least {min_job_years} years. If less than {min_job_years} years, DENY immediately. Stop. \n"
        f"4. Payment History: Must have 0 missed payments in the last 12 months. If missed payments > 0, DENY. Stop.\n"
        f"5. Debt-to-Income (DTI) Ratio: Must be {int(max_dti*100)}% or lower. Calculate DTI as follows:\n"
        f"   a. Proposed monthly payment = Requested Amount / 60 months (5-year term).\n"
        f"   b. Monthly debt obligations = (Outstanding Debts * 0.05) + Proposed monthly payment.\n"
        f"   c. DTI Ratio = Monthly debt obligations / Monthly gross income.\n"
        f"   d. If DTI Ratio is greater than {int(max_dti*100)}%, DENY. Otherwise, APPROVE.\n\n"
        f"Execution Instructions:\n"
        f"1. First, call `get_credit_profile` to check the credit score.\n"
        f"2. If score is >= {min_score}, call both `get_active_debts` and `get_income_profile` to retrieve the remaining 4 features.\n"
        f"3. Evaluate all 5 criteria. If any criteria fails, DENY the loan and explain exactly which features failed.\n"
        f"4. If all 5 criteria pass, APPROVE the loan.\n"
        f"5. When you have made a final decision, write:\n"
        f"   Thought: <Your detailed reasoning explaining the 5 features, DTI calculations, and the specific failure reason if denied>\n"
        f"   Decision: <APPROVED or DENIED>\n"
    )
    
    messages[0] = system_msg
    
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

    model_pool = [{"model": m, "api_key": None} for m in candidate_models]
        
    for item in model_pool:
        if "gemini" in item["model"]:
            item["api_key"] = settings.GEMINI_API_KEY
        elif "groq" in item["model"]:
            item["api_key"] = settings.GROQ_API_KEY
        elif "openai" in item["model"]:
            item["api_key"] = settings.OPENAI_API_KEY
            
    response_msg = None
    last_err = None
    
    for item in model_pool:
        model_name = item["model"]
        api_key = item["api_key"]
        if not api_key:
            continue
            
        max_retries = 2
        for attempt in range(2):
            try:
                response = completion(
                    model=model_name,
                    messages=messages,
                    tools=TOOLS_SCHEMAS,
                    temperature=0.0,
                    api_key=api_key
                )
                response_msg = response.choices[0].message
                break
            except Exception as e:
                last_err = e
                print(f"[Loan Agent] Model {model_name} failed: {e}. Falling back...")
                if attempt == 1:
                    break
        if response_msg:
            break
    else:
        raise last_err or ValueError("No valid LLM key matching requested models.")

    new_message = {
        "role": "assistant",
        "content": response_msg.content or ""
    }
    
    if response_msg.get("tool_calls"):
        new_message["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments
                }
            } for tc in response_msg.tool_calls
        ]
        
    messages.append(new_message)
    
    thoughts = state.get("thoughts", []).copy()
    final_decision = state.get("final_decision", "PENDING")
    
    content = response_msg.content or ""
    for line in content.split("\n"):
        if "Thought:" in line:
            t = line.split("Thought:")[-1].strip()
            thoughts.append(t)
        if "Decision:" in line:
            d = line.split("Decision:")[-1].strip()
            if "APPROVED" in d.upper():
                final_decision = "APPROVED"
            elif "DENIED" in d.upper():
                final_decision = "DENIED"
                
    return {
        "messages": messages,
        "thoughts": thoughts,
        "final_decision": final_decision
    }


def tools_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Node 2: Tool Execution block.
    """
    messages = state["messages"].copy()
    last_msg = messages[-1]
    
    tool_calls = last_msg.get("tool_calls", [])
    for tc in tool_calls:
        func_name = tc["function"]["name"]
        args = json.loads(tc["function"]["arguments"])
        
        if func_name == "get_credit_profile":
            result = get_credit_profile.invoke(args, config=config)
        elif func_name == "get_active_debts":
            result = get_active_debts.invoke(args, config=config)
        elif func_name == "get_income_profile":
            result = get_income_profile.invoke(args, config=config)
        else:
            result = {"error": f"Unknown tool: {func_name}"}
            
        messages.append({
            "role": "tool",
            "tool_call_id": tc.get("id", "call_id"),
            "name": func_name,
            "content": json.dumps(result)
        })
        
    return {"messages": messages}


def route_next(state: AgentState) -> str:
    last_msg = state["messages"][-1]
    if last_msg.get("tool_calls"):
        return "tools_node"
    return END


# 5. Compile the State Graph
workflow = StateGraph(AgentState)

workflow.add_node("agent_brain", agent_brain_node)
workflow.add_node("tools_node", tools_node)

workflow.add_edge(START, "agent_brain")
workflow.add_conditional_edges(
    "agent_brain",
    route_next,
    {
        "tools_node": "tools_node",
        END: END
    }
)
workflow.add_edge("tools_node", "agent_brain")

agent_executor = workflow.compile()
