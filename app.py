"""
Voice Scheduling Agent - FastAPI Web Application
WebSocket relay between browser and OpenAI Realtime API with Google Calendar integration
"""

import asyncio
import base64
import json
import os
import pickle
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import websockets

# Google Calendar
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request as GoogleRequest

# ==================== CONFIGURATION ====================

# Load from environment variables - no hardcoded fallbacks for security
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError(
        "OPENAI_API_KEY environment variable is required. "
        "Please set it in your .env file or environment."
    )

REALTIME_API_URL = os.environ.get(
    "OPENAI_REALTIME_URL",
    "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17"
)

# Google OAuth2 Credentials - must be set via environment variables
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
if not GOOGLE_CLIENT_ID:
    raise ValueError(
        "GOOGLE_CLIENT_ID environment variable is required. "
        "Please set it in your .env file or environment."
    )

GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
if not GOOGLE_CLIENT_SECRET:
    raise ValueError(
        "GOOGLE_CLIENT_SECRET environment variable is required. "
        "Please set it in your .env file or environment."
    )

GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI",
    "http://localhost:8000/auth/callback"
)
SCOPES = ['https://www.googleapis.com/auth/calendar']

# Allow HTTP for localhost
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# ==================== FASTAPI APP ====================

app = FastAPI(title="Voice Scheduling Agent")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


# ==================== GOOGLE CALENDAR ====================

def get_google_credentials():
    """Load Google credentials from token.pickle"""
    token_file = 'token.pickle'
    if os.path.exists(token_file):
        with open(token_file, 'rb') as token:
            creds = pickle.load(token)
            if creds and creds.valid:
                return creds
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(GoogleRequest())
                with open(token_file, 'wb') as token:
                    pickle.dump(creds, token)
                return creds
    return None


def get_calendar_service():
    """Get authenticated Google Calendar service"""
    creds = get_google_credentials()
    if creds:
        return build('calendar', 'v3', credentials=creds)
    return None


def add_calendar_event(summary: str, start_time: str, end_time: str = None,
                       description: str = None, attendee_name: str = None):
    """Add an event to Google Calendar"""
    service = get_calendar_service()
    if not service:
        return {"error": "Google Calendar not authenticated. Please connect your calendar first."}
    
    try:
        # Parse start_time
        try:
            start_time_clean = start_time.replace('Z', '+00:00')
            if '+' not in start_time_clean and '-' not in start_time_clean[10:]:
                start_dt = datetime.fromisoformat(start_time_clean)
            else:
                start_dt = datetime.fromisoformat(start_time_clean)
        except Exception:
            start_dt = datetime.now() + timedelta(hours=1)
        
        # Calculate end time
        if end_time:
            try:
                end_time_clean = end_time.replace('Z', '+00:00')
                if '+' not in end_time_clean and '-' not in end_time_clean[10:]:
                    end_dt = datetime.fromisoformat(end_time_clean)
                else:
                    end_dt = datetime.fromisoformat(end_time_clean)
            except Exception:
                end_dt = start_dt + timedelta(hours=1)
        else:
            end_dt = start_dt + timedelta(hours=1)
        
        # Build description
        event_description = description or ""
        if attendee_name:
            event_description = f"Meeting with {attendee_name}\n{event_description}".strip()
        
        event = {
            'summary': summary,
            'description': event_description,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'UTC'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'UTC'},
        }
        
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        
        return {
            "success": True,
            "event_id": created_event.get('id'),
            "summary": created_event.get('summary'),
            "start": created_event.get('start', {}).get('dateTime'),
            "html_link": created_event.get('htmlLink'),
            "message": f"Event '{summary}' created successfully!"
        }
    except Exception as e:
        return {"error": f"Failed to create calendar event: {str(e)}"}


# ==================== ROUTES ====================

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main HTML page"""
    with open("static/index.html", "r") as f:
        return HTMLResponse(content=f.read())


@app.get("/auth/status")
async def auth_status():
    """Check if Google Calendar is authenticated"""
    creds = get_google_credentials()
    return JSONResponse({
        "authenticated": creds is not None,
        "message": "Connected to Google Calendar" if creds else "Not connected"
    })


@app.get("/auth/login")
async def auth_login():
    """Initiate Google OAuth flow"""
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [GOOGLE_REDIRECT_URI]
            }
        },
        scopes=SCOPES
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    
    auth_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent'
    )
    
    return RedirectResponse(url=auth_url)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    """Handle Google OAuth callback"""
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    
    if error:
        return RedirectResponse(url="/?auth=error&message=" + error)
    
    if not code:
        return RedirectResponse(url="/?auth=error&message=no_code")
    
    try:
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [GOOGLE_REDIRECT_URI]
                }
            },
            scopes=SCOPES
        )
        flow.redirect_uri = GOOGLE_REDIRECT_URI
        
        # Exchange code for tokens using full authorization response URL
        # This handles scope changes from Google properly
        authorization_response = str(request.url)
        flow.fetch_token(authorization_response=authorization_response)
        creds = flow.credentials
        
        # Save credentials
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
        
        return RedirectResponse(url="/?auth=success")
    
    except Exception as e:
        return RedirectResponse(url=f"/?auth=error&message={str(e)}")


@app.get("/auth/logout")
async def auth_logout():
    """Remove Google Calendar authentication"""
    if os.path.exists('token.pickle'):
        os.remove('token.pickle')
    return RedirectResponse(url="/?auth=logged_out")


# ==================== WEBSOCKET ====================

def get_session_config():
    """Get the session configuration for OpenAI Realtime API"""
    current_dt = datetime.now()
    
    system_instructions = f"""You are a friendly voice assistant that helps users schedule calendar meetings.

CURRENT DATE AND TIME:
- Date: {current_dt.strftime('%Y-%m-%d')}
- Time: {current_dt.strftime('%H:%M:%S')}
- Day: {current_dt.strftime('%A')}
- ISO: {current_dt.isoformat()}

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
                        "description": "Start time in ISO 8601 format (e.g., 2026-01-15T14:00:00)"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "End time in ISO 8601 format. If not specified, defaults to 1 hour after start."
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


async def handle_function_call(call_id: str, name: str, arguments: str):
    """Handle function calls from the assistant"""
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError:
        args = {}
    
    if name == "add_calendar_event":
        result = add_calendar_event(
            summary=args.get("summary", "Meeting"),
            start_time=args.get("start_time"),
            end_time=args.get("end_time"),
            description=args.get("description"),
            attendee_name=args.get("attendee_name")
        )
    else:
        result = {"error": f"Unknown function: {name}"}
    
    return result


@app.websocket("/ws")
async def websocket_endpoint(client_ws: WebSocket):
    """WebSocket endpoint that relays audio between browser and OpenAI"""
    await client_ws.accept()
    
    if not OPENAI_API_KEY:
        await client_ws.send_json({
            "type": "error",
            "message": "OpenAI API key not configured"
        })
        await client_ws.close()
        return
    
    # Check Google Calendar auth
    creds = get_google_credentials()
    await client_ws.send_json({
        "type": "auth_status",
        "authenticated": creds is not None
    })
    
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1"
    }
    
    try:
        async with websockets.connect(
            REALTIME_API_URL,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20
        ) as openai_ws:
            
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
                            await openai_ws.send(json.dumps(get_session_config()))
                        elif msg.get("type") == "stop":
                            # Commit current audio buffer
                            await openai_ws.send(json.dumps({
                                "type": "input_audio_buffer.commit"
                            }))
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    print(f"[Browser->OpenAI Error] {e}")
            
            async def relay_to_browser():
                """Relay messages from OpenAI to browser"""
                try:
                    async for message in openai_ws:
                        event = json.loads(message)
                        event_type = event.get("type", "")
                        
                        # Session events
                        if event_type == "session.created":
                            await client_ws.send_json({
                                "type": "status",
                                "status": "ready",
                                "message": "Session ready"
                            })
                        
                        elif event_type == "session.updated":
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
                                    
                                    # Execute function
                                    result = await handle_function_call(call_id, name, arguments)
                                    
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
                        
                        elif event_type == "conversation.item.input_audio_transcription.completed":
                            transcript = event.get("transcript", "")
                            if transcript:
                                await client_ws.send_json({
                                    "type": "transcript_done",
                                    "role": "user",
                                    "text": transcript
                                })
                        
                        # Error handling
                        elif event_type == "error":
                            error = event.get("error", {})
                            await client_ws.send_json({
                                "type": "error",
                                "message": error.get("message", "Unknown error")
                            })
                
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    print(f"[OpenAI->Browser Error] {e}")
            
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
        await client_ws.send_json({
            "type": "error",
            "message": error_msg
        })
    except Exception as e:
        await client_ws.send_json({
            "type": "error",
            "message": str(e)
        })
    finally:
        try:
            await client_ws.close()
        except:
            pass


# ==================== MAIN ====================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
