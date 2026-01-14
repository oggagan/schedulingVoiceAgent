"""
Conversation service for storing and retrieving conversation history
"""

import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session

from app.models import Conversation, Message, CalendarEvent, ConversationStatus
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ConversationService:
    """Service for managing conversation history"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_conversation(
        self,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        user_id: Optional[int] = None
    ) -> Conversation:
        """Create a new conversation session"""
        session_id = str(uuid.uuid4())
        
        conversation = Conversation(
            session_id=session_id,
            user_id=user_id,
            client_ip=client_ip,
            user_agent=user_agent,
            status=ConversationStatus.ACTIVE
        )
        
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        
        logger.info(
            f"Conversation created",
            extra={"session_id": session_id, "client_ip": client_ip}
        )
        
        return conversation
    
    def get_conversation(self, session_id: str) -> Optional[Conversation]:
        """Get conversation by session ID"""
        return self.db.query(Conversation).filter(
            Conversation.session_id == session_id
        ).first()
    
    def get_conversation_by_id(self, conversation_id: int) -> Optional[Conversation]:
        """Get conversation by ID"""
        return self.db.query(Conversation).filter(
            Conversation.id == conversation_id
        ).first()
    
    def list_conversations(
        self,
        limit: int = 50,
        offset: int = 0,
        user_id: Optional[int] = None
    ) -> List[Conversation]:
        """List conversations with pagination"""
        query = self.db.query(Conversation)
        
        if user_id:
            query = query.filter(Conversation.user_id == user_id)
        
        return query.order_by(
            Conversation.started_at.desc()
        ).offset(offset).limit(limit).all()
    
    def end_conversation(
        self,
        session_id: str,
        status: ConversationStatus = ConversationStatus.COMPLETED
    ) -> Optional[Conversation]:
        """End a conversation session"""
        conversation = self.get_conversation(session_id)
        if conversation:
            conversation.status = status
            conversation.ended_at = datetime.utcnow()
            self.db.commit()
            
            logger.info(
                f"Conversation ended",
                extra={"session_id": session_id, "status": status.value}
            )
        
        return conversation
    
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str
    ) -> Optional[Message]:
        """Add a message to a conversation"""
        conversation = self.get_conversation(session_id)
        if not conversation:
            logger.warning(f"Conversation not found: {session_id}")
            return None
        
        message = Message(
            conversation_id=conversation.id,
            role=role,
            content=content
        )
        
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        
        logger.debug(
            f"Message added to conversation",
            extra={"session_id": session_id, "role": role}
        )
        
        return message
    
    def get_messages(
        self,
        session_id: str,
        limit: int = 100
    ) -> List[Message]:
        """Get messages for a conversation"""
        conversation = self.get_conversation(session_id)
        if not conversation:
            return []
        
        return self.db.query(Message).filter(
            Message.conversation_id == conversation.id
        ).order_by(Message.timestamp.asc()).limit(limit).all()
    
    def add_calendar_event(
        self,
        session_id: str,
        google_event_id: str,
        summary: str,
        start_time: datetime,
        end_time: datetime,
        description: Optional[str] = None,
        attendee_name: Optional[str] = None,
        html_link: Optional[str] = None
    ) -> Optional[CalendarEvent]:
        """Record a calendar event created in a conversation"""
        conversation = self.get_conversation(session_id)
        if not conversation:
            logger.warning(f"Conversation not found for calendar event: {session_id}")
            return None
        
        event = CalendarEvent(
            conversation_id=conversation.id,
            google_event_id=google_event_id,
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            description=description,
            attendee_name=attendee_name,
            html_link=html_link
        )
        
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        
        logger.info(
            f"Calendar event recorded",
            extra={
                "session_id": session_id,
                "event_id": google_event_id,
                "summary": summary
            }
        )
        
        return event
    
    def list_calendar_events(
        self,
        limit: int = 50,
        offset: int = 0,
        user_id: Optional[int] = None
    ) -> List[CalendarEvent]:
        """List calendar events, optionally filtered by user"""
        query = self.db.query(CalendarEvent)
        
        # Filter by user if provided
        if user_id:
            query = query.join(Conversation).filter(Conversation.user_id == user_id)
        
        return query.order_by(
            CalendarEvent.created_at.desc()
        ).offset(offset).limit(limit).all()
    
    def get_conversation_stats(self, user_id: Optional[int] = None) -> dict:
        """
        Get conversation statistics
        
        Args:
            user_id: Optional user ID to filter statistics by user
        """
        query = self.db.query(Conversation)
        events_query = self.db.query(CalendarEvent)
        
        # Filter by user if provided
        if user_id:
            query = query.filter(Conversation.user_id == user_id)
            events_query = events_query.join(Conversation).filter(Conversation.user_id == user_id)
        
        total = query.count()
        active = query.filter(Conversation.status == ConversationStatus.ACTIVE).count()
        completed = query.filter(Conversation.status == ConversationStatus.COMPLETED).count()
        errors = query.filter(Conversation.status == ConversationStatus.ERROR).count()
        total_events = events_query.count()
        
        # Calculate total messages
        total_messages = 0
        if user_id:
            conversations = query.all()
            total_messages = sum(len(conv.messages) for conv in conversations)
        else:
            # For all users, count all messages
            total_messages = self.db.query(Message).count()
        
        return {
            "total_conversations": total,
            "active": active,
            "completed": completed,
            "errors": errors,
            "total_calendar_events": total_events,
            "total_messages": total_messages
        }
