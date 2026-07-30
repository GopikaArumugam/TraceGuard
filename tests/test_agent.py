import uuid
import pytest
from app.db import SessionLocal, Base, engine
from app.models import AgentSession, DecisionStep
from app.agent import agent_executor
from app.callbacks import AuditCallbackHandler

@pytest.fixture(autouse=True)
def setup_database():
    """Fixture to create tables before running tests and clean them after.
    """
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

def test_agent_low_credit_denial():
    """Verify that a user with poor credit is denied and the run is audited.
    """
    session_id = uuid.uuid4()
    
    # Run the agent with poor credit user profile
    state = {
        "user_id": "usr_low",
        "email": "customer.low@gmail.com",
        "phone": "+1-555-0100",
        "requested_amount": 5000.0,
        "thoughts": [],
        "final_decision": "PENDING"
    }
    
    # Create DB Session object
    db = SessionLocal()
    session = AgentSession(
        session_id=session_id,
        user_id="usr_low",
        requested_amount=5000.0,
        decision_status="PENDING"
    )
    db.add(session)
    db.commit()
    db.close()
    
    # Attach our auditing callback listener to the agent run
    handler = AuditCallbackHandler(session_id=session_id)
    config = {"callbacks": [handler]}
    
    # Execute the agent graph
    result = agent_executor.invoke(state, config=config)
    
    # Assert correctness of output
    assert result["final_decision"] == "DENIED"
    assert len(result["thoughts"]) > 0
    
    # Check that database records were populated and redacted
    db = SessionLocal()
    saved_session = db.query(AgentSession).filter(AgentSession.session_id == session_id).first()
    # Update session status
    saved_session.decision_status = result["final_decision"]
    saved_session.final_output = result["thoughts"][0]
    db.commit()
    
    assert saved_session.decision_status == "DENIED"
    
    # Reconstruct timeline from steps
    steps = db.query(DecisionStep).filter(DecisionStep.session_id == session_id).all()
    assert len(steps) > 0
    
    # Verify PII was redacted from steps
    for step in steps:
        step_str = str(step.input_payload) + str(step.output_payload)
        assert "customer.low@gmail.com" not in step_str
        assert "Alice Smith" not in step_str
        
    db.close()

def test_agent_high_credit_approval():
    """Verify that a user with excellent credit is approved.
    """
    session_id = uuid.uuid4()
    state = {
        "user_id": "usr_high",
        "email": "customer.high@gmail.com",
        "phone": "+1-555-9999",
        "requested_amount": 10000.0,
        "thoughts": [],
        "final_decision": "PENDING"
    }
    
    db = SessionLocal()
    session = AgentSession(
        session_id=session_id,
        user_id="usr_high",
        requested_amount=10000.0,
        decision_status="PENDING"
    )
    db.add(session)
    db.commit()
    db.close()
    
    handler = AuditCallbackHandler(session_id=session_id)
    config = {"callbacks": [handler]}
    
    result = agent_executor.invoke(state, config=config)
    
    assert result["final_decision"] == "APPROVED"
