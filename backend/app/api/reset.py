"""POST /api/reset — only registered when ENABLE_RESET=true (E2E tests)."""

from fastapi import APIRouter

from app.services import portfolio

router = APIRouter(prefix="/api", tags=["system"])


@router.post("/reset")
async def reset() -> dict:
    await portfolio.reset()
    return {"status": "reset"}
