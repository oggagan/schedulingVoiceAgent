"""
Session management utilities
"""

import secrets
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session as DBSession

from app.models import Session as SessionModel, User
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Session expiration (30 days)
SESSION_EXPIRATION_DAYS = 30


def generate_session_token() -> str:
    """Generate a secure random session token"""
    return secrets.token_urlsafe(32)


def create_session(
    db: DBSession,
    user_id: Optional[int] = None
) -> SessionModel:
    """
    Create a new session
    
    Args:
        db: Database session
        user_id: Optional user ID to link session to user
        
    Returns:
        Created Session object
    """
    session_token = generate_session_token()
    expires_at = datetime.utcnow() + timedelta(days=SESSION_EXPIRATION_DAYS)
    
    session = SessionModel(
        session_token=session_token,
        user_id=user_id,
        expires_at=expires_at
    )
    
    db.add(session)
    db.commit()
    db.refresh(session)
    
    logger.info(f"Session created", extra={"session_token": session_token[:8], "user_id": user_id})
    
    return session


def get_session(
    db: DBSession,
    session_token: str
) -> Optional[SessionModel]:
    """
    Get session by token, checking expiration
    
    Args:
        db: Database session
        session_token: Session token to look up
        
    Returns:
        Session object if valid, None if not found or expired
    """
    session = db.query(SessionModel).filter(
        SessionModel.session_token == session_token
    ).first()
    
    if not session:
        return None
    
    # Check expiration
    if datetime.utcnow() > session.expires_at:
        logger.debug(f"Session expired: {session_token[:8]}")
        delete_session(db, session_token)
        return None
    
    # Update last_used_at
    session.last_used_at = datetime.utcnow()
    db.commit()
    
    return session


def get_user_from_session(
    db: DBSession,
    session_token: Optional[str]
) -> Optional[User]:
    """
    Get user from session token
    
    Args:
        db: Database session
        session_token: Session token
        
    Returns:
        User object if session is valid and linked to user, None otherwise
    """
    if not session_token:
        return None
    
    session = get_session(db, session_token)
    if not session or not session.user_id:
        return None
    
    return db.query(User).filter(User.id == session.user_id).first()


def delete_session(
    db: DBSession,
    session_token: str
) -> bool:
    """
    Delete a session (logout)
    
    Args:
        db: Database session
        session_token: Session token to delete
        
    Returns:
        True if session was deleted, False if not found
    """
    session = db.query(SessionModel).filter(
        SessionModel.session_token == session_token
    ).first()
    
    if session:
        db.delete(session)
        db.commit()
        logger.info(f"Session deleted: {session_token[:8]}")
        return True
    
    return False


def cleanup_expired_sessions(db: DBSession) -> int:
    """
    Clean up expired sessions
    
    Args:
        db: Database session
        
    Returns:
        Number of sessions deleted
    """
    now = datetime.utcnow()
    expired = db.query(SessionModel).filter(
        SessionModel.expires_at < now
    ).all()
    
    count = len(expired)
    for session in expired:
        db.delete(session)
    
    db.commit()
    
    if count > 0:
        logger.info(f"Cleaned up {count} expired sessions")
    
    return count
