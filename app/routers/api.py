"""
REST API routes for health checks, conversation history, and statistics
"""

from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Cookie
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.config import settings
from app.database import get_db
from app.services.conversation import ConversationService
from app.services.calendar import CalendarService
from app.services.user import UserService
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["API"])


# ==================== Response Models ====================

class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str
    services: dict


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    timestamp: datetime

    class Config:
        from_attributes = True


class CalendarEventResponse(BaseModel):
    id: int
    google_event_id: Optional[str]
    summary: str
    start_time: datetime
    end_time: datetime
    attendee_name: Optional[str]
    html_link: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ConversationResponse(BaseModel):
    id: int
    session_id: str
    status: str
    started_at: datetime
    ended_at: Optional[datetime]
    message_count: int
    events_created: int

    class Config:
        from_attributes = True


class ConversationDetailResponse(ConversationResponse):
    messages: List[MessageResponse]
    calendar_events: List[CalendarEventResponse]


class StatsResponse(BaseModel):
    total_conversations: int
    active: int
    completed: int
    errors: int
    total_calendar_events: int
    total_messages: int = 0


class UserResponse(BaseModel):
    id: int
    email: Optional[str]
    created_at: datetime
    last_login: datetime
    is_authenticated: bool
    
    class Config:
        from_attributes = True


# ==================== Endpoints ====================

@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health Check",
    description="""
    Health check endpoint for load balancers and monitoring systems.
    
    Returns the current status of the application and all connected services.
    Use this endpoint to verify the application is running and all services are operational.
    
    **Response Codes:**
    - `200 OK`: Application is healthy
    - All services are checked and status is returned
    
    **Use Cases:**
    - Load balancer health checks
    - Monitoring system probes
    - Deployment verification
    """
)
async def health_check(
    session: str = Cookie(None),
    db: Session = Depends(get_db)
):
    """
    Health check endpoint for load balancers and monitoring
    
    Returns service status and version information
    """
    # Check if any user is authenticated (for backward compatibility)
    user_service = UserService(db)
    user = user_service.get_user_from_session_token(session) if session else None
    
    google_calendar_status = "not_connected"
    if user:
        calendar_service = CalendarService(db=db, user_id=user.id)
        google_calendar_status = "connected" if calendar_service.is_authenticated(user.id) else "not_connected"
    else:
        # Fallback to legacy check
        legacy_service = CalendarService()
        google_calendar_status = "connected" if legacy_service.is_authenticated() else "not_connected"
    
    services = {
        "database": "healthy",
        "google_calendar": google_calendar_status,
        "openai": "configured" if settings.openai_api_key else "not_configured"
    }
    
    return HealthResponse(
        status="healthy",
        version=settings.app_version,
        timestamp=datetime.utcnow().isoformat() + "Z",
        services=services
    )


@router.get(
    "/user/me",
    response_model=UserResponse,
    summary="Get Current User",
    description="""
    Get information about the currently authenticated user.
    
    Returns user profile including:
    - User ID
    - Email address
    - Account creation date
    - Last login time
    - Authentication status
    
    **Authentication Required**: This endpoint requires a valid session cookie.
    
    **Use Cases:**
    - Display user profile in dashboard
    - Verify user authentication
    - Get user-specific information
    """
)
async def get_current_user(
    session: str = Cookie(None),
    db: Session = Depends(get_db)
):
    """
    Get current authenticated user information
    
    Requires valid session cookie. Returns 401 if not authenticated.
    """
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    user_service = UserService(db)
    user = user_service.get_user_from_session_token(session)
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")
    
    # Check if user has valid Google Calendar credentials
    calendar_service = CalendarService(db=db, user_id=user.id)
    is_authenticated = calendar_service.is_authenticated(user.id)
    
    logger.debug(f"Retrieved user info for user {user.id}")
    
    return UserResponse(
        id=user.id,
        email=user.email,
        created_at=user.created_at,
        last_login=user.last_login,
        is_authenticated=is_authenticated
    )


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="Get Statistics",
    description="""
    Retrieve application-wide statistics.
    
    Returns aggregated counts of:
    - Total conversations
    - Active conversations
    - Completed conversations
    - Error conversations
    - Total calendar events created
    
    **Use Cases:**
    - Dashboard metrics
    - Monitoring and analytics
    - Usage tracking
    """
)
async def get_stats(
    session: str = Cookie(None),
    db: Session = Depends(get_db)
):
    """
    Get application statistics
    
    Returns counts of conversations and calendar events.
    If user is authenticated via session, only returns their statistics.
    Otherwise returns all statistics (for admin/backward compatibility).
    """
    conversation_service = ConversationService(db)
    
    # Get user from session if available
    user_id = None
    if session:
        user_service = UserService(db)
        user = user_service.get_user_from_session_token(session)
        if user:
            user_id = user.id
    
    stats = conversation_service.get_conversation_stats(user_id=user_id)
    
    logger.info(f"Stats retrieved for user_id: {user_id}")
    return StatsResponse(**stats)


@router.get(
    "/conversations",
    response_model=List[ConversationResponse],
    summary="List Conversations",
    description="""
    Retrieve a paginated list of all conversations.
    
    Returns conversations ordered by start time (newest first).
    Each conversation includes:
    - Session ID
    - Status (active, completed, error)
    - Start and end times
    - Message count
    - Calendar events created
    
    **Pagination:**
    - Use `limit` to control page size (max 100)
    - Use `offset` to skip results for pagination
    - Example: `?limit=20&offset=0` for first page, `?limit=20&offset=20` for second page
    
    **Example Request:**
    ```
    GET /api/conversations?limit=10&offset=0
    ```
    """
)
async def list_conversations(
    limit: int = Query(
        default=50,
        le=100,
        ge=1,
        description="Maximum number of results to return (1-100)"
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of results to skip for pagination"
    ),
    session: str = Cookie(None),
    db: Session = Depends(get_db)
):
    """
    List conversations with pagination
    
    - **limit**: Maximum number of results (default 50, max 100)
    - **offset**: Number of results to skip (for pagination)
    
    If user is authenticated via session, only returns their conversations.
    Otherwise returns all conversations (for admin/backward compatibility).
    """
    conversation_service = ConversationService(db)
    
    # Get user from session if available
    user_id = None
    if session:
        user_service = UserService(db)
        user = user_service.get_user_from_session_token(session)
        if user:
            user_id = user.id
    
    conversations = conversation_service.list_conversations(
        limit=limit,
        offset=offset,
        user_id=user_id
    )
    
    result = []
    for conv in conversations:
        result.append(ConversationResponse(
            id=conv.id,
            session_id=conv.session_id,
            status=conv.status.value,
            started_at=conv.started_at,
            ended_at=conv.ended_at,
            message_count=len(conv.messages),
            events_created=len(conv.calendar_events)
        ))
    
    logger.debug(f"Listed {len(result)} conversations")
    return result


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="Get Conversation Details",
    description="""
    Retrieve detailed information about a specific conversation.
    
    Returns the complete conversation including:
    - All messages (user and assistant)
    - All calendar events created
    - Session metadata
    - Timestamps
    
    **Use Cases:**
    - View conversation history
    - Debug conversation issues
    - Export conversation data
    
    **Example Request:**
    ```
    GET /api/conversations/123
    ```
    """
)
async def get_conversation(
    conversation_id: int,
    session: str = Cookie(None),
    db: Session = Depends(get_db)
):
    """
    Get detailed conversation by ID
    
    Returns conversation with all messages and calendar events.
    If user is authenticated, only returns their own conversations.
    """
    conversation_service = ConversationService(db)
    conv = conversation_service.get_conversation_by_id(conversation_id)
    
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Check if user owns this conversation
    if session:
        user_service = UserService(db)
        user = user_service.get_user_from_session_token(session)
        if user and conv.user_id and conv.user_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied to this conversation")
    
    messages = [
        MessageResponse(
            id=msg.id,
            role=msg.role,
            content=msg.content,
            timestamp=msg.timestamp
        )
        for msg in conv.messages
    ]
    
    events = [
        CalendarEventResponse(
            id=evt.id,
            google_event_id=evt.google_event_id,
            summary=evt.summary,
            start_time=evt.start_time,
            end_time=evt.end_time,
            attendee_name=evt.attendee_name,
            html_link=evt.html_link,
            created_at=evt.created_at
        )
        for evt in conv.calendar_events
    ]
    
    logger.debug(f"Retrieved conversation {conversation_id}")
    
    return ConversationDetailResponse(
        id=conv.id,
        session_id=conv.session_id,
        status=conv.status.value,
        started_at=conv.started_at,
        ended_at=conv.ended_at,
        message_count=len(messages),
        events_created=len(events),
        messages=messages,
        calendar_events=events
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=List[MessageResponse],
    summary="Get Conversation Messages",
    description="""
    Retrieve all messages for a specific conversation.
    
    Returns messages in chronological order (oldest first).
    Each message includes:
    - Role (user, assistant, system)
    - Content
    - Timestamp
    
    **Use Cases:**
    - Display conversation transcript
    - Analyze conversation flow
    - Export messages
    
    **Example Request:**
    ```
    GET /api/conversations/123/messages
    ```
    """
)
async def get_conversation_messages(
    conversation_id: int,
    db: Session = Depends(get_db)
):
    """
    Get messages for a specific conversation
    """
    conversation_service = ConversationService(db)
    conv = conversation_service.get_conversation_by_id(conversation_id)
    
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    return [
        MessageResponse(
            id=msg.id,
            role=msg.role,
            content=msg.content,
            timestamp=msg.timestamp
        )
        for msg in conv.messages
    ]


@router.get(
    "/events",
    response_model=List[CalendarEventResponse],
    summary="List Calendar Events",
    description="""
    Retrieve a paginated list of all calendar events created through the application.
    
    Returns events ordered by creation time (newest first).
    Each event includes:
    - Google Calendar event ID
    - Summary (title)
    - Start and end times
    - Attendee name
    - HTML link to view in Google Calendar
    
    **Pagination:**
    - Use `limit` to control page size (max 100)
    - Use `offset` to skip results for pagination
    
    **Example Request:**
    ```
    GET /api/events?limit=20&offset=0
    ```
    """
)
async def list_calendar_events(
    limit: int = Query(
        default=50,
        le=100,
        ge=1,
        description="Maximum number of results to return (1-100)"
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of results to skip for pagination"
    ),
    session: str = Cookie(None),
    db: Session = Depends(get_db)
):
    """
    List calendar events created through the application.
    
    If user is authenticated via session, only returns events from their conversations.
    Otherwise returns all events (for admin/backward compatibility).
    """
    conversation_service = ConversationService(db)
    
    # Get user from session if available
    user_id = None
    if session:
        user_service = UserService(db)
        user = user_service.get_user_from_session_token(session)
        if user:
            user_id = user.id
    
    # List events, filtered by user if authenticated
    events = conversation_service.list_calendar_events(
        limit=limit,
        offset=offset,
        user_id=user_id
    )
    
    return [
        CalendarEventResponse(
            id=evt.id,
            google_event_id=evt.google_event_id,
            summary=evt.summary,
            start_time=evt.start_time,
            end_time=evt.end_time,
            attendee_name=evt.attendee_name,
            html_link=evt.html_link,
            created_at=evt.created_at
        )
        for evt in events
    ]
