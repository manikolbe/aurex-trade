"""Bot control — combined API + HTMX routers."""

from fastapi import APIRouter

from .api import router as api_router
from .htmx import router as htmx_router

router = APIRouter()
router.include_router(api_router)
router.include_router(htmx_router)

__all__ = ["router"]
