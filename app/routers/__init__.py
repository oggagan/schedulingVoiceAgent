"""
API Routers
"""

from app.routers.auth import router as auth_router
from app.routers.websocket import router as websocket_router
from app.routers.api import router as api_router

__all__ = ["auth_router", "websocket_router", "api_router"]
