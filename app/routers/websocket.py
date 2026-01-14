"""
WebSocket router for real-time voice communication with multi-user support
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from sqlalchemy.orm import Session
import websockets

from app.config import settings
from app.database import get_db
from app.services.calendar import CalendarService
from app.services.conversation import ConversationService
from app.services.user import UserService
from app.services.openai_realtime import (
    get_session_config,
    get_websocket_headers,
    handle_function_call
)
from app.models import ConversationStatus
from app.utils.logger import get_logger, get_context_logger

logger = get_logger(__name__)

router = APIRouter(tags=["WebSocket"])


def get_session_from_websocket(client_ws: WebSocket) -> str:
    """Extract session token from WebSocket cookies or query params"""
    # Try cookies first
    cookie_header = client_ws.headers.get("cookie", "")
    if cookie_header:
        for cookie in cookie_header.split(";"):
            cookie = cookie.strip()
            if cookie.startswith("session="):
                return cookie.split("=", 1)[1]
    
    # Try query parameter as fallback
    query_string = client_ws.url.query
    if query_string:
        params = dict(param.split("=") for param in query_string.split("&") if "=" in param)
        if "session" in params:
            return params["session"]
    
    return None


@router.websocket("/ws")
async def websocket_endpoint(
    client_ws: WebSocket,
    db: Session = Depends(get_db)
):
    """
    WebSocket endpoint for real-time voice communication with OpenAI Realtime API.
    
    **Connection Flow:**
    1. Client connects via WebSocket
    2. Server extracts user session from cookie
    3. Server establishes connection to OpenAI Realtime API
    4. Audio is relayed bidirectionally:
       - Browser → Server → OpenAI (user speech)
       - OpenAI → Server → Browser (assistant response)
    5. Function calls are executed server-side using user's calendar
    6. Conversation is stored in database linked to user
    
    **Message Types:**
    - `audio` - Base64 encoded PCM16 audio chunks
    - `start` - Initialize conversation session
    - `stop` - End conversation session
    
    **Server Messages:**
    - `status` - Connection status updates
    - `audio` - Assistant audio response
    - `transcript` - Real-time transcription
    - `function_result` - Calendar event creation results
    - `error` - Error messages
    
    **Example:**
    ```javascript
    const ws = new WebSocket('ws://localhost:8000/ws');
    ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'start' }));
    };
    ```
    """
    await client_ws.accept()
    
    # Get client info
    client_ip = client_ws.client.host if client_ws.client else "unknown"
    user_agent = client_ws.headers.get("user-agent", "unknown")
    
    # Extract session token
    session_token = get_session_from_websocket(client_ws)
    
    # Get user from session
    user_service = UserService(db)
    user = user_service.get_user_from_session_token(session_token) if session_token else None
    user_id = user.id if user else None
    
    # Create conversation session linked to user
    conversation_service = ConversationService(db)
    conversation = conversation_service.create_conversation(
        client_ip=client_ip,
        user_agent=user_agent,
        user_id=user_id
    )
    session_id = conversation.session_id
    
    # Create user-specific calendar service
    user_calendar_service = CalendarService(db=db, user_id=user_id)
    
    # Create context logger
    ctx_logger = get_context_logger(
        __name__,
        session_id=session_id,
        client_ip=client_ip,
        user_id=user_id
    )
    
    ctx_logger.info("WebSocket connection established", extra={"user_email": user.email if user else None})
    
    # Validate API key
    if not settings.openai_api_key:
        ctx_logger.error("OpenAI API key not configured")
        await client_ws.send_json({
            "type": "error",
            "message": "OpenAI API key not configured"
        })
        await client_ws.close()
        conversation_service.end_conversation(session_id, ConversationStatus.ERROR)
        return
    
    # Check Google Calendar auth for this user
    is_authenticated = user_calendar_service.is_authenticated(user_id) if user_id else False
    await client_ws.send_json({
        "type": "auth_status",
        "authenticated": is_authenticated,
        "email": user.email if user and is_authenticated else None
    })
    
    try:
        async with websockets.connect(
            settings.openai_realtime_url,
            additional_headers=get_websocket_headers(),
            ping_interval=20,
            ping_timeout=20
        ) as openai_ws:
            
            ctx_logger.info("Connected to OpenAI Realtime API")
            
            # Send initial status
            await client_ws.send_json({
                "type": "status",
                "status": "connected",
                "message": "Connected to OpenAI Realtime API"
            })
            
            async def relay_to_openai():
                """Relay messages from browser to OpenAI"""
                try:
                    while True:
                        data = await client_ws.receive_text()
                        msg = json.loads(data)
                        
                        if msg.get("type") == "audio":
                            # Relay audio to OpenAI
                            await openai_ws.send(json.dumps({
                                "type": "input_audio_buffer.append",
                                "audio": msg.get("audio", "")
                            }))
                        elif msg.get("type") == "start":
                            # Send session config and start response
                            ctx_logger.info("Session started by user")
                            await openai_ws.send(json.dumps(get_session_config()))
                        elif msg.get("type") == "stop":
                            # Commit current audio buffer
                            await openai_ws.send(json.dumps({
                                "type": "input_audio_buffer.commit"
                            }))
                except WebSocketDisconnect:
                    ctx_logger.info("Browser WebSocket disconnected")
                except Exception as e:
                    ctx_logger.error(f"Browser->OpenAI relay error: {e}")
            
            async def relay_to_browser():
                """Relay messages from OpenAI to browser"""
                try:
                    async for message in openai_ws:
                        event = json.loads(message)
                        event_type = event.get("type", "")
                        
                        # Session events
                        if event_type == "session.created":
                            ctx_logger.debug("OpenAI session created")
                            await client_ws.send_json({
                                "type": "status",
                                "status": "ready",
                                "message": "Session ready"
                            })
                        
                        elif event_type == "session.updated":
                            ctx_logger.debug("OpenAI session updated, triggering initial response")
                            # Trigger initial response
                            await openai_ws.send(json.dumps({
                                "type": "response.create",
                                "response": {"modalities": ["text", "audio"]}
                            }))
                        
                        # Response events
                        elif event_type == "response.created":
                            await client_ws.send_json({
                                "type": "status",
                                "status": "speaking",
                                "message": "Assistant speaking"
                            })
                        
                        elif event_type == "response.done":
                            response = event.get("response", {})
                            output = response.get("output", [])
                            
                            # Check for function calls
                            for item in output:
                                if item.get("type") == "function_call":
                                    call_id = item.get("call_id")
                                    name = item.get("name")
                                    arguments = item.get("arguments", "{}")
                                    
                                    # Execute function with user-specific calendar service
                                    result = await handle_function_call(
                                        call_id, name, arguments,
                                        user_calendar_service,
                                        session_id
                                    )
                                    
                                    # Record calendar event in database
                                    if name == "add_calendar_event" and result.get("success"):
                                        args = json.loads(arguments)
                                        try:
                                            # Parse start time - handle timezone-aware and naive datetimes
                                            start_time_str = args.get("start_time", "")
                                            if start_time_str:
                                                start_time_str = start_time_str.replace('Z', '+00:00')
                                                start_dt = datetime.fromisoformat(start_time_str)
                                            else:
                                                # Fallback to result data
                                                start_time_str = result.get("start", "")
                                                if start_time_str:
                                                    start_time_str = start_time_str.replace('Z', '+00:00')
                                                    start_dt = datetime.fromisoformat(start_time_str)
                                                else:
                                                    ctx_logger.error("No start_time found in event result")
                                                    start_dt = None
                                            
                                            # Parse end time
                                            end_str = args.get("end_time") or result.get("end")
                                            if end_str:
                                                end_str = end_str.replace('Z', '+00:00')
                                                end_dt = datetime.fromisoformat(end_str)
                                            elif start_dt:
                                                end_dt = start_dt + timedelta(hours=1)
                                            else:
                                                end_dt = None
                                            
                                            if start_dt and end_dt:
                                                # Convert to UTC for database storage if timezone-aware
                                                if start_dt.tzinfo:
                                                    start_dt = start_dt.astimezone(timezone.utc).replace(tzinfo=None)
                                                if end_dt.tzinfo:
                                                    end_dt = end_dt.astimezone(timezone.utc).replace(tzinfo=None)
                                                
                                                conversation_service.add_calendar_event(
                                                    session_id=session_id,
                                                    google_event_id=result.get("event_id"),
                                                    summary=result.get("summary") or args.get("summary", "Meeting"),
                                                    start_time=start_dt,
                                                    end_time=end_dt,
                                                    description=args.get("description"),
                                                    attendee_name=args.get("attendee_name"),
                                                    html_link=result.get("html_link")
                                                )
                                                ctx_logger.info(f"Calendar event recorded: {result.get('event_id')}")
                                            else:
                                                ctx_logger.error("Could not parse start_time or end_time for calendar event")
                                        except Exception as e:
                                            ctx_logger.error(f"Error recording calendar event: {e}", exc_info=True)
                                    
                                    # Send result to browser
                                    await client_ws.send_json({
                                        "type": "function_result",
                                        "name": name,
                                        "result": result
                                    })
                                    
                                    # Send result back to OpenAI
                                    await openai_ws.send(json.dumps({
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "function_call_output",
                                            "call_id": call_id,
                                            "output": json.dumps(result)
                                        }
                                    }))
                                    
                                    # Request acknowledgment response
                                    await openai_ws.send(json.dumps({
                                        "type": "response.create"
                                    }))
                            
                            await client_ws.send_json({
                                "type": "status",
                                "status": "listening",
                                "message": "Listening"
                            })
                        
                        # Audio events
                        elif event_type == "response.audio.delta":
                            delta = event.get("delta", "")
                            if delta:
                                await client_ws.send_json({
                                    "type": "audio",
                                    "audio": delta
                                })
                        
                        # Transcript events
                        elif event_type == "response.audio_transcript.delta":
                            transcript = event.get("delta", "")
                            if transcript:
                                await client_ws.send_json({
                                    "type": "transcript",
                                    "role": "assistant",
                                    "delta": transcript
                                })
                        
                        elif event_type == "response.audio_transcript.done":
                            transcript = event.get("transcript", "")
                            await client_ws.send_json({
                                "type": "transcript_done",
                                "role": "assistant",
                                "text": transcript
                            })
                            # Store assistant message
                            if transcript:
                                conversation_service.add_message(
                                    session_id, "assistant", transcript
                                )
                        
                        elif event_type == "conversation.item.input_audio_transcription.completed":
                            transcript = event.get("transcript", "")
                            if transcript:
                                await client_ws.send_json({
                                    "type": "transcript_done",
                                    "role": "user",
                                    "text": transcript
                                })
                                # Store user message
                                conversation_service.add_message(
                                    session_id, "user", transcript
                                )
                        
                        # Error handling
                        elif event_type == "error":
                            error = event.get("error", {})
                            error_msg = error.get("message", "Unknown error")
                            ctx_logger.error(f"OpenAI error: {error_msg}")
                            await client_ws.send_json({
                                "type": "error",
                                "message": error_msg
                            })
                
                except websockets.exceptions.ConnectionClosed as e:
                    ctx_logger.info(f"OpenAI WebSocket closed: {e}")
                except Exception as e:
                    ctx_logger.error(f"OpenAI->Browser relay error: {e}")
            
            # Run both relay tasks concurrently
            await asyncio.gather(
                relay_to_openai(),
                relay_to_browser()
            )
    
    except websockets.exceptions.InvalidStatusCode as e:
        error_msg = "Failed to connect to OpenAI"
        if "401" in str(e):
            error_msg = "Invalid OpenAI API key"
        elif "429" in str(e):
            error_msg = "Rate limited - please wait"
        ctx_logger.error(f"OpenAI connection failed: {error_msg}")
        await client_ws.send_json({
            "type": "error",
            "message": error_msg
        })
        conversation_service.end_conversation(session_id, ConversationStatus.ERROR)
    except Exception as e:
        ctx_logger.error(f"WebSocket error: {e}")
        await client_ws.send_json({
            "type": "error",
            "message": str(e)
        })
        conversation_service.end_conversation(session_id, ConversationStatus.ERROR)
    finally:
        # End conversation
        conversation_service.end_conversation(session_id, ConversationStatus.COMPLETED)
        ctx_logger.info("WebSocket session ended")
        try:
            await client_ws.close()
        except:
            pass
