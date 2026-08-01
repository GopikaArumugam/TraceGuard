import os
import time
from sqlalchemy.orm import Session
from uuid import UUID
from litellm import completion
from app.config import settings
from app.models import DecisionStep, AgentSession

def generate_decision_summary(session_id: UUID, db: Session) -> str:
    """Retrieves redacted audit steps and generates a domain-specific plain-English explanation.
    Implements a multi-provider failover pool: tries Gemini first, and automatically
    swaps to Groq (Llama 3.1) if Gemini is rate-limited or busy.
    """
    # 1. Fetch session metadata
    session = db.query(AgentSession).filter(AgentSession.session_id == session_id).first()
    if not session:
        return "Session not found."
        
    # 2. Fetch all steps ordered chronologically
    steps = db.query(DecisionStep).filter(DecisionStep.session_id == session_id).order_by(DecisionStep.created_at.asc()).all()

    if not steps:
        return f"No audit steps recorded for this session. Decision: {session.decision_status}."

    # 3. Format the timeline of steps into a technical history string
    timeline_text = []
    for idx, step in enumerate(steps, start=1):
        step_info = f"Step {idx} ({step.step_type}): Name: '{step.name}'"
        if step.input_payload and any(step.input_payload.values()):
            step_info += f", Inputs: {step.input_payload}"
        if step.output_payload and any(step.output_payload.values()):
            step_info += f", Outputs: {step.output_payload}"
        timeline_text.append(step_info)

    history_log = "\n".join(timeline_text)

    # 4. Detect Agent Type dynamically from tool names
    agent_type = "loan"
    for step in steps:
        if "employee" in step.name or "leave" in step.name or "team" in step.name:
            agent_type = "hr"
            break
        if "refund" in step.name or "order" in step.name or "payment" in step.name or "fraud" in step.name:
            agent_type = "refund"
            break

    # 5. Select domain-specific prompt
    if agent_type == "hr":
        prompt = f"""
        You are an automated corporate Human Resources compliance officer.
        Your task is to translate a technical sequence of employee records, leave balance queries, and department overlapping availability checks into a clear, professional, plain-English explanation of why an employee's time-off/leave request was APPROVED or DENIED.
        
        Guidelines:
        - Write a single, polite, professional paragraph.
        - Explain the decision directly based on employee details, available balances, and department coverage ratios.
        - NEVER use technical jargon such as "JSON", "payload", "tool", "API", "callback", "SQL", or "node".
        - Explain it like an HR representative notifying an employee about their vacation request.
        
        Technical Audit Log to translate:
        Employee ID: {session.user_id}
        Leave Request Status: {session.decision_status}
        Requested Leave Days: {session.requested_amount}
        
        Timeline Steps:
        {history_log}
        
        Plain-English Explanation:
        """
    elif agent_type == "refund":
        prompt = f"""
        You are an automated customer support compliance and payment auditor.
        Your task is to translate a technical sequence of order transaction fetches, credit card clearance checks, and fraud index calculations into a clear, professional, plain-English explanation of why a customer's refund request was APPROVED or DENIED.
        
        Guidelines:
        - Write a single, helpful, professional paragraph.
        - Explain the decision directly based on order date, payment settlement, fraud score, and 30-day return policy.
        - NEVER use technical jargon such as "JSON", "payload", "tool", "API", "callback", "SQL", or "node".
        - Explain it like a consumer assistance representative addressing a customer.
        
        Technical Audit Log to translate:
        Customer ID: {session.user_id}
        Refund Request Status: {session.decision_status}
        Refund Claim Value: ${session.requested_amount}
        
        Timeline Steps:
        {history_log}
        
        Plain-English Explanation:
        """
    else:
        prompt = f"""
        You are an automated bank underwriting compliance auditor.
        Your task is to translate a technical sequence of database and tool audit logs into a clear, professional, plain-English explanation for why a loan request was APPROVED or DENIED.
        
        Guidelines:
        - Write a single, polite, professional paragraph.
        - Explain the decision directly based on the data retrieved (credit score, debts, amount).
        - NEVER use technical jargon such as "JSON", "payload", "tool", "API", "callback", "SQL", or "node".
        - Explain it like a human bank counselor talking to a customer.
        
        Technical Audit Log to translate:
        Requested Loan Amount: ${session.requested_amount}
        Final Decision Status: {session.decision_status}
        
        Timeline Steps:
        {history_log}
        
        Plain-English Explanation:
        """

    # 6. Model pool routing logic
    model_pool = []
    if settings.GEMINI_API_KEY:
        env_model = os.getenv("GEMINI_MODEL", "gemini/gemini-1.5-flash")
        candidate_models = []
        if env_model:
            candidate_models.append(env_model)
        for fb in ["gemini/gemini-1.5-flash", "gemini/gemini-2.0-flash", "gemini/gemini-3.5-flash"]:
            if fb not in candidate_models:
                candidate_models.append(fb)
        for m in candidate_models:
            model_pool.append({"model": m, "api_key": settings.GEMINI_API_KEY})
    if settings.GROQ_API_KEY:
        model_pool.append({"model": "groq/llama-3.1-8b-instant", "api_key": settings.GROQ_API_KEY})
    if settings.OPENAI_API_KEY and settings.OPENAI_API_KEY != "mock-key":
        model_pool.append({"model": "openai/gpt-4o-mini", "api_key": settings.OPENAI_API_KEY})

    if not model_pool:
        raise ValueError("Strict Mode Active: No active LLM API keys configured.")

    explanation = ""
    last_err = None

    for item in model_pool:
        model_name = item["model"]
        api_key = item["api_key"]
        
        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = completion(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    api_key=api_key
                )
                explanation = response.choices[0].message.content.strip()
                break
            except Exception as e:
                last_err = e
                if "503" in str(e) or "overloaded" in str(e).lower():
                    wait_time = 2 ** attempt
                    print(f"Warning: Summarizer model {model_name} failed ({str(e)}). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    break
        
        if explanation:
            break
    else:
        raise last_err
        
    return explanation

def generate_challenge_response(session_id: UUID, db: Session) -> str:
    """Retrieves redacted audit steps and generates a formal, legally structured 
    regulatory compliance defense letter tailored dynamically to the target agent category.
    """
    session = db.query(AgentSession).filter(AgentSession.session_id == session_id).first()
    if not session:
        return "Session not found."
        
    steps = db.query(DecisionStep).filter(DecisionStep.session_id == session_id).order_by(DecisionStep.created_at.asc()).all()
    if not steps:
        return f"No audit steps recorded to verify. Decision: {session.decision_status}."

    timeline_text = []
    for idx, step in enumerate(steps, start=1):
        step_info = f"Step {idx} ({step.step_type}): Name: '{step.name}'"
        if step.input_payload and any(step.input_payload.values()):
            step_info += f", Inputs: {step.input_payload}"
        if step.output_payload and any(step.output_payload.values()):
            step_info += f", Outputs: {step.output_payload}"
        timeline_text.append(step_info)

    history_log = "\n".join(timeline_text)

    # Detect Agent Type dynamically
    agent_type = "loan"
    for step in steps:
        if "employee" in step.name or "leave" in step.name or "team" in step.name:
            agent_type = "hr"
            break
        if "refund" in step.name or "order" in step.name or "payment" in step.name or "fraud" in step.name:
            agent_type = "refund"
            break

    # Select prompt tailored to domain regulatory agency
    if agent_type == "hr":
        prompt = f"""
        You are a corporate Human Resources Legal and Labor Compliance Director.
        Your task is to draft a formal internal compliance audit defense letter justifying a specific automated HR leave approval decision.
        
        Format your response EXACTLY as a formal compliance letter:
        - Header: Formal Letterhead from "Corporate HR Governance & Employment Compliance Auditing Division"
        - Subject: HR DECISION AUDIT RESPONSE - Time-Off Policy Review for Session ID: {session_id}
        - Addressed to: Internal Human Resources Compliance & Auditing Board (or Department of Labor Oversight Board)
        - Include the following distinct sections:
          1. EXECUTIVE SUMMARY: State the employee identifier, leave type, requested days, approval outcome, and timestamp.
          2. CORPORATE LEAVE POLICY CONTEXT: Outline the factors evaluated (Employment status check, leave balance verification, team overlap availability limits).
          3. COMPLIANCE EMPIRICAL FINDINGS: Cite the exact numbers and parameters recorded in the HR timeline logs.
          4. STRATIFIED GOVERNANCE ANALYSIS: Explain step-by-step how the data was checked against corporate threshold guidelines (balance check, team coverage ratio remains above 75%), proving absolute compliance with Labor Standards.
          5. RECORD CERTIFICATION: Certify that all logs are PII-redacted, immutable, and saved securely to the auditing registry.
        - Sign-off: "Respectfully submitted, Director of Employee Relations, Corporate Governance Auditing Division"
        
        Guidelines:
        - Maintain a highly professional, legal, authoritative, and policy-compliant tone.
        - Citing exact values (employee ID, leave days, department availability) from the history log is mandatory.
        
        Technical Audit History:
        Requested Leave Days: {session.requested_amount}
        Decision Status: {session.decision_status}
        
        Timeline Logs:
        {history_log}
        
        Draft Regulatory Defense Letter:
        """
    elif agent_type == "refund":
        prompt = f"""
        You are a Merchant Services Risk and Consumer Financial Protection Compliance Director.
        Your task is to draft a formal compliance defense letter to a consumer transaction regulatory authority (like the FTC or FINRA Merchant Board) justifying a specific automated customer support refund approval decision.
        
        Format your response EXACTLY as a formal compliance letter:
        - Header: Formal Letterhead from "Consumer Support Risk and Transaction Compliance Auditing Division"
        - Subject: REFUND AUDIT RESPONSE - FTC Consumer Protection Division for Session ID: {session_id}
        - Addressed to: Merchant Services Compliance Audit & Federal Trade Commission (FTC) Division of Consumer Protection
        - Include the following distinct sections:
          1. EXECUTIVE SUMMARY: State the customer ID, order ID, refund purchase size ($), transaction decision, and timestamp.
          2. MERCHANT REFUND POLICY CONTEXT: Outline the policy requirements checked (30-day window check, transaction settlement, fraud risk threshold checks).
          3. TRANSACTION EMPIRICAL FINDINGS: Cite the exact details and fraud scores recorded in the support timeline logs.
          4. STRATIFIED COMPLIANCE ANALYSIS: Explain step-by-step how the transaction checked against safety thresholds (fraud score below risk limit, order within 30 days), proving full compliance with FTC consumer trust guidelines.
          5. RECORD CERTIFICATION: Certify that all logs are PII-redacted, immutable, and locked in the audit registry.
        - Sign-off: "Respectfully submitted, Vice President of Risk Mitigation, Merchant Financial Compliance Division"
        
        Guidelines:
        - Maintain a highly professional, risk-aware, legally sound, and compliance-driven tone.
        - Citing exact values (order ID, refund amount, fraud score) from the audit log is mandatory.
        
        Technical Audit History:
        Requested Refund Amount: ${session.requested_amount}
        Decision Status: {session.decision_status}
        
        Timeline Logs:
        {history_log}
        
        Draft Regulatory Defense Letter:
        """
    else:
        prompt = f"""
        You are a bank underwriting compliance defense counsel.
        Your task is to draft a formal, legally structured, and detailed response to a regulatory inquiry (such as from a banking regulator or fair lending auditor) regarding a specific loan underwriting decision.
        
        Format your response EXACTLY as a formal compliance letter:
        - Header: Formal Letterhead from "Underwriting Compliance Division, Decision Path Auditor System"
        - Subject: FORMAL COMPLIANCE RESPONSE - Regulatory Review for Session ID: {session_id}
        - Addressed to: Office of Fair Lending and Compliance Oversight
        - Include the following distinct sections:
          1. EXECUTIVE SUMMARY: State the applicant user ID, requested amount, final decision, and timestamp.
          2. RISK POLICY CONTEXT: Outline the 5 specific risk factors evaluated (Credit Score, Employment Status, Job Length, Missed Payments, DTI).
          3. EMPIRICAL FINDINGS: Cite the exact numbers/text recorded in the audit timeline (redacted).
          4. STRATIFIED COMPLIANCE ANALYSIS: Explain step-by-step how the data was checked against the bank's thresholds, showing that the decision was objective, fair, and mathematically correct (zero bias).
          5. GOVERNANCE CERTIFICATION: Certify that all logs are PII-redacted, immutable, and saved to the auditing registry.
        - Sign-off: "Respectfully submitted, Lead Compliance Officer, Underwriting Auditor Division"
        
        Guidelines:
        - Maintain a highly professional, legal, authoritative, and compliance-driven tone.
        - Citing exact values (DTI calculations, credit score, etc.) from the audit history is mandatory.
        
        Technical Audit History:
        Requested Loan Amount: ${session.requested_amount}
        Final Decision Status: {session.decision_status}
        
        Timeline Logs:
        {history_log}
        
        Draft Regulatory Defense Letter:
        """

    model_pool = []
    if settings.GEMINI_API_KEY:
        env_model = os.getenv("GEMINI_MODEL", "gemini/gemini-1.5-flash")
        candidate_models = []
        if env_model:
            candidate_models.append(env_model)
        for fb in ["gemini/gemini-1.5-flash", "gemini/gemini-2.0-flash", "gemini/gemini-3.5-flash"]:
            if fb not in candidate_models:
                candidate_models.append(fb)
        for m in candidate_models:
            model_pool.append({"model": m, "api_key": settings.GEMINI_API_KEY})
    if settings.GROQ_API_KEY:
        model_pool.append({"model": "groq/llama-3.1-8b-instant", "api_key": settings.GROQ_API_KEY})
    if settings.OPENAI_API_KEY and settings.OPENAI_API_KEY != "mock-key":
        model_pool.append({"model": "openai/gpt-4o-mini", "api_key": settings.OPENAI_API_KEY})

    if not model_pool:
        raise ValueError("No active LLM API keys configured.")

    letter = ""
    last_err = None

    for item in model_pool:
        model_name = item["model"]
        api_key = item["api_key"]
        
        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = completion(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    api_key=api_key
                )
                letter = response.choices[0].message.content.strip()
                break
            except Exception as e:
                last_err = e
                if "503" in str(e) or "overloaded" in str(e).lower():
                    wait_time = 2 ** attempt
                    print(f"Warning: Model {model_name} failed in challenge generator ({str(e)}). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    break
        
        if letter:
            break
    else:
        raise last_err
        
    return letter
