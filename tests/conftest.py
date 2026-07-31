import os
# Force tests to run against a separate database file, protecting development logs
os.environ["DATABASE_URL"] = "sqlite:///./test_audit_logs.db"
