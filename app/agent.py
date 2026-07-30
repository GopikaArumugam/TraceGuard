import os
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, START, END
from litellm import completion
from app.config import settings

# 1. Define the Agent State
class AgentState(TypedDict):
    user_id: str
    email: str
    phone: str
    requested_amount: float
    credit_score: int          # Filled by fetch_credit_score node
    active_debts: float        # Filled by check_debts node
    thoughts: List[str]        # Accumulated LLM thoughts/reasoning
    final_decision: str        # APPROVED / DENIED / PENDING

# 2. Define Mock Tools (Agent physical actions)
def get_credit_profile(user_id: str, email: str) -> Dict[str, Any]:
    """Mock tool to fetch credit bureau report.
    Contains simulated PII data (name, email) and a credit score.
    """
    score_map = {
        "usr_low": 500,   # Will cause Denial
        "usr_mid": 600,   # Borderline, depends on debts
        "usr_high": 750,  # Easy Approval
    }
    score = score_map.get(user_id, 650) # Default to medium score
    
    return {
        "full_name": "Alice Smith",
        "email_address": email,
        "credit_score": score,
        "report_date": "2026-07-30"
    }

def get_active_debts(user_id: str) -> Dict[str, Any]:
    """Mock tool to fetch active banking loan/debt profile.
    """
    debt_map = {
        "usr_low": 8000.0,
        "usr_mid": 5000.0,
        "usr_high": 1000.0,
    }
    debts = debt_map.get(user_id, 2000.0)
    
    return {
        "active_credit_lines": 2,
        "debts_total": debts,
        "missed_payments_last_12m": 0
    }

# 3. Define Graph Nodes (Flow execution steps)

def fetch_credit_score_node(state: AgentState) -> Dict[str, Any]:
    """Node 1: Fetch credit score from the bureau tool.
    """
    profile = get_credit_profile(state["user_id"], state["email"])
    return {
        "credit_score": profile["credit_score"]
    }

def check_debts_node(state: AgentState) -> Dict[str, Any]:
    """Node 2: Fetch debt balance from the bank api tool.
    """
    debts_profile = get_active_debts(state["user_id"])
    return {
        "active_debts": debts_profile["debts_total"]
    }

def make_decision_node(state: AgentState) -> Dict[str, Any]:
    """Node 3: Analyze information and make a final decision using LLM reasoning.
    Strictly dependent on the LLM. If keys are missing, the system will raise an error.
    """
    credit = state["credit_score"]
    debts = state["active_debts"]
    amount = state["requested_amount"]
    
    prompt = f"""
    You are a professional bank loan underwriter.
    Analyze the loan application with the following criteria:
    - Requested Amount: ${amount}
    - Credit Score: {credit}
    - Total Outstanding Debts: ${debts}
    
    Rules for loan approvals:
    - If Credit Score is below 550, DENY.
    - If Credit Score is 700 or above, APPROVE.
    - If Credit Score is between 550 and 700, APPROVE only if Total Outstanding Debts are less than $3,000. Otherwise, DENY.
    
    First, write a single thought sentence explaining your analysis logic.
    Second, write your final decision status (must be exactly 'APPROVED' or 'DENIED').
    
    Format your response EXACTLY like this:
    Thought: <Your reasoning sentence here>
    Decision: <APPROVED or DENIED>
    """
    
    # Model routing logic based on which API key is configured
    model_name = None
    api_key = None
    
    # 1. Prefer Gemini if a key is supplied (free tier)
    if settings.GEMINI_API_KEY:
        model_name = "gemini/gemini-1.5-flash"
        api_key = settings.GEMINI_API_KEY
    # 2. Fall back to OpenAI if a real key is present
    elif settings.OPENAI_API_KEY and settings.OPENAI_API_KEY != "mock-key":
        model_name = "openai/gpt-4o-mini"
        api_key = settings.OPENAI_API_KEY
        
    if not model_name:
        raise ValueError("Strict Mode Active: No active LLM API keys configured. Set GEMINI_API_KEY or OPENAI_API_KEY.")
        
    # Call completion using LiteLLM (automatically reads the correct API key)
    response = completion(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        api_key=api_key
    )
    content = response.choices[0].message.content.strip()
    
    thoughts = "Underwriting evaluation complete."
    decision = "DENIED"
    
    # Parse output structure
    for line in content.split("\n"):
        if line.startswith("Thought:"):
            thoughts = line.replace("Thought:", "").strip()
        elif line.startswith("Decision:"):
            decision = line.replace("Decision:", "").strip()
            
    return {
        "thoughts": [thoughts],
        "final_decision": decision
    }

# 4. Define and Compile the State Graph
workflow = StateGraph(AgentState)

# Add our processing nodes
workflow.add_node("fetch_credit_score", fetch_credit_score_node)
workflow.add_node("check_debts", check_debts_node)
workflow.add_node("make_decision", make_decision_node)

# Set up flow connections
workflow.add_edge(START, "fetch_credit_score")
workflow.add_edge("fetch_credit_score", "check_debts")
workflow.add_edge("check_debts", "make_decision")
workflow.add_edge("make_decision", END)

# Compile into a runnable agent
agent_executor = workflow.compile()
