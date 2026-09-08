from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.robot_health import robot_health_summary


router = APIRouter(prefix="/robot-health", tags=["robot-health"])


@router.get("")
def summary(x_user_id: str = Header(min_length=8, max_length=80), db: Session = Depends(get_db)) -> dict:
    return robot_health_summary(db, x_user_id)
