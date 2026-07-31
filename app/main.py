import uuid
from fastapi import FastAPI, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.config import settings
from app.db import get_db, Base, engine
from app.models import AgentSession, DecisionStep, ApplicantProfile
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
from app.summarizer import generate_decision_summary, generate_challenge_response

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
import os

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def validate_api_key(api_key: str = Depends(api_key_header)):
    if not api_key or api_key != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key (X-API-Key header required)."
        )
    return api_key

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

def seed_database_profiles(db: Session):
    """Pre-populates the database with the 6 standard loan applicant profiles 
    to make the testing dataset 100% database-driven and remove code fallbacks.
    """
    count = db.query(ApplicantProfile).count()
    if count > 0:
        return
        
    presets = [
        ApplicantProfile(
            user_id="usr_qualified",
            credit_score=740,
            monthly_gross_income=9500.0,
            debts_total=1200.0,
            missed_payments_last_12m=0,
            employment_status="Employed",
            length_of_employment_years=3.5
        ),
        ApplicantProfile(
            user_id="usr_low",
            credit_score=500,
            monthly_gross_income=1500.0,
            debts_total=8000.0,
            missed_payments_last_12m=0,
            employment_status="Employed",
            length_of_employment_years=4.5
        ),
        ApplicantProfile(
            user_id="usr_unemployed",
            credit_score=680,
            monthly_gross_income=0.0,
            debts_total=1000.0,
            missed_payments_last_12m=0,
            employment_status="Unemployed",
            length_of_employment_years=0.0
        ),
        ApplicantProfile(
            user_id="usr_new_job",
            credit_score=690,
            monthly_gross_income=4500.0,
            debts_total=1500.0,
            missed_payments_last_12m=0,
            employment_status="Employed",
            length_of_employment_years=0.4
        ),
        ApplicantProfile(
            user_id="usr_missed_payments",
            credit_score=710,
            monthly_gross_income=5000.0,
            debts_total=2000.0,
            missed_payments_last_12m=2,
            employment_status="Employed",
            length_of_employment_years=2.5
        ),
        ApplicantProfile(
            user_id="usr_high_debt",
            credit_score=620,
            monthly_gross_income=3000.0,
            debts_total=6500.0,
            missed_payments_last_12m=0,
            employment_status="Employed",
            length_of_employment_years=3.0
        )
    ]
    
    try:
        db.add_all(presets)
        db.commit()
        print("Database seeding completed successfully. Added 6 base applicant profiles.")
    except Exception as e:
        db.rollback()
        print(f"Warning: Database seeding failed: {str(e)}")

# Create SQL database tables on startup (SQLite migrations fallback)
@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=engine)
    db = next(get_db())
    try:
        seed_database_profiles(db)
    finally:
        db.close()

@app.get("/health", status_code=status.HTTP_200_OK, tags=["System"])
def health_check():
    """Simple check endpoint to verify server is active.
    """
    return {"status": "healthy", "timestamp": datetime.utcnow()}

@app.post("/agent/run", response_model=LoanResponse, status_code=status.HTTP_201_CREATED, tags=["Agent Execution"])
def run_loan_agent(payload: LoanRequest, db: Session = Depends(get_db), api_key: str = Depends(validate_api_key)):
    """Triggers the Loan Approver Agent to evaluate a loan request.
    Auto-redacts PII and commits the detailed trace steps to the database.
    Supports auditing different target agents (loan, medical, or trading).
    """
    # 0. Routing for simulated HR Leave Approval Agent
    # 0. Routing for simulated HR Leave Approval Agent
    if payload.agent_type == "hr":
        session_id = uuid.uuid4()
        req_days = payload.hr_leave_days if payload.hr_leave_days is not None else 5
        available_balance = 15
        
        # Dynamic policy evaluation check
        if req_days > available_balance:
            status_verdict = "DENIED"
            final_out = f"Leave request of {req_days} days denied. Insufficient leave balance ({available_balance} days available)."
            reasoning = f"Employee Jane Doe requested {req_days} days of {payload.hr_leave_type or 'Annual'} leave. Employee profile is active. Available balance is {available_balance} days, which is less than the requested {req_days} days. Policy check failed: Insufficient balance. Action: DENIED."
            response_msg = "HR Leave request denied due to insufficient balance. DENIED."
        else:
            status_verdict = "APPROVED"
            final_out = f"Leave request of {req_days} days approved under company HR leave policy."
            reasoning = f"Employee Jane Doe requested {req_days} days of {payload.hr_leave_type or 'Annual'} leave. Employee profile is active. Available balance is {available_balance} days, which covers the requested {req_days} days. Team overlap analysis indicates a coverage ratio of 87.5% (above 75% policy threshold). Leave policy checks successfully passed. Action: APPROVED."
            response_msg = "HR Leave audit complete. APPROVED."

        session = AgentSession(
            session_id=session_id,
            user_id=payload.hr_employee_id or payload.user_id,
            requested_amount=float(req_days),
            decision_status=status_verdict,
            final_output=final_out
        )
        steps = [
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_CALL",
                name="validate_employee_record",
                input_payload={"employee_id": payload.hr_employee_id or payload.user_id},
                output_payload={}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_OUTPUT",
                name="validate_employee_record",
                input_payload={},
                output_payload={"name": "Jane Doe", "department": "Engineering", "employment_status": "Active", "joining_date": "2023-03-15"}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_CALL",
                name="fetch_leave_balance",
                input_payload={"employee_id": payload.hr_employee_id or payload.user_id, "leave_type": payload.hr_leave_type or "Annual"},
                output_payload={}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_OUTPUT",
                name="fetch_leave_balance",
                input_payload={},
                output_payload={"annual_allocated": 25, "days_taken": 10, "current_balance": available_balance}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_CALL",
                name="check_team_availability",
                input_payload={"department": "Engineering", "start_date": "2026-08-10", "end_date": "2026-08-15"},
                output_payload={}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_OUTPUT",
                name="check_team_availability",
                input_payload={},
                output_payload={"active_team_members": 8, "overlapping_leaves_count": 1, "coverage_ratio": 0.875}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="THOUGHT",
                name="policy_evaluation",
                input_payload={"logic_reasoning": reasoning},
                output_payload={}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="FINAL_DECISION",
                name="leave_verdict",
                input_payload={},
                output_payload={"status": status_verdict, "approved_days": req_days if status_verdict == "APPROVED" else 0}
            )
        ]
        try:
            db.add(session)
            for s in steps:
                db.add(s)
            db.commit()
            return LoanResponse(
                session_id=session_id,
                status=status_verdict,
                message=response_msg
            )
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to execute HR agent: {str(e)}"
            )

    # 0.1 Routing for simulated Customer Support Refund Agent
    elif payload.agent_type == "refund":
        session_id = uuid.uuid4()
        ref_amt = payload.refund_amount if payload.refund_amount is not None else 149.99
        max_allowed_refund = 250.00
        
        # Dynamic policy evaluation check
        if ref_amt > max_allowed_refund:
            status_verdict = "DENIED"
            final_out = f"Refund request of ${ref_amt} denied. Refund value exceeds automated threshold limit of ${max_allowed_refund}."
            reasoning = f"Customer Robert Johnson requested a refund of ${ref_amt} for order 'ORD-55419'. The transaction is settled. However, the requested refund amount exceeds the automated threshold limit of ${max_allowed_refund}. Policy check failed: Requires manual manager oversight. Action: DENIED."
            response_msg = "Refund request denied. Transaction value exceeds automated check thresholds."
        else:
            status_verdict = "APPROVED"
            final_out = f"Refund request of ${ref_amt} approved under consumer return safety guidelines."
            reasoning = f"Customer Robert Johnson requested a refund of ${ref_amt} for order 'ORD-55419'. The order was delivered on 2026-07-28 (within the 30-day window). Payment transaction TXN_7744112 is settled. Fraud check score is low (12/100). Refund matches our customer satisfaction guarantee. Action: APPROVED."
            response_msg = "Refund risk audit complete. APPROVED."

        session = AgentSession(
            session_id=session_id,
            user_id=payload.refund_customer_id or payload.user_id,
            requested_amount=ref_amt,
            decision_status=status_verdict,
            final_output=final_out
        )
        steps = [
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_CALL",
                name="validate_customer_account",
                input_payload={"customer_id": payload.refund_customer_id or payload.user_id},
                output_payload={}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_OUTPUT",
                name="validate_customer_account",
                input_payload={},
                output_payload={"name": "Robert Johnson", "account_status": "Active", "account_level": "Premium", "trust_score": 98}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_CALL",
                name="retrieve_order_details",
                input_payload={"order_id": payload.refund_order_id or "ORD-55419", "customer_id": payload.refund_customer_id or payload.user_id},
                output_payload={}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_OUTPUT",
                name="retrieve_order_details",
                input_payload={},
                output_payload={"purchase_date": "2026-07-28", "items_count": 2, "total_value": ref_amt, "delivery_status": "Delivered"}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_CALL",
                name="verify_payment_transaction",
                input_payload={"order_id": payload.refund_order_id or "ORD-55419", "payment_method": "Credit Card"},
                output_payload={}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_OUTPUT",
                name="verify_payment_transaction",
                input_payload={},
                output_payload={"transaction_id": "TXN_7744112", "charge_status": "Settled", "disputed": False}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_CALL",
                name="evaluate_fraud_risk",
                input_payload={"customer_id": payload.refund_customer_id or payload.user_id, "refund_amount": ref_amt},
                output_payload={}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="TOOL_OUTPUT",
                name="evaluate_fraud_risk",
                input_payload={},
                output_payload={"fraud_risk_score": 12, "flagged": False}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="THOUGHT",
                name="refund_policy_check",
                input_payload={"logic_reasoning": reasoning},
                output_payload={}
            ),
            DecisionStep(
                session_id=session_id,
                step_type="FINAL_DECISION",
                name="refund_verdict",
                input_payload={},
                output_payload={"status": status_verdict, "refunded_amount": ref_amt if status_verdict == "APPROVED" else 0.0}
            )
        ]
        try:
            db.add(session)
            for s in steps:
                db.add(s)
            db.commit()
            return LoanResponse(
                session_id=session_id,
                status=status_verdict,
                message=response_msg
            )
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to execute Customer Support refund agent: {str(e)}"
            )

    # 0.2 Check and upsert custom ApplicantProfile if custom parameters are provided
    if any(v is not None for v in [
        payload.credit_score,
        payload.debts_total,
        payload.missed_payments_last_12m,
        payload.monthly_gross_income,
        payload.employment_status,
        payload.length_of_employment_years
    ]):
        profile = db.query(ApplicantProfile).filter(ApplicantProfile.user_id == payload.user_id).first()
        if not profile:
            profile = ApplicantProfile(user_id=payload.user_id)
            # Provide safe default fallbacks for unsupplied values
            profile.credit_score = payload.credit_score if payload.credit_score is not None else 650
            profile.debts_total = payload.debts_total if payload.debts_total is not None else 2000.0
            profile.missed_payments_last_12m = payload.missed_payments_last_12m if payload.missed_payments_last_12m is not None else 0
            profile.monthly_gross_income = payload.monthly_gross_income if payload.monthly_gross_income is not None else 3000.0
            profile.employment_status = payload.employment_status if payload.employment_status is not None else "Employed"
            profile.length_of_employment_years = payload.length_of_employment_years if payload.length_of_employment_years is not None else 2.0
            db.add(profile)
        else:
            if payload.credit_score is not None:
                profile.credit_score = payload.credit_score
            if payload.debts_total is not None:
                profile.debts_total = payload.debts_total
            if payload.missed_payments_last_12m is not None:
                profile.missed_payments_last_12m = payload.missed_payments_last_12m
            if payload.monthly_gross_income is not None:
                profile.monthly_gross_income = payload.monthly_gross_income
            if payload.employment_status is not None:
                profile.employment_status = payload.employment_status
            if payload.length_of_employment_years is not None:
                profile.length_of_employment_years = payload.length_of_employment_years
            
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to save custom applicant profile parameters: {str(e)}"
            )

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

    # 2. Setup Agent State parameters (ReAct loop state)
    initial_state = {
        "user_id": payload.user_id,
        "email": payload.email,
        "phone": payload.phone,
        "requested_amount": payload.requested_amount,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are a professional bank loan underwriter agent.\n"
                    f"Your objective is to evaluate a loan request for User ID '{payload.user_id}' requesting ${payload.requested_amount}.\n\n"
                    f"You must use the following tools to fetch the necessary information:\n"
                    f"1. `get_credit_profile` (requires user_id, email)\n"
                    f"2. `get_active_debts` (requires user_id)\n"
                    f"3. `get_income_profile` (requires user_id)\n\n"
                    f"Underwriting & Affordability Rules (5 Criteria to Consider):\n"
                    f"1. Credit Score: If Credit Score is below 580, DENY immediately (subprime borrower). Stop execution.\n"
                    f"2. Employment Status: Must be exactly 'Employed'. If 'Unemployed', DENY immediately. Stop execution.\n"
                    f"3. Length of Employment: Must be at least 1.0 years. If less than 1.0 years, DENY immediately. Stop. \n"
                    f"4. Payment History: Must have 0 missed payments in the last 12 months. If missed payments > 0, DENY. Stop.\n"
                    f"5. Debt-to-Income (DTI) Ratio: Must be 45% or lower. Calculate DTI as follows:\n"
                    f"   a. Proposed monthly payment = Requested Amount / 60 months (5-year term).\n"
                    f"   b. Monthly debt obligations = (Outstanding Debts * 0.05) + Proposed monthly payment.\n"
                    f"   c. DTI Ratio = Monthly debt obligations / Monthly gross income.\n"
                    f"   d. If DTI Ratio is greater than 45%, DENY. Otherwise, APPROVE.\n\n"
                    f"Execution Instructions:\n"
                    f"1. First, call `get_credit_profile` to check the credit score.\n"
                    f"2. If score is >= 580, call both `get_active_debts` and `get_income_profile` to retrieve the remaining 4 features.\n"
                    f"3. Evaluate all 5 criteria. If any criteria fails, DENY the loan and explain exactly which features failed.\n"
                    f"4. If all 5 criteria pass, APPROVE the loan.\n"
                    f"5. When you have made a final decision, write:\n"
                    f"   Thought: <Your detailed reasoning explaining the 5 features, DTI calculations, and the specific failure reason if denied>\n"
                    f"   Decision: <APPROVED or DENIED>\n"
                )
            },
            {
                "role": "user",
                "content": "Begin the evaluation."
            }
        ],
        "thoughts": [],
        "final_decision": "PENDING"
    }

    # 3. Instantiate and attach callback handler and configurable agent policy
    handler = AuditCallbackHandler(session_id=session_id)
    config = {
        "callbacks": [handler],
        "configurable": {
            "credit_score_threshold": payload.credit_score_threshold if payload.credit_score_threshold is not None else 580,
            "max_dti_ratio": payload.max_dti_ratio if payload.max_dti_ratio is not None else 0.45,
            "min_employment_years": payload.min_employment_years if payload.min_employment_years is not None else 1.0,
            "llm_model": payload.llm_model if payload.llm_model else "gemini/gemini-3.5-flash"
        }
    }

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
    start_time: Optional[datetime] = Query(None, description="Filter logs starting from this timestamp (ISO format)"),
    end_time: Optional[datetime] = Query(None, description="Filter logs ending at this timestamp (ISO format)"),
    db: Session = Depends(get_db),
    api_key: str = Depends(validate_api_key)
):
    """Fulfills the Search & Retrieval Component.
    Enables searching through historical audit sessions by user, decision status, or time range.
    """
    query = db.query(AgentSession)
    if user_id:
        query = query.filter(AgentSession.user_id == user_id)
    if status:
        query = query.filter(AgentSession.decision_status == status)
    if start_time:
        query = query.filter(AgentSession.created_at >= start_time)
    if end_time:
        query = query.filter(AgentSession.created_at <= end_time)
        
    sessions = query.order_by(AgentSession.created_at.desc()).all()
    return sessions

@app.get("/audit/session/{session_id}", response_model=AuditTimelineResponse, tags=["Audit Registry"])
def get_session_audit_trace(session_id: uuid.UUID, db: Session = Depends(get_db), api_key: str = Depends(validate_api_key)):
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
def explain_agent_decision(session_id: uuid.UUID, db: Session = Depends(get_db), api_key: str = Depends(validate_api_key)):
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

@app.get("/audit/session/{session_id}/challenge-response", response_model=ExplanationResponse, tags=["Audit Registry"])
def explain_regulatory_challenge(session_id: uuid.UUID, db: Session = Depends(get_db), api_key: str = Depends(validate_api_key)):
    """Fulfills the Regulatory Challenge Response Generator (Bonus Feature).
    Generates a formal compliance audit defense letter explaining the underwriting details.
    """
    try:
        explanation = generate_challenge_response(session_id=session_id, db=db)
        return ExplanationResponse(
            session_id=session_id,
            explanation=explanation
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate regulatory compliance defense letter: {str(e)}"
        )
