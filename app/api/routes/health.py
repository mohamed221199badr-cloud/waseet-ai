from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Liveness plus the two non-secret environment flags an operator needs to verify
    the deploy: app_env and llm_mode. No secrets, tokens or URLs are exposed."""
    db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "db": "ok",
        "app_env": settings.app_env,
        "llm_mode": settings.llm_mode,
    }
