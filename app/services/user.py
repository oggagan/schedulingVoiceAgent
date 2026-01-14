"""
User service for managing users and user sessions
"""

from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session

from app.models import User
from app.utils.session import create_session, get_user_from_session, delete_session
from app.utils.logger import get_logger

logger = get_logger(__name__)


class UserService:
    """Service for user management"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_or_create_user(self, email: str) -> User:
        """
        Get existing user by email or create new user
        
        Args:
            email: User's email address
            
        Returns:
            User object
        """
        user = self.db.query(User).filter(User.email == email).first()
        
        if not user:
            user = User(email=email)
            self.db.add(user)
            self.db.commit()
            self.db.refresh(user)
            logger.info(f"New user created: {email}")
        else:
            # Update last login
            user.last_login = datetime.utcnow()
            self.db.commit()
            logger.debug(f"Existing user logged in: {email}")
        
        return user
    
    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID"""
        return self.db.query(User).filter(User.id == user_id).first()
    
    def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email"""
        return self.db.query(User).filter(User.email == email).first()
    
    def get_user_from_session_token(self, session_token: Optional[str]) -> Optional[User]:
        """
        Get user from session token
        
        Args:
            session_token: Session token from cookie
            
        Returns:
            User object if session is valid, None otherwise
        """
        return get_user_from_session(self.db, session_token)
    
    def create_user_session(self, user_id: Optional[int] = None) -> str:
        """
        Create a new session for a user
        
        Args:
            user_id: Optional user ID (None for anonymous session)
            
        Returns:
            Session token string
        """
        session = create_session(self.db, user_id=user_id)
        return session.session_token
    
    def delete_user_session(self, session_token: str) -> bool:
        """
        Delete a user session (logout)
        
        Args:
            session_token: Session token to delete
            
        Returns:
            True if deleted, False if not found
        """
        return delete_session(self.db, session_token)
    
    def update_user_token(self, user_id: int, encrypted_token: str) -> bool:
        """
        Update user's encrypted Google OAuth token
        
        Args:
            user_id: User ID
            encrypted_token: Encrypted token string
            
        Returns:
            True if updated successfully
        """
        user = self.get_user_by_id(user_id)
        if not user:
            logger.error(f"User not found: {user_id}")
            return False
        
        user.google_token_encrypted = encrypted_token
        user.last_login = datetime.utcnow()
        self.db.commit()
        
        logger.info(f"User token updated: {user_id}")
        return True
