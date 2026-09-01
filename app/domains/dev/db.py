from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.core.database import get_session

router = APIRouter()


@router.get("/health")
def check_database_health(session: Session = Depends(get_session)):
    try:
        session.execute(text("SELECT 1")).one()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection failed",
        ) from exc

    return {"database": "ok"}
