"""
Seed script for HR and Refund agent database tables.
Run once: python -m app.seed_hr_refund
Creates realistic employee, leave, customer, and order records.
"""
from datetime import date
from app.db import SessionLocal, Base, engine
from app.models import (
    EmployeeRecord, LeaveRecord,
    CustomerAccount, OrderRecord
)

def seed():
    # Create all new tables
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        # ---------------------------------------------------------------
        # HR: Employee Records
        # ---------------------------------------------------------------
        employees = [
            EmployeeRecord(employee_id="EMP-1001", full_name="Jane Doe",        department="Engineering",  employment_status="Active",   joining_date=date(2022, 3, 15)),
            EmployeeRecord(employee_id="EMP-1002", full_name="Raj Patel",        department="Engineering",  employment_status="Active",   joining_date=date(2021, 8, 1)),
            EmployeeRecord(employee_id="EMP-1003", full_name="Sara Ahmed",       department="Marketing",    employment_status="Active",   joining_date=date(2023, 1, 10)),
            EmployeeRecord(employee_id="EMP-1004", full_name="Carlos Rivera",    department="Marketing",    employment_status="Active",   joining_date=date(2020, 6, 20)),
            EmployeeRecord(employee_id="EMP-1005", full_name="Priya Nair",       department="Finance",      employment_status="Active",   joining_date=date(2019, 11, 5)),
            EmployeeRecord(employee_id="EMP-1006", full_name="Tom Haverford",    department="Engineering",  employment_status="Active",   joining_date=date(2024, 2, 1)),
            EmployeeRecord(employee_id="EMP-1007", full_name="Lena Fischer",     department="HR",           employment_status="Inactive", joining_date=date(2018, 4, 12)),
            EmployeeRecord(employee_id="EMP-1008", full_name="David Kim",        department="Engineering",  employment_status="Active",   joining_date=date(2023, 9, 3)),
        ]

        # ---------------------------------------------------------------
        # HR: Leave Balance Records
        # ---------------------------------------------------------------
        leave_records = [
            # Jane Doe (EMP-1001) — ample leave
            LeaveRecord(employee_id="EMP-1001", leave_type="Annual",   annual_allocated=25, days_taken=10, currently_on_leave=False),
            LeaveRecord(employee_id="EMP-1001", leave_type="Sick",     annual_allocated=10, days_taken=2,  currently_on_leave=False),
            LeaveRecord(employee_id="EMP-1001", leave_type="Casual",   annual_allocated=5,  days_taken=1,  currently_on_leave=False),

            # Raj Patel (EMP-1002) — currently on leave (affects team coverage)
            LeaveRecord(employee_id="EMP-1002", leave_type="Annual",   annual_allocated=25, days_taken=20, currently_on_leave=True),
            LeaveRecord(employee_id="EMP-1002", leave_type="Sick",     annual_allocated=10, days_taken=3,  currently_on_leave=False),

            # Sara Ahmed (EMP-1003)
            LeaveRecord(employee_id="EMP-1003", leave_type="Annual",   annual_allocated=20, days_taken=18, currently_on_leave=False),
            LeaveRecord(employee_id="EMP-1003", leave_type="Sick",     annual_allocated=10, days_taken=0,  currently_on_leave=False),

            # Carlos Rivera (EMP-1004) — nearly exhausted annual leave
            LeaveRecord(employee_id="EMP-1004", leave_type="Annual",   annual_allocated=20, days_taken=19, currently_on_leave=False),

            # Priya Nair (EMP-1005)
            LeaveRecord(employee_id="EMP-1005", leave_type="Annual",   annual_allocated=25, days_taken=5,  currently_on_leave=False),
            LeaveRecord(employee_id="EMP-1005", leave_type="Parental", annual_allocated=90, days_taken=0,  currently_on_leave=False),

            # Tom Haverford (EMP-1006) — new joiner, small allocation
            LeaveRecord(employee_id="EMP-1006", leave_type="Annual",   annual_allocated=12, days_taken=2,  currently_on_leave=False),

            # David Kim (EMP-1008)
            LeaveRecord(employee_id="EMP-1008", leave_type="Annual",   annual_allocated=25, days_taken=8,  currently_on_leave=False),
            LeaveRecord(employee_id="EMP-1008", leave_type="Sick",     annual_allocated=10, days_taken=6,  currently_on_leave=False),
        ]

        # ---------------------------------------------------------------
        # Refund: Customer Account Records
        # ---------------------------------------------------------------
        customers = [
            CustomerAccount(customer_id="CUST-5001", full_name="Robert Johnson",   account_status="Active",    account_tier="Premium",  trust_score=95, prior_refund_count=1),
            CustomerAccount(customer_id="CUST-5002", full_name="Emily Chen",       account_status="Active",    account_tier="Standard", trust_score=78, prior_refund_count=0),
            CustomerAccount(customer_id="CUST-5003", full_name="Michael Torres",   account_status="Active",    account_tier="VIP",      trust_score=99, prior_refund_count=3),
            CustomerAccount(customer_id="CUST-5004", full_name="Fatima Al-Rashid", account_status="Suspended", account_tier="Standard", trust_score=40, prior_refund_count=5),
            CustomerAccount(customer_id="CUST-5005", full_name="James O'Brien",    account_status="Active",    account_tier="Standard", trust_score=82, prior_refund_count=0),
        ]

        # ---------------------------------------------------------------
        # Refund: Order Records
        # ---------------------------------------------------------------
        orders = [
            # Robert Johnson — recent delivery, low fraud, eligible
            OrderRecord(order_id="ORD-9001", customer_id="CUST-5001", purchase_date=date(2026, 7, 20), delivery_status="Delivered", total_value=149.99, payment_method="Credit Card", days_since_purchase=11, fraud_risk_score=8),
            # Emily Chen — recent, eligible
            OrderRecord(order_id="ORD-9002", customer_id="CUST-5002", purchase_date=date(2026, 7, 15), delivery_status="Delivered", total_value=89.50,  payment_method="Debit Card",  days_since_purchase=16, fraud_risk_score=15),
            # Michael Torres — large order, exceeds auto-approval cap
            OrderRecord(order_id="ORD-9003", customer_id="CUST-5003", purchase_date=date(2026, 7, 25), delivery_status="Delivered", total_value=750.00, payment_method="Credit Card", days_since_purchase=6,  fraud_risk_score=12),
            # Robert Johnson — old order, outside 30-day window
            OrderRecord(order_id="ORD-9004", customer_id="CUST-5001", purchase_date=date(2026, 5, 10), delivery_status="Delivered", total_value=200.00, payment_method="Credit Card", days_since_purchase=82, fraud_risk_score=10),
            # James O'Brien — high fraud score
            OrderRecord(order_id="ORD-9005", customer_id="CUST-5005", purchase_date=date(2026, 7, 28), delivery_status="Delivered", total_value=210.00, payment_method="Credit Card", days_since_purchase=3,  fraud_risk_score=88),
        ]

        # Insert only if not already seeded
        for emp in employees:
            if not db.get(EmployeeRecord, emp.employee_id):
                db.add(emp)

        for lr in leave_records:
            # Check by employee_id + leave_type combination
            existing = db.query(LeaveRecord).filter(
                LeaveRecord.employee_id == lr.employee_id,
                LeaveRecord.leave_type == lr.leave_type
            ).first()
            if not existing:
                db.add(lr)

        for cust in customers:
            if not db.get(CustomerAccount, cust.customer_id):
                db.add(cust)

        for order in orders:
            if not db.get(OrderRecord, order.order_id):
                db.add(order)

        db.commit()
        print("[OK] HR and Refund seed data committed successfully.")
        print(f"  - {len(employees)} employees | {len(leave_records)} leave records")
        print(f"  - {len(customers)} customers | {len(orders)} orders")

    except Exception as e:
        db.rollback()
        print(f"[FAIL] Seeding failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
