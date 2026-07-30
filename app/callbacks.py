from typing import Any, Dict, List, Optional
from uuid import UUID
from langchain_core.callbacks import BaseCallbackHandler
from app.db import SessionLocal
from app.models import DecisionStep
from app.redactor import pii_redactor

class AuditCallbackHandler(BaseCallbackHandler):
    """Custom callback handler to intercept agent execution events.
    Automatically redacts PII and persists events as decision steps in the database.
    """
    
    def __init__(self, session_id: UUID):
        super().__init__()
        self.session_id = session_id

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Called when a tool starts executing.
        """
        tool_name = serialized.get("name", "unknown_tool")
        
        # Redact any PII from the inputs before logging
        redacted_input = pii_redactor.redact_text(input_str)
        
        db = SessionLocal()
        try:
            step = DecisionStep(
                session_id=self.session_id,
                step_type="TOOL_CALL",
                name=tool_name,
                input_payload={"raw_input": redacted_input},
                output_payload={}
            )
            db.add(step)
            db.commit()
        except Exception as e:
            db.rollback()
        finally:
            db.close()

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Called when a tool finishes executing.
        """
        # Redact any PII from the outputs before logging
        redacted_output = pii_redactor.redact_data(output)
        
        db = SessionLocal()
        try:
            step = DecisionStep(
                session_id=self.session_id,
                step_type="TOOL_OUTPUT",
                name="tool_response",
                input_payload={},
                output_payload={"result": redacted_output}
            )
            db.add(step)
            db.commit()
        except Exception as e:
            db.rollback()
        finally:
            db.close()
