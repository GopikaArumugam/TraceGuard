import uuid
from fastapi import FastAPI, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.config import settings
from app.db import get_db, Base, engine
from app.models import AgentSession, DecisionStep
from app.schemas import (
    LoanRequest,
    LoanResponse,
    AuditTimelineResponse,
    StepResponse,
    ExplanationResponse,
    SessionSummaryResponse
)
from app.agent import agent_executor
from app.callbacks import AuditCallbackHandler
from app.summarizer import generate_decision_summary

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

# Initialize FastAPI application
app = FastAPI(
    title="Loan Underwriting Decision Path Auditor",
    description="Compliance logging, PII masking, and LLM explanation service for Loan Approver Agents.",
    version="1.0.0"
)

# Mount static files folder
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", response_class=FileResponse, tags=["Dashboard"])
def read_index():
    """Serves the front-end dashboard homepage.
    """
    return os.path.join(static_dir, "index.html")

# Create SQL database tables on startup (SQLite migrations fallback)
@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=engine)

@app.get("/health", status_code=status.HTTP_200_OK, tags=["System"])
def health_check():
    """Simple check endpoint to verify server is active.
    """
    return {"status": "healthy", "timestamp": datetime.utcnow()}

@app.post("/agent/run", response_model=LoanResponse, status_code=status.HTTP_201_CREATED, tags=["Agent Execution"])
def run_loan_agent(payload: LoanRequest, db: Session = Depends(get_db)):
    """Triggers the Loan Approver Agent to evaluate a loan request.
    Auto-redacts PII and commits the detailed trace steps to the database.
    """
    # 1. Initialize session record in DB
    session_id = uuid.uuid4()
    session = AgentSession(
        session_id=session_id,
        user_id=payload.user_id,
        requested_amount=payload.requested_amount,
        decision_status="PENDING"
    )
    
    try:
        db.add(session)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initialize auditing session."
        )

    # 2. Setup Agent State parameters
    initial_state = {
        "user_id": payload.user_id,
        "email": payload.email,
        "phone": payload.phone,
        "requested_amount": payload.requested_amount,
        "thoughts": [],
        "final_decision": "PENDING"
    }

    # 3. Instantiate and attach callback handler to capture tool events
    handler = AuditCallbackHandler(session_id=session_id)
    config = {"callbacks": [handler]}

    # 4. Invoke the LangGraph graph
    try:
        result = agent_executor.invoke(initial_state, config=config)
    except Exception as e:
        # Update session to failed if exception rises
        db.refresh(session)
        session.decision_status = "FAILED"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent execution failed: {str(e)}"
        )

    # 5. Retrieve thoughts and final decision details from output state
    thoughts = result.get("thoughts", ["No underwriting reasoning recorded."])
    final_decision = result.get("final_decision", "DENIED")

    # 6. Log final thoughts as step records and update main session status
    db.refresh(session)
    session.decision_status = final_decision
    session.final_output = thoughts[0] if thoughts else "Decision complete."
    
    # Save the thought step explicitly to the steps timeline
    thought_step = DecisionStep(
        session_id=session_id,
        step_type="THOUGHT",
        name="underwriter_thought",
        input_payload={"logic_reasoning": thoughts[0] if thoughts else "No thought details"},
        output_payload={}
    )
    
    # Save final decision step explicitly
    decision_step = DecisionStep(
        session_id=session_id,
        step_type="FINAL_DECISION",
        name="loan_decision",
        input_payload={},
        output_payload={"status": final_decision}
    )
    
    try:
        db.add(thought_step)
        db.add(decision_step)
        db.commit()
    except Exception as e:
        db.rollback()

    return LoanResponse(
        session_id=session_id,
        status=final_decision,
        message=f"Loan evaluation processing complete. Decision: {final_decision}."
    )

@app.get("/audit/sessions", response_model=List[SessionSummaryResponse], tags=["Audit Registry"])
def get_audit_sessions(
    user_id: Optional[str] = Query(None, description="Search by user identifier"),
    status: Optional[str] = Query(None, description="Filter by decision status (APPROVED/DENIED)"),
    db: Session = Depends(get_db)
):
    """Fulfills the Search & Retrieval Component.
    Enables searching through historical audit sessions by user or decision status.
    """
    query = db.query(AgentSession)
    if user_id:
        query = query.filter(AgentSession.user_id == user_id)
    if status:
        query = query.filter(AgentSession.decision_status == status)
        
    sessions = query.order_by(AgentSession.created_at.desc()).all()
    return sessions

@app.get("/audit/session/{session_id}", response_model=AuditTimelineResponse, tags=["Audit Registry"])
def get_session_audit_trace(session_id: uuid.UUID, db: Session = Depends(get_db)):
    """Fulfills the Replay Capability & Decision Timeline Components.
    Retrieves the complete, chronological timeline of events for an evaluation run.
    """
    session = db.query(AgentSession).filter(AgentSession.session_id == session_id).first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Auditing session record not found."
        )

    # Fetch steps in order of execution time
    steps = db.query(DecisionStep).filter(
        DecisionStep.session_id == session_id
    ).order_by(DecisionStep.created_at.asc()).all()

    timeline = [
        StepResponse(
            step_type=s.step_type,
            name=s.name,
            input_payload=s.input_payload or {},
            output_payload=s.output_payload or {},
            created_at=s.created_at
        ) for s in steps
    ]

    return AuditTimelineResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        requested_amount=session.requested_amount,
        decision_status=session.decision_status,
        created_at=session.created_at,
        timeline=timeline
    )

@app.get("/audit/session/{session_id}/explain", response_model=ExplanationResponse, tags=["Audit Registry"])
def explain_agent_decision(session_id: uuid.UUID, db: Session = Depends(get_db)):
    """Generates a plain-English explanation for an agent's loan approval decision.
    """
    try:
        explanation = generate_decision_summary(session_id=session_id, db=db)
        return ExplanationResponse(
            session_id=session_id,
            explanation=explanation
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate plain-English explanation: {str(e)}"
        )
