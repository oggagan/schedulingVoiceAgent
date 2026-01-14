"""
FastAPI Application Factory
Main entry point for the Voice Scheduling Agent
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.utils.logger import setup_logging, get_logger
from app.routers import auth_router, websocket_router, api_router

# Setup logging
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"Debug mode: {settings.debug}")
    
    # Initialize database
    init_db()
    logger.info("Database initialized")
    
    # Ensure directories exist
    os.makedirs("data", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    yield
    
    # Shutdown
    logger.info("Application shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application"""
    
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="""
        ## Voice Scheduling Agent
        
        A production-ready real-time voice assistant that helps users schedule calendar meetings
        through natural voice conversations.
        
        ### Features
        
        * 🎤 **Real-time Voice Interaction** - Powered by OpenAI Realtime API
        * 📅 **Google Calendar Integration** - Automatically creates calendar events
        * 💾 **Conversation History** - SQLite database stores all conversations
        * 📊 **REST API** - Access conversation history and statistics
        * 🔒 **Secure OAuth** - Google OAuth 2.0 authentication
        * 📝 **Structured Logging** - JSON logs with rotation
        
        ### API Documentation
        
        * **Swagger UI**: `/docs` - Interactive API documentation
        * **ReDoc**: `/redoc` - Alternative API documentation
        * **OpenAPI JSON**: `/openapi.json` - OpenAPI specification
        """,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=[
            {
                "name": "Authentication",
                "description": "Google OAuth 2.0 authentication endpoints for calendar access."
            },
            {
                "name": "WebSocket",
                "description": "Real-time WebSocket endpoint for voice communication with OpenAI Realtime API."
            },
            {
                "name": "API",
                "description": "REST API endpoints for health checks, conversation history, and statistics."
            }
        ],
        contact={
            "name": "Voice Scheduling Agent",
            "url": "https://github.com/your-repo/voice-scheduling-agent",
        },
        license_info={
            "name": "MIT",
        }
    )
    
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Mount static files
    app.mount("/static", StaticFiles(directory="static"), name="static")
    
    # Include routers
    app.include_router(auth_router)
    app.include_router(websocket_router)
    app.include_router(api_router)
    
    # Root route - serve HTML
    @app.get("/", response_class=HTMLResponse)
    async def root():
        """Serve the main HTML page"""
        with open("static/index.html", "r") as f:
            return HTMLResponse(content=f.read())
    
    # Dashboard route
    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        """Serve the dashboard page"""
        with open("static/dashboard.html", "r") as f:
            return HTMLResponse(content=f.read())
    
    # Request logging middleware
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """Log all HTTP requests"""
        import time
        start_time = time.time()
        
        response = await call_next(request)
        
        duration_ms = round((time.time() - start_time) * 1000, 2)
        
        # Skip logging for static files and health checks
        path = request.url.path
        if not path.startswith("/static") and path != "/api/health":
            logger.info(
                f"{request.method} {path} - {response.status_code}",
                extra={
                    "client_ip": request.client.host if request.client else "unknown",
                    "duration_ms": duration_ms
                }
            )
        
        return response
    
    return app


# Create application instance
app = create_app()
