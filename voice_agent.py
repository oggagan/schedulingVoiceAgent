"""
Voice Scheduling Agent with OpenAI Realtime API and Google Calendar Integration
Uses laptop microphone for real-time voice interaction

Required packages:
pip install websockets pyaudio google-auth google-auth-oauthlib google-api-python-client
"""

import asyncio
import base64
import json
import pyaudio
import os
import pickle
from datetime import datetime, timedelta

# Google Calendar
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

import websockets

# ==================== CONFIGURATION ====================

# OpenAI Realtime API
# Load from environment variables - no hardcoded values for security
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
GOOGLE_REDIRECT_URI = "http://localhost:8000/auth/callback"
SCOPES = ['https://www.googleapis.com/auth/calendar']

# Audio settings for OpenAI Realtime API (24kHz PCM16 mono)
SAMPLE_RATE = 24000
CHUNK_SIZE = 4800  # 0.2 seconds of audio at 24kHz
FORMAT = pyaudio.paInt16
CHANNELS = 1


class VoiceSchedulingAgent:
    def __init__(self):
        self.audio = pyaudio.PyAudio()
        self.calendar_service = None
        self.ws = None
        self.is_playing = False
        self.audio_buffer = bytearray()
        self.playback_stream = None
        self.response_in_progress = False
        
    def authenticate_google(self):
        """Authenticate with Google Calendar API"""
        # Allow HTTP for localhost (required for OAuth redirect)
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
        
        creds = None
        token_file = 'token.pickle'
        
        # Load existing token if available
        if os.path.exists(token_file):
            with open(token_file, 'rb') as token:
                creds = pickle.load(token)
        
        # If no valid credentials, do OAuth flow
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
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
                
                # Get authorization URL
                auth_url, _ = flow.authorization_url(prompt='consent')
                print(f'\nPlease visit this URL to authorize the application:\n{auth_url}')
                print('\nAfter authorization, you will be redirected. Copy the full redirect URL and paste it here:')
                redirect_response = input('Enter the full redirect URL: ')
                
                # Extract authorization code from redirect URL
                flow.fetch_token(authorization_response=redirect_response)
                creds = flow.credentials
            
            # Save credentials for future use
            with open(token_file, 'wb') as token:
                pickle.dump(creds, token)
        
        self.calendar_service = build('calendar', 'v3', credentials=creds)
        print("Google Calendar authenticated successfully!")
        return True
    
    def add_calendar_event(self, summary: str, start_time: str, end_time: str = None, 
                          description: str = None, attendee_name: str = None):
        """Add an event to Google Calendar"""
        if not self.calendar_service:
            return {"error": "Google Calendar not authenticated. Please authenticate first."}
        
        try:
            # Parse start_time (expecting ISO format)
            if isinstance(start_time, str):
                try:
                    # Handle various ISO format variations
                    start_time_clean = start_time.replace('Z', '+00:00')
                    if '+' not in start_time_clean and '-' not in start_time_clean[10:]:
                        # No timezone info, assume local
                        start_dt = datetime.fromisoformat(start_time_clean)
                    else:
                        start_dt = datetime.fromisoformat(start_time_clean)
                except Exception:
                    # If parsing fails, default to 1 hour from now
                    start_dt = datetime.now() + timedelta(hours=1)
            else:
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
            
            # Build description with attendee name if provided
            event_description = description or ""
            if attendee_name:
                event_description = f"Meeting with {attendee_name}\n{event_description}".strip()
            
            event = {
                'summary': summary,
                'description': event_description,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': 'UTC',
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': 'UTC',
                },
            }
            
            created_event = self.calendar_service.events().insert(
                calendarId='primary', 
                body=event
            ).execute()
            
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
    
    def get_session_config(self):
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
                            "description": "Start time in ISO 8601 format (e.g., 2026-01-15T14:00:00). Convert relative times to absolute using the current date provided."
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
    
    async def send_audio(self, audio_data: bytes):
        """Send audio data to the Realtime API"""
        if self.ws:
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')
            await self.ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": audio_base64
            }))
    
    async def handle_function_call(self, call_id: str, name: str, arguments: str):
        """Handle function calls from the assistant"""
        print(f"\n[Function Call] {name}")
        
        try:
            args = json.loads(arguments)
        except json.JSONDecodeError:
            args = {}
        
        result = None
        if name == "add_calendar_event":
            result = self.add_calendar_event(
                summary=args.get("summary", "Meeting"),
                start_time=args.get("start_time"),
                end_time=args.get("end_time"),
                description=args.get("description"),
                attendee_name=args.get("attendee_name")
            )
            print(f"[Calendar] {result}")
        else:
            result = {"error": f"Unknown function: {name}"}
        
        # Send function result back to the API
        await self.ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result)
            }
        }))
        
        # Request a response after function execution
        await self.ws.send(json.dumps({
            "type": "response.create"
        }))
    
    async def play_audio_chunk(self, audio_base64: str):
        """Decode and buffer audio for playback"""
        try:
            audio_data = base64.b64decode(audio_base64)
            self.audio_buffer.extend(audio_data)
        except Exception as e:
            print(f"[Audio Error] {e}")
    
    async def audio_playback_task(self):
        """Continuously play audio from the buffer"""
        self.playback_stream = self.audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            output=True,
            frames_per_buffer=CHUNK_SIZE
        )
        
        try:
            while True:
                if len(self.audio_buffer) >= CHUNK_SIZE * 2:
                    # Play a chunk
                    chunk = bytes(self.audio_buffer[:CHUNK_SIZE * 2])
                    self.audio_buffer = self.audio_buffer[CHUNK_SIZE * 2:]
                    self.playback_stream.write(chunk)
                else:
                    await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
        finally:
            if self.playback_stream:
                self.playback_stream.stop_stream()
                self.playback_stream.close()
    
    async def audio_capture_task(self):
        """Capture audio from microphone and send to API"""
        stream = self.audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE
        )
        
        print("\n🎤 Listening... (Speak into your microphone)")
        
        try:
            while True:
                # Don't send audio while assistant is speaking
                if not self.response_in_progress:
                    try:
                        audio_data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                        await self.send_audio(audio_data)
                    except Exception as e:
                        print(f"[Mic Error] {e}")
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
        finally:
            stream.stop_stream()
            stream.close()
    
    async def handle_events(self):
        """Handle incoming events from the Realtime API"""
        current_function_call = {}
        
        try:
            async for message in self.ws:
                event = json.loads(message)
                event_type = event.get("type", "")
                
                # Session events
                if event_type == "session.created":
                    print("[Session] Connected to OpenAI Realtime API")
                    # Send session configuration
                    await self.ws.send(json.dumps(self.get_session_config()))
                
                elif event_type == "session.updated":
                    print("[Session] Configuration updated")
                    # Trigger initial response to start conversation
                    await self.ws.send(json.dumps({
                        "type": "response.create",
                        "response": {
                            "modalities": ["text", "audio"]
                        }
                    }))
                
                # Response events
                elif event_type == "response.created":
                    self.response_in_progress = True
                
                elif event_type == "response.done":
                    self.response_in_progress = False
                    # Check if there's a function call in the response
                    response = event.get("response", {})
                    output = response.get("output", [])
                    for item in output:
                        if item.get("type") == "function_call":
                            call_id = item.get("call_id")
                            name = item.get("name")
                            arguments = item.get("arguments", "{}")
                            await self.handle_function_call(call_id, name, arguments)
                
                # Audio events
                elif event_type == "response.audio.delta":
                    delta = event.get("delta", "")
                    if delta:
                        await self.play_audio_chunk(delta)
                
                # Transcript events (for debugging/display)
                elif event_type == "response.audio_transcript.delta":
                    transcript = event.get("delta", "")
                    if transcript:
                        print(transcript, end="", flush=True)
                
                elif event_type == "response.audio_transcript.done":
                    print()  # New line after transcript
                
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "")
                    if transcript:
                        print(f"\n[You] {transcript}")
                
                # Error handling
                elif event_type == "error":
                    error = event.get("error", {})
                    print(f"\n[Error] {error.get('message', 'Unknown error')}")
                
                # Rate limit events
                elif event_type == "rate_limits.updated":
                    pass  # Silently handle rate limit updates
                
        except websockets.exceptions.ConnectionClosed as e:
            print(f"\n[Connection] Closed: {e}")
        except Exception as e:
            print(f"\n[Error] {e}")
    
    async def run(self):
        """Main run loop"""
        print("\n" + "="*60)
        print("  Voice Scheduling Agent - OpenAI Realtime API")
        print("="*60)
        
        if not OPENAI_API_KEY:
            print("\n[Error] OPENAI_API_KEY not set!")
            print("Please set your OpenAI API key:")
            print("  - Set OPENAI_API_KEY environment variable, or")
            print("  - Edit the OPENAI_API_KEY variable in this script")
            return
        
        # Authenticate Google Calendar
        try:
            self.authenticate_google()
        except Exception as e:
            print(f"\n[Warning] Google Calendar auth failed: {e}")
            print("Calendar features will not work.")
        
        print("\nConnecting to OpenAI Realtime API...")
        
        # Connect to WebSocket
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
            ) as ws:
                self.ws = ws
                
                # Start tasks
                playback_task = asyncio.create_task(self.audio_playback_task())
                capture_task = asyncio.create_task(self.audio_capture_task())
                events_task = asyncio.create_task(self.handle_events())
                
                print("\n[Ready] Voice agent is active!")
                print("Press Ctrl+C to stop.\n")
                
                try:
                    # Wait for any task to complete (or fail)
                    done, pending = await asyncio.wait(
                        [playback_task, capture_task, events_task],
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    
                    # Cancel remaining tasks
                    for task in pending:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                            
                except asyncio.CancelledError:
                    pass
                    
        except websockets.exceptions.InvalidStatusCode as e:
            print(f"\n[Error] Failed to connect: {e}")
            if "401" in str(e):
                print("Invalid API key. Please check your OPENAI_API_KEY.")
            elif "429" in str(e):
                print("Rate limited. Please wait and try again.")
        except Exception as e:
            print(f"\n[Error] Connection failed: {e}")
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Cleanup resources"""
        print("\n[Cleanup] Shutting down...")
        if self.audio:
            self.audio.terminate()


async def main():
    agent = VoiceSchedulingAgent()
    try:
        await agent.run()
    except KeyboardInterrupt:
        print("\n\nStopping voice agent...")
    finally:
        agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
