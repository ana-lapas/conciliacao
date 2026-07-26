import json
from datetime import datetime
from sqlalchemy import create_engine, text
import os

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

def log_to_db(level, step, message, details=None, cnab_file_id=None):
    """Insere um registro de log na tabela processing_log."""
    with engine.connect() as conn:
        conn.execute(
            text("""
                INSERT INTO processing_log (cnab_file_id, step, level, message, details)
                VALUES (:file_id, :step, :level, :message, :details)
            """),
            {
                "file_id": cnab_file_id,
                "step": step,
                "level": level,
                "message": message,
                "details": json.dumps(details) if details else None
            }
        )
        conn.commit()