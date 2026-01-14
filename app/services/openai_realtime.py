"""
OpenAI Realtime API service for voice interactions
"""

import json
from datetime import datetime
from typing import Callable, Any

try:
    import zoneinfo
    HAS_ZONEINFO = True
    HAS_PYTZ = False
except ImportError:
    try:
        from backports import zoneinfo
        HAS_ZONEINFO = True
        HAS_PYTZ = False
    except ImportError:
        try:
            import pytz
            HAS_ZONEINFO = False
            HAS_PYTZ = True
        except ImportError:
            HAS_ZONEINFO = False
            HAS_PYTZ = False

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


def get_session_config() -> dict[str, Any]:
    """
    Get the session configuration for OpenAI Realtime API
    
    Returns:
        Session configuration dictionary to send on connection
    """
    # Get current time in configured timezone
    try:
        if HAS_ZONEINFO:
            tz = zoneinfo.ZoneInfo(settings.timezone)
        elif HAS_PYTZ:
            import pytz
            tz = pytz.timezone(settings.timezone)
        else:
            tz = None
            tz_name = 'UTC'
            current_dt = datetime.now()
        
        if tz:
            current_dt = datetime.now(tz)
            tz_name = settings.timezone
    except Exception:
        try:
            if HAS_ZONEINFO:
                tz = zoneinfo.ZoneInfo('UTC')
            elif HAS_PYTZ:
                import pytz
                tz = pytz.UTC
            else:
                tz = None
            if tz:
                current_dt = datetime.now(tz)
            else:
                current_dt = datetime.now()
            tz_name = 'UTC'
        except Exception:
            current_dt = datetime.now()
            tz_name = 'UTC'
    
    system_instructions = f"""You are a friendly voice assistant that helps users schedule calendar meetings.

CURRENT DATE AND TIME (Timezone: {tz_name}):
- Date: {current_dt.strftime('%Y-%m-%d')}
- Time: {current_dt.strftime('%H:%M:%S %Z')}
- Day: {current_dt.strftime('%A')}
- ISO: {current_dt.isoformat()}
- Timezone: {tz_name}

When user says "tomorrow", add 1 day to current date.
When user says "today", use current date.

YOUR TASK:
1. Greet the user warmly and introduce yourself as their scheduling assistant
2. Ask for their name
3. Ask for the preferred date and time for the meeting
4. Ask for a meeting title (optional but encouraged)
5. ALWAYS confirm all the details before creating the event
6. Only call add_calendar_event AFTER the user confirms the details
7. After creating the event, confirm success to the user

IMPORTANT RULES:
- Be conversational and friendly
- Keep responses concise (this is voice, not text)
- Always convert relative dates (tomorrow, next Monday) to ISO format using the current date above
- When user specifies a time (e.g., "5 PM", "2:30 PM"), interpret it in the current timezone ({tz_name})
- Generate ISO datetime strings WITHOUT timezone suffix (e.g., "2026-01-15T17:00:00" for 5 PM)
- The system will automatically handle timezone conversion
- Confirm before creating any event
- If the user wants to change something, accommodate them before creating the event"""

    tools = [
        {
            "type": "function",
            "name": "add_calendar_event",
            "description": "Create a new event in the user's Google Calendar. Only call this AFTER confirming all details with the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "The title of the calendar event (meeting title)"
                    },
                    "start_time": {
                        "type": "string",
                        "description": f"Start time in ISO 8601 format WITHOUT timezone (e.g., 2026-01-15T17:00:00 for 5 PM in {tz_name}). The system will interpret times in the {tz_name} timezone."
                    },
                    "end_time": {
                        "type": "string",
                        "description": f"End time in ISO 8601 format WITHOUT timezone (e.g., 2026-01-15T18:00:00). If not specified, defaults to 1 hour after start. Times are interpreted in {tz_name} timezone."
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional description for the event"
                    },
                    "attendee_name": {
                        "type": "string",
                        "description": "The name of the person scheduling the meeting"
                    }
                },
                "required": ["summary", "start_time"]
            }
        }
    ]
    
    return {
        "type": "session.update",
        "session": {
            "modalities": ["text", "audio"],
            "instructions": system_instructions,
            "voice": "alloy",
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": "whisper-1"
            },
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500
            },
            "tools": tools,
            "tool_choice": "auto"
        }
    }


def get_websocket_headers() -> dict[str, str]:
    """Get headers for OpenAI WebSocket connection"""
    return {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "OpenAI-Beta": "realtime=v1"
    }


async def handle_function_call(
    call_id: str,
    name: str,
    arguments: str,
    calendar_service: Any,
    session_id: str = None
) -> dict[str, Any]:
    """
    Handle function calls from the OpenAI assistant
    
    Args:
        call_id: Function call ID from OpenAI
        name: Function name
        arguments: JSON string of function arguments
        calendar_service: CalendarService instance
        session_id: Current session ID for logging
        
    Returns:
        Function result dictionary
    """
    logger.info(
        f"Function call received: {name}",
        extra={"session_id": session_id, "call_id": call_id}
    )
    
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError:
        logger.error(f"Invalid function arguments: {arguments}")
        args = {}
    
    if name == "add_calendar_event":
        result = calendar_service.add_event(
            summary=args.get("summary", "Meeting"),
            start_time=args.get("start_time"),
            end_time=args.get("end_time"),
            description=args.get("description"),
            attendee_name=args.get("attendee_name")
        )
        
        logger.info(
            f"Calendar event function result",
            extra={
                "session_id": session_id,
                "success": result.get("success", False),
                "event_id": result.get("event_id")
            }
        )
    else:
        result = {"error": f"Unknown function: {name}"}
        logger.warning(f"Unknown function called: {name}")
    
    return result
