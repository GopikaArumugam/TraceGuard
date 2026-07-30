from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

# Determine database engine parameters based on dialect.
# SQLite requires 'check_same_thread=False' for concurrent async operations in FastAPI.
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

# Create connection engine
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)

# Create SessionLocal class for database transactions
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Declarative base class for models
Base = declarative_base()

# Dependency helper to yield DB sessions to endpoints
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
