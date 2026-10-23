from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.db.session import engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def live() -> dict:
    """Is the process running. Touches nothing."""
    return {"status": "ok"}


@router.get("/readyz")
async def ready(response: Response) -> dict:
    """Can the process serve. Touches the database."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "detail": type(exc).__name__}
    return {"status": "ok", "database": "ok"}