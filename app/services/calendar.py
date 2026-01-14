"""
Google Calendar service for OAuth and event management
Supports multi-user with per-user token storage in database
"""

import os
import pickle
from datetime import datetime, timedelta
from typing import Optional, Any

# Try to import timezone libraries
HAS_ZONEINFO = False
HAS_PYTZ = False

try:
    import zoneinfo
    # Test if zoneinfo works (requires tzdata on Windows)
    try:
        zoneinfo.ZoneInfo('UTC')
        HAS_ZONEINFO = True
    except Exception:
        # zoneinfo available but tzdata missing
        HAS_ZONEINFO = False
except ImportError:
    # Python < 3.9, try backports
    try:
        from backports import zoneinfo
        try:
            zoneinfo.ZoneInfo('UTC')
            HAS_ZONEINFO = True
        except Exception:
            HAS_ZONEINFO = False
    except ImportError:
        HAS_ZONEINFO = False

# Try pytz as fallback
if not HAS_ZONEINFO:
    try:
        import pytz
        pytz.timezone('UTC')  # Test if it works
        HAS_PYTZ = True
    except Exception:
        HAS_PYTZ = False

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request as GoogleRequest
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User
from app.utils.crypto import encrypt_token, decrypt_token
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Allow HTTP for localhost (required for OAuth redirect)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# Google OAuth2 Scopes
# Note: 'openid' is required when using 'userinfo.email'
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'openid',
    'https://www.googleapis.com/auth/userinfo.email'
]


class CalendarService:
    """Service for Google Calendar operations with multi-user support"""
    
    def __init__(self, db: Optional[Session] = None, user_id: Optional[int] = None):
        """
        Initialize CalendarService
        
        Args:
            db: Database session (required for user-specific operations)
            user_id: User ID for user-specific calendar operations
        """
        self.db = db
        self.user_id = user_id
        # Keep legacy token file for backward compatibility during migration
        self.token_file = 'data/token.pickle'
        if self.token_file:
            os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
        
        # Get timezone from settings
        self.timezone = None
        self.timezone_str = 'UTC'
        
        try:
            if HAS_ZONEINFO:
                try:
                    self.timezone = zoneinfo.ZoneInfo(settings.timezone)
                    self.timezone_str = settings.timezone
                    logger.info(f"Using timezone: {self.timezone_str}")
                except Exception as e:
                    logger.warning(f"Failed to load timezone '{settings.timezone}' with zoneinfo: {e}")
                    # Try UTC as fallback
                    try:
                        self.timezone = zoneinfo.ZoneInfo('UTC')
                        self.timezone_str = 'UTC'
                    except Exception:
                        HAS_ZONEINFO = False  # Mark as unavailable
                        logger.warning("zoneinfo not working, trying pytz fallback")
            
            if not self.timezone and HAS_PYTZ:
                try:
                    import pytz
                    self.timezone = pytz.timezone(settings.timezone)
                    self.timezone_str = settings.timezone
                    logger.info(f"Using timezone with pytz: {self.timezone_str}")
                except Exception as e:
                    logger.warning(f"Failed to load timezone '{settings.timezone}' with pytz: {e}")
                    try:
                        import pytz
                        self.timezone = pytz.UTC
                        self.timezone_str = 'UTC'
                    except Exception:
                        self.timezone = None
            
            if not self.timezone:
                logger.warning("No timezone library available, times will be treated as naive/UTC")
                
        except Exception as e:
            logger.error(f"Error initializing timezone: {e}")
            self.timezone = None
            self.timezone_str = 'UTC'
    
    def get_credentials(self, user_id: Optional[int] = None) -> Optional[Credentials]:
        """
        Load Google credentials from database or legacy file
        
        Args:
            user_id: User ID to load credentials for (uses self.user_id if not provided)
            
        Returns:
            Credentials object if found and valid, None otherwise
        """
        target_user_id = user_id or self.user_id
        
        # Try database first if user_id and db are available
        if target_user_id and self.db:
            user = self.db.query(User).filter(User.id == target_user_id).first()
            if user and user.google_token_encrypted:
                try:
                    # Decrypt token
                    decrypted_token = decrypt_token(user.google_token_encrypted)
                    creds = pickle.loads(decrypted_token)
                    
                    # Refresh if expired
                    if creds and creds.valid:
                        return creds
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(GoogleRequest())
                        self.save_credentials(target_user_id, creds)
                        return creds
                except Exception as e:
                    logger.error(f"Error loading credentials from database: {e}")
        
        # Fallback to legacy file (for migration period)
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, 'rb') as token:
                    creds = pickle.load(token)
                    if creds and creds.valid:
                        return creds
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(GoogleRequest())
                        # Try to save to database if we have user_id
                        if target_user_id and self.db:
                            self.save_credentials(target_user_id, creds)
                        else:
                            self._save_credentials_file(creds)
                        return creds
            except Exception as e:
                logger.error(f"Error loading credentials from file: {e}")
        
        return None
    
    def save_credentials(self, user_id: int, creds: Credentials) -> bool:
        """
        Save credentials to database for a specific user
        
        Args:
            user_id: User ID
            creds: Google OAuth credentials
            
        Returns:
            True if saved successfully
        """
        if not self.db:
            logger.error("Database session required to save credentials")
            return False
        
        try:
            user = self.db.query(User).filter(User.id == user_id).first()
            if not user:
                logger.error(f"User not found: {user_id}")
                return False
            
            # Pickle credentials
            token_bytes = pickle.dumps(creds)
            
            # Encrypt token
            encrypted_token = encrypt_token(token_bytes)
            
            # Save to user record
            user.google_token_encrypted = encrypted_token
            user.last_login = datetime.utcnow()
            self.db.commit()
            
            logger.info(f"Credentials saved to database for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error saving credentials to database: {e}")
            self.db.rollback()
            return False
    
    def _save_credentials_file(self, creds: Credentials) -> None:
        """Legacy method: Save credentials to file (for backward compatibility)"""
        try:
            with open(self.token_file, 'wb') as token:
                pickle.dump(creds, token)
            logger.info("Credentials saved to file (legacy)")
        except Exception as e:
            logger.error(f"Error saving credentials to file: {e}")
    
    def get_oauth_flow(self, state: Optional[str] = None) -> Flow:
        """
        Create OAuth flow for authentication
        
        Args:
            state: Optional state parameter to pass through OAuth flow
        """
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [settings.google_redirect_uri]
                }
            },
            scopes=SCOPES
        )
        flow.redirect_uri = settings.google_redirect_uri
        return flow
    
    def get_authorization_url(self, state: Optional[str] = None) -> str:
        """
        Get Google OAuth authorization URL
        
        Args:
            state: Optional state parameter (typically session token)
        """
        flow = self.get_oauth_flow(state=state)
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            prompt='consent',
            state=state
        )
        logger.info("Generated OAuth authorization URL", extra={"has_state": state is not None})
        return auth_url
    
    def exchange_code(
        self,
        authorization_response: str,
        user_id: Optional[int] = None
    ) -> tuple[bool, Optional[Credentials], Optional[str]]:
        """
        Exchange authorization code for tokens
        
        Args:
            authorization_response: Full authorization response URL
            user_id: User ID to save tokens for (if None, returns credentials without saving)
            
        Returns:
            Tuple of (success, credentials, user_email)
        """
        try:
            flow = self.get_oauth_flow()
            flow.fetch_token(authorization_response=authorization_response)
            creds = flow.credentials
            
            # Get user info from credentials
            user_email = None
            try:
                # Try to get email from OAuth2 userinfo API
                service = build('oauth2', 'v2', credentials=creds)
                user_info = service.userinfo().get().execute()
                user_email = user_info.get('email')
                logger.info(f"Retrieved email from userinfo API: {user_email}")
            except Exception as e:
                logger.warning(f"Could not fetch user email from userinfo API: {e}")
                # Fallback 1: Try to extract email from ID token if available
                try:
                    if hasattr(creds, 'id_token') and creds.id_token:
                        import base64
                        import json
                        # Decode JWT (simple base64 decode, no verification needed for email extraction)
                        parts = creds.id_token.split('.')
                        if len(parts) >= 2:
                            # Decode the payload (second part)
                            payload = parts[1]
                            # Add padding if needed
                            payload += '=' * (4 - len(payload) % 4)
                            decoded = base64.urlsafe_b64decode(payload)
                            token_data = json.loads(decoded)
                            user_email = token_data.get('email')
                            if user_email:
                                logger.info(f"Retrieved email from ID token: {user_email}")
                except Exception as e_id:
                    logger.debug(f"Could not extract email from ID token: {e_id}")
                
                # Fallback 2: Try to get email from Calendar API settings
                if not user_email:
                    try:
                        calendar_service = build('calendar', 'v3', credentials=creds)
                        settings = calendar_service.settings().list().execute()
                        # Look for email in settings
                        for setting in settings.get('items', []):
                            if setting.get('id') == 'userEmail':
                                user_email = setting.get('value')
                                logger.info(f"Retrieved email from Calendar settings: {user_email}")
                                break
                    except Exception as e2:
                        logger.warning(f"Could not fetch user email from Calendar settings: {e2}")
            
            # Save to database if user_id provided
            if user_id and self.db:
                self.save_credentials(user_id, creds)
            elif not user_id:
                # Save to legacy file if no user_id (backward compatibility)
                self._save_credentials_file(creds)
            
            logger.info("OAuth tokens exchanged successfully", extra={"user_id": user_id, "email": user_email})
            return True, creds, user_email
        except Exception as e:
            logger.error(f"Error exchanging OAuth code: {e}")
            return False, None, None
    
    def revoke_credentials(self, user_id: Optional[int] = None) -> bool:
        """
        Remove stored credentials for a user
        
        Args:
            user_id: User ID to revoke credentials for
        """
        target_user_id = user_id or self.user_id
        
        if target_user_id and self.db:
            user = self.db.query(User).filter(User.id == target_user_id).first()
            if user:
                user.google_token_encrypted = None
                self.db.commit()
                logger.info(f"Credentials revoked for user {target_user_id}")
                return True
        
        # Also remove legacy file
        if os.path.exists(self.token_file):
            os.remove(self.token_file)
            logger.info("Legacy credentials file removed")
        
        return False
    
    def is_authenticated(self, user_id: Optional[int] = None) -> bool:
        """
        Check if user is authenticated
        
        Args:
            user_id: User ID to check (uses self.user_id if not provided)
        """
        return self.get_credentials(user_id) is not None
    
    def get_service(self, user_id: Optional[int] = None):
        """
        Get authenticated Google Calendar service
        
        Args:
            user_id: User ID to get service for
        """
        creds = self.get_credentials(user_id)
        if creds:
            return build('calendar', 'v3', credentials=creds)
        return None
    
    def add_event(
        self,
        summary: str,
        start_time: str,
        end_time: Optional[str] = None,
        description: Optional[str] = None,
        attendee_name: Optional[str] = None,
        user_id: Optional[int] = None
    ) -> dict[str, Any]:
        """
        Add an event to Google Calendar
        
        Args:
            summary: Event title
            start_time: Start time in ISO format
            end_time: End time in ISO format (defaults to 1 hour after start)
            description: Event description
            attendee_name: Name of the person scheduling
            user_id: User ID to create event for (uses self.user_id if not provided)
            
        Returns:
            dict with success status and event details or error
        """
        target_user_id = user_id or self.user_id
        service = self.get_service(target_user_id)
        
        if not service:
            logger.warning("Calendar event creation failed: not authenticated", extra={"user_id": target_user_id})
            return {"error": "Google Calendar not authenticated. Please connect your calendar first."}
        
        try:
            # Parse start time
            start_dt = self._parse_datetime(start_time)
            if not start_dt:
                start_dt = datetime.now() + timedelta(hours=1)
                logger.warning(f"Invalid start_time '{start_time}', defaulting to 1 hour from now")
            
            # Parse or calculate end time
            if end_time:
                end_dt = self._parse_datetime(end_time)
                if not end_dt:
                    end_dt = start_dt + timedelta(hours=1)
            else:
                end_dt = start_dt + timedelta(hours=1)
            
            # Build description
            event_description = description or ""
            if attendee_name:
                event_description = f"Meeting with {attendee_name}\n{event_description}".strip()
            
            # Create event with proper timezone
            # Convert to the configured timezone if not already timezone-aware
            if start_dt.tzinfo is None:
                if self.timezone:
                    if HAS_PYTZ and not HAS_ZONEINFO:
                        import pytz
                        start_dt = self.timezone.localize(start_dt)
                    else:
                        start_dt = start_dt.replace(tzinfo=self.timezone)
                else:
                    # No timezone available, assume UTC
                    from datetime import timezone as dt_timezone
                    start_dt = start_dt.replace(tzinfo=dt_timezone.utc)
                    logger.warning("No timezone configured, treating naive datetime as UTC")
            if end_dt.tzinfo is None:
                if self.timezone:
                    if HAS_PYTZ and not HAS_ZONEINFO:
                        import pytz
                        end_dt = self.timezone.localize(end_dt)
                    else:
                        end_dt = end_dt.replace(tzinfo=self.timezone)
                else:
                    # No timezone available, assume UTC
                    from datetime import timezone as dt_timezone
                    end_dt = end_dt.replace(tzinfo=dt_timezone.utc)
            
            # Format datetime for Google Calendar API
            # Google Calendar expects ISO format with timezone
            start_iso = start_dt.isoformat()
            end_iso = end_dt.isoformat()
            
            # Use timezone string or UTC as fallback
            event_timezone = self.timezone_str if self.timezone_str else 'UTC'
            
            event = {
                'summary': summary,
                'description': event_description,
                'start': {'dateTime': start_iso, 'timeZone': event_timezone},
                'end': {'dateTime': end_iso, 'timeZone': event_timezone},
            }
            
            logger.info(
                f"Creating event with timezone {event_timezone}: {start_iso} to {end_iso}",
                extra={"user_id": target_user_id}
            )
            
            created_event = service.events().insert(
                calendarId='primary',
                body=event
            ).execute()
            
            result = {
                "success": True,
                "event_id": created_event.get('id'),
                "summary": created_event.get('summary'),
                "start": created_event.get('start', {}).get('dateTime'),
                "end": created_event.get('end', {}).get('dateTime'),
                "html_link": created_event.get('htmlLink'),
                "message": f"Event '{summary}' created successfully!"
            }
            
            logger.info(
                f"Calendar event created: {summary}",
                extra={"event_id": result["event_id"], "user_id": target_user_id}
            )
            return result
            
        except Exception as e:
            logger.error(f"Error creating calendar event: {e}", extra={"user_id": target_user_id})
            return {"error": f"Failed to create calendar event: {str(e)}"}
    
    def _parse_datetime(self, dt_string: str) -> Optional[datetime]:
        """
        Parse datetime string to timezone-aware datetime object.
        If the datetime is naive (no timezone), assumes it's in the configured timezone.
        """
        if not dt_string:
            return None
        try:
            # Handle various ISO format variations
            dt_string = dt_string.replace('Z', '+00:00')
            
            # Try to parse as timezone-aware first
            if '+' in dt_string or dt_string.endswith('+00:00'):
                # Has timezone info
                dt = datetime.fromisoformat(dt_string)
                return dt
            
            # Check if it has timezone offset in the middle (e.g., 2026-01-15T17:00:00-05:00)
            if '-' in dt_string[10:] and dt_string.count('-') >= 3:
                # Has timezone offset
                dt = datetime.fromisoformat(dt_string)
                return dt
            
            # Naive datetime - assume it's in the configured timezone
            naive_dt = datetime.fromisoformat(dt_string)
            # Make it timezone-aware by localizing to configured timezone
            if self.timezone:
                if HAS_PYTZ and not HAS_ZONEINFO:
                    # pytz requires localize() instead of replace()
                    import pytz
                    aware_dt = self.timezone.localize(naive_dt)
                else:
                    aware_dt = naive_dt.replace(tzinfo=self.timezone)
            else:
                # No timezone available, assume UTC
                aware_dt = naive_dt.replace(tzinfo=None)
            logger.debug(f"Parsed naive datetime '{dt_string}' as {aware_dt} in timezone {self.timezone_str}")
            return aware_dt
            
        except Exception as e:
            logger.error(f"Error parsing datetime '{dt_string}': {e}")
            return None


# Legacy singleton instance (for backward compatibility)
# New code should create CalendarService(db=db, user_id=user_id) instances
# Note: This is only used in legacy code paths. New code should create instances with db and user_id.
# We don't initialize at module level to avoid import-time errors
def get_legacy_calendar_service():
    """Get a CalendarService instance for legacy code (no db/user_id)"""
    return CalendarService()
