"""
Google OAuth authentication routes with multi-user support
"""

from fastapi import APIRouter, Request, Response, Cookie, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.calendar import CalendarService
from app.services.user import UserService
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def get_session_token(request: Request, session: str = Cookie(None)) -> str:
    """Get session token from cookie or create new one"""
    return session


@router.get(
    "/status",
    summary="Check Authentication Status",
    description="""
    Check if the current user is authenticated with Google Calendar.
    
    Returns the current authentication status for the user identified by session cookie.
    Use this to determine if the user needs to complete the OAuth flow.
    
    **Response:**
    - `authenticated: true` - User is connected to Google Calendar
    - `authenticated: false` - User needs to authenticate
    - `email` - User's email if authenticated
    """
)
async def auth_status(
    session: str = Cookie(None),
    db: Session = Depends(get_db)
):
    """Check if Google Calendar is authenticated for current user"""
    logger.info(f"Auth status check - session cookie: {session[:8] if session else 'None'}")
    
    user_service = UserService(db)
    user = user_service.get_user_from_session_token(session)
    
    if user:
        logger.info(f"User found from session: {user.email}, user_id: {user.id}")
        calendar_service = CalendarService(db=db, user_id=user.id)
        is_authenticated = calendar_service.is_authenticated(user.id)
        
        logger.info(f"Calendar authenticated: {is_authenticated}")
        return JSONResponse({
            "authenticated": is_authenticated,
            "email": user.email if is_authenticated else None,
            "message": f"Connected as {user.email}" if is_authenticated else "Not connected"
        })
    else:
        logger.info("No user found from session cookie")
        return JSONResponse({
            "authenticated": False,
            "email": None,
            "message": "Not connected"
        })


@router.get(
    "/login",
    summary="Initiate OAuth Login",
    description="""
    Initiate the Google OAuth 2.0 authentication flow.
    
    Creates or retrieves a session for the user and redirects to Google's
    authorization page. The session token is passed in the OAuth state parameter
    to link the authentication to the correct user.
    
    **Flow:**
    1. Create/get session for user
    2. Set session cookie
    3. Redirect to Google with session token in state
    4. User grants permission
    5. Google redirects to `/auth/callback` with state
    6. Tokens are exchanged and stored for the user
    7. User is redirected back to the application
    """
)
async def auth_login(
    request: Request,
    response: Response,
    session: str = Cookie(None),
    db: Session = Depends(get_db)
):
    """Initiate Google OAuth flow with session management"""
    client_ip = request.client.host if request.client else "unknown"
    
    # Get or create session
    user_service = UserService(db)
    if not session:
        # Create new anonymous session
        session_token = user_service.create_user_session(user_id=None)
        session = session_token
    else:
        # Verify existing session is valid
        user = user_service.get_user_from_session_token(session)
        if not user:
            # Session invalid, create new one
            session_token = user_service.create_user_session(user_id=None)
            session = session_token
    
    logger.info(f"OAuth login initiated", extra={"client_ip": client_ip, "session": session[:8]})
    
    # Create calendar service and get auth URL with state
    calendar_service = CalendarService(db=db)
    auth_url = calendar_service.get_authorization_url(state=session)
    
    # Create redirect response and set cookie
    redirect = RedirectResponse(url=auth_url)
    redirect.set_cookie(
        key="session",
        value=session,
        max_age=30 * 24 * 60 * 60,  # 30 days
        httponly=True,
        samesite="lax",
        secure=False,  # Set to True in production with HTTPS
        path="/"  # Ensure cookie is available for all paths
    )
    
    logger.info(f"Session cookie set for OAuth flow: {session[:8]}")
    return redirect


@router.get("/callback")
async def auth_callback(
    request: Request,
    response: Response,
    session: str = Cookie(None),
    state: str = None,
    db: Session = Depends(get_db)
):
    """Handle Google OAuth callback with user linking"""
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    oauth_state = request.query_params.get("state") or state
    
    if error:
        logger.warning(f"OAuth error: {error}")
        return RedirectResponse(url=f"/?auth=error&message={error}")
    
    if not code:
        logger.warning("OAuth callback without code")
        return RedirectResponse(url="/?auth=error&message=no_code")
    
    # Use state from OAuth or cookie
    session_token = oauth_state or session
    
    if not session_token:
        logger.warning("OAuth callback without session")
        return RedirectResponse(url="/?auth=error&message=no_session")
    
    try:
        user_service = UserService(db)
        calendar_service = CalendarService(db=db)
        
        # Exchange code for tokens
        authorization_response = str(request.url)
        success, creds, user_email = calendar_service.exchange_code(
            authorization_response,
            user_id=None  # Will be set after user creation
        )
        
        if not success or not creds:
            logger.error("OAuth token exchange failed")
            return RedirectResponse(url="/?auth=error&message=token_exchange_failed")
        
        # Get or create user from email
        if not user_email:
            # If we couldn't get email, create a placeholder user
            # This ensures the session can be linked even if email retrieval fails
            from datetime import datetime
            user_email = f"user_{session_token[:8]}_{datetime.utcnow().strftime('%Y%m%d')}@oauth.local"
            logger.warning(f"OAuth successful but no email retrieved, using placeholder: {user_email}")
        
        user = user_service.get_or_create_user(user_email)
        
        # Save credentials to user
        calendar_service.save_credentials(user.id, creds)
        
        # Update session to link to user
        from app.utils.session import get_session
        db_session = get_session(db, session_token)
        if db_session:
            db_session.user_id = user.id
            db.commit()
        else:
            # Create new session if none exists
            session_token = user_service.create_user_session(user_id=user.id)
        
        # Set session cookie in response
        logger.info(f"Setting session cookie in callback: {session_token[:8]}, user_id: {user.id}")
        redirect = RedirectResponse(url="/?auth=success")
        redirect.set_cookie(
            key="session",
            value=session_token,
            max_age=30 * 24 * 60 * 60,  # 30 days
            httponly=True,
            samesite="lax",
            secure=False,  # Set to True in production with HTTPS
            path="/"  # Ensure cookie is available for all paths
        )
        
        logger.info(f"OAuth authentication successful for user: {user_email}")
        return redirect
    
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return RedirectResponse(url=f"/?auth=error&message={str(e)}")


@router.get(
    "/logout",
    summary="Logout / Revoke Authentication",
    description="""
    Revoke Google Calendar authentication and remove user session.
    
    After calling this endpoint, the user will need to authenticate again
    to use calendar features.
    """
)
async def auth_logout(
    response: Response,
    session: str = Cookie(None),
    db: Session = Depends(get_db)
):
    """Remove Google Calendar authentication and session"""
    if session:
        user_service = UserService(db)
        user = user_service.get_user_from_session_token(session)
        
        if user:
            # Revoke user's credentials
            calendar_service = CalendarService(db=db, user_id=user.id)
            calendar_service.revoke_credentials(user.id)
        
        # Delete session
        user_service.delete_user_session(session)
        logger.info("User logged out and session deleted")
    else:
        # Fallback: revoke legacy credentials
        from app.services.calendar import get_legacy_calendar_service
        calendar_service = get_legacy_calendar_service()
        calendar_service.revoke_credentials()
        logger.info("Legacy credentials revoked")
    
    # Clear session cookie
    response.delete_cookie(key="session")
    
    return RedirectResponse(url="/?auth=logged_out")
