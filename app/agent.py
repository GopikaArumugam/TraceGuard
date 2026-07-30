import os
import time
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, START, END
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
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

# 2. Define Mock Tools using the LangChain @tool decorator
@tool
def get_credit_profile(user_id: str, email: str) -> Dict[str, Any]:
    """Fetch credit bureau report. Contains simulated PII data and a credit score.
    """
    score_map = {
        "usr_low": 500,   # Will cause Denial
        "usr_mid": 600,   # Borderline, depends on debts
        "usr_high": 750,  # Easy Approval
    }
    score = score_map.get(user_id, 650)
    
    return {
        "full_name": "Alice Smith",
        "email_address": email,
        "credit_score": score,
        "report_date": "2026-07-30"
    }

@tool
def get_active_debts(user_id: str) -> Dict[str, Any]:
    """Fetch active banking loan/debt profile.
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

# 3. Define Graph Nodes

def fetch_credit_score_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Node 1: Fetch credit score using config callbacks context.
    """
    profile = get_credit_profile.invoke(
        {"user_id": state["user_id"], "email": state["email"]},
        config=config
    )
    return {
        "credit_score": profile["credit_score"]
    }

def check_debts_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Node 2: Fetch debt balance from the bank api tool.
    """
    debts_profile = get_active_debts.invoke(
        {"user_id": state["user_id"]},
        config=config
    )
    return {
        "active_debts": debts_profile["debts_total"]
    }

def make_decision_node(state: AgentState) -> Dict[str, Any]:
    """Node 3: Analyze information and make a decision using LLM reasoning.
    Implements a multi-provider failover pool: tries Gemini first, and automatically
    swaps to Groq (Llama 3.1) if Gemini is rate-limited or busy.
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
    
    # Compile candidate list of models based on active keys
    model_pool = []
    
    # Priority 1: Gemini (User's primary selection)
    if settings.GEMINI_API_KEY:
        model_pool.append({
            "model": "gemini/gemini-3.5-flash",
            "api_key": settings.GEMINI_API_KEY
        })
    
    # Priority 2: Groq / Llama (Free high-availability backup)
    if settings.GROQ_API_KEY:
        model_pool.append({
            "model": "groq/llama-3.1-8b-instant",
            "api_key": settings.GROQ_API_KEY
        })
        
    # Priority 3: OpenAI (Paid option)
    if settings.OPENAI_API_KEY and settings.OPENAI_API_KEY != "mock-key":
        model_pool.append({
            "model": "openai/gpt-4o-mini",
            "api_key": settings.OPENAI_API_KEY
        })
        
    if not model_pool:
        raise ValueError("Strict Mode Active: No active LLM API keys configured. Please add GEMINI_API_KEY or GROQ_API_KEY to your .env file.")
        
    content = ""
    last_err = None
    
    # Execute routing through the pool
    for item in model_pool:
        model_name = item["model"]
        api_key = item["api_key"]
        
        # Retry logic per provider for transient errors
        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = completion(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    api_key=api_key
                )
                content = response.choices[0].message.content.strip()
                break
            except Exception as e:
                last_err = e
                # If a 503 capacity limit is reached, wait and retry
                if "503" in str(e) or "overloaded" in str(e).lower():
                    wait_time = 2 ** attempt
                    print(f"Warning: Model {model_name} is busy. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    # For other errors, skip directly to failover model
                    break
        
        # If we got a successful response from this provider, stop the failover loop
        if content:
            break
    else:
        # If all providers in the pool failed
        raise last_err
    
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

workflow.add_node("fetch_credit_score", fetch_credit_score_node)
workflow.add_node("check_debts", check_debts_node)
workflow.add_node("make_decision", make_decision_node)

workflow.add_edge(START, "fetch_credit_score")
workflow.add_edge("fetch_credit_score", "check_debts")
workflow.add_edge("check_debts", "make_decision")
workflow.add_edge("make_decision", END)

agent_executor = workflow.compile()
