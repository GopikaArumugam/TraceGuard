from sqlalchemy.orm import Session
from uuid import UUID
from litellm import completion
from app.config import settings
from app.models import DecisionStep, AgentSession

def generate_decision_summary(session_id: UUID, db: Session) -> str:
    """Retrieves redacted audit steps and generates a plain-English explanation of the decision.
    Strictly dependent on the LLM. If keys are missing, the system will raise an error.
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

    # 4. Construct LLM prompt
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

    # 5. Model routing logic
    model_name = None
    api_key = None
    
    if settings.GEMINI_API_KEY:
        model_name = "gemini/gemini-1.5-flash"
        api_key = settings.GEMINI_API_KEY
    elif settings.OPENAI_API_KEY and settings.OPENAI_API_KEY != "mock-key":
        model_name = "openai/gpt-4o-mini"
        api_key = settings.OPENAI_API_KEY

    if not model_name:
        raise ValueError("Strict Mode Active: No active LLM API keys configured. Set GEMINI_API_KEY or OPENAI_API_KEY.")

    response = completion(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        api_key=api_key
    )
    explanation = response.choices[0].message.content.strip()
    
    return explanation
