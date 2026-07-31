import uuid
from datetime import datetime, date
from sqlalchemy import Column, String, Float, DateTime, ForeignKey, JSON, Text, Integer, Boolean, Date
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import TypeDecorator, CHAR
from app.db import Base

# Compatibility helper to support UUIDs across both PostgreSQL and SQLite
class GUID(TypeDecorator):
    """Platform-independent GUID type.
    Uses PostgreSQL's UUID type, otherwise uses CHAR(36) in SQLite.
    """
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID())
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == "postgresql":
            return str(value)
        else:
            if not isinstance(value, uuid.UUID):
                return str(uuid.UUID(value))
            else:
                return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        else:
            if not isinstance(value, uuid.UUID):
                return uuid.UUID(value)
            return value

class AgentSession(Base):
    __tablename__ = "agent_sessions"

    session_id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(100), nullable=False, index=True)
    requested_amount = Column(Float, nullable=False)
    final_output = Column(Text, nullable=True)
    decision_status = Column(String(50), default="PENDING", index=True) # APPROVED / DENIED / PENDING
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

class DecisionStep(Base):
    __tablename__ = "decision_steps"

    step_id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    session_id = Column(GUID(), ForeignKey("agent_sessions.session_id", ondelete="CASCADE"), nullable=False, index=True)
    step_type = Column(String(50), nullable=False, index=True) # THOUGHT / TOOL_CALL / TOOL_OUTPUT / FINAL_DECISION
    name = Column(String(100), nullable=False)
    input_payload = Column(JSON, nullable=True)
    output_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

class ApplicantProfile(Base):
    __tablename__ = "applicant_profiles"

    user_id = Column(String(100), primary_key=True)
    credit_score = Column(Integer, nullable=False)
    debts_total = Column(Float, nullable=False)
    missed_payments_last_12m = Column(Integer, nullable=False)
    monthly_gross_income = Column(Float, nullable=False)
    employment_status = Column(String(50), nullable=False)
    length_of_employment_years = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# HR Agent Tables
# ---------------------------------------------------------------------------
class EmployeeRecord(Base):
    """Stores employee HR records used by the HR Leave Approval Agent."""
    __tablename__ = "employee_records"

    employee_id = Column(String(50), primary_key=True)
    full_name = Column(String(150), nullable=False)
    department = Column(String(100), nullable=False)
    employment_status = Column(String(50), nullable=False)  # Active / Inactive / On Probation
    joining_date = Column(Date, nullable=False)


class LeaveRecord(Base):
    """Stores leave balance records per employee per leave type."""
    __tablename__ = "leave_records"

    record_id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(String(50), ForeignKey("employee_records.employee_id", ondelete="CASCADE"), nullable=False, index=True)
    leave_type = Column(String(50), nullable=False)  # Annual / Sick / Parental / Casual
    annual_allocated = Column(Integer, nullable=False)
    days_taken = Column(Integer, nullable=False, default=0)
    currently_on_leave = Column(Boolean, default=False)


# ---------------------------------------------------------------------------
# Refund Agent Tables
# ---------------------------------------------------------------------------
class CustomerAccount(Base):
    """Stores customer CRM records used by the Customer Support Refund Agent."""
    __tablename__ = "customer_accounts"

    customer_id = Column(String(50), primary_key=True)
    full_name = Column(String(150), nullable=False)
    account_status = Column(String(50), nullable=False)  # Active / Suspended / Closed
    account_tier = Column(String(50), nullable=False)    # Standard / Premium / VIP
    trust_score = Column(Integer, nullable=False)        # 0-100
    prior_refund_count = Column(Integer, nullable=False, default=0)


class OrderRecord(Base):
    """Stores order records used by the Customer Support Refund Agent."""
    __tablename__ = "order_records"

    order_id = Column(String(50), primary_key=True)
    customer_id = Column(String(50), ForeignKey("customer_accounts.customer_id", ondelete="CASCADE"), nullable=False, index=True)
    purchase_date = Column(Date, nullable=False)
    delivery_status = Column(String(50), nullable=False)  # Delivered / Pending / Returned
    total_value = Column(Float, nullable=False)
    payment_method = Column(String(50), nullable=False)
    days_since_purchase = Column(Integer, nullable=False)
    fraud_risk_score = Column(Integer, nullable=False, default=10)  # 0-100
