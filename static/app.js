/**
 * Voice Scheduling Agent - Frontend JavaScript
 * Handles WebSocket communication, audio capture/playback, and UI updates
 */

// ==================== State ====================
let websocket = null;
let audioContext = null;
let mediaStream = null;
let audioWorkletNode = null;
let isConnected = false;
let isListening = false;
let currentTranscript = '';

// Audio playback queue
let audioQueue = [];
let isPlaying = false;

// ==================== DOM Elements ====================
const voiceOrb = document.getElementById('voice-orb');
const statusText = document.getElementById('status-text');
const startBtn = document.getElementById('start-btn');
const stopBtn = document.getElementById('stop-btn');
const transcript = document.getElementById('transcript');
const authStatus = document.getElementById('auth-status');
const authBtn = document.getElementById('auth-btn');

// ==================== Initialization ====================
document.addEventListener('DOMContentLoaded', () => {
    checkAuthStatus();
    checkUrlParams();
});

function checkUrlParams() {
    const params = new URLSearchParams(window.location.search);
    const auth = params.get('auth');

    if (auth === 'success') {
        addSystemMessage('Successfully connected to Google Calendar!', 'success');
        // Clean URL first
        window.history.replaceState({}, document.title, '/');
        // Refresh auth status after a short delay to ensure cookie is set
        setTimeout(() => {
            checkAuthStatus();
        }, 500);
    } else if (auth === 'error') {
        const message = params.get('message') || 'Authentication failed';
        addSystemMessage(`Authentication error: ${message}`, 'error');
        window.history.replaceState({}, document.title, '/');
    } else if (auth === 'logged_out') {
        addSystemMessage('Disconnected from Google Calendar', 'system');
        checkAuthStatus();
        window.history.replaceState({}, document.title, '/');
    }
}

async function checkAuthStatus() {
    try {
        const response = await fetch('/auth/status', {
            credentials: 'include'  // Include cookies
        });
        const data = await response.json();

        const dashboardLink = document.getElementById('dashboard-link');
        const dashboardBtn = document.getElementById('dashboard-btn');

        if (data.authenticated) {
            authStatus.classList.remove('disconnected');
            authStatus.classList.add('connected');
            const authText = authStatus.querySelector('.auth-text');
            if (data.email) {
                authText.textContent = `Connected as ${data.email}`;
            } else {
                authText.textContent = 'Connected';
            }
            authBtn.textContent = 'Disconnect';
            authBtn.onclick = () => { window.location.href = '/auth/logout'; };

            // Show dashboard link and button
            if (dashboardLink) {
                dashboardLink.style.display = 'inline-flex';
            }
            if (dashboardBtn) {
                dashboardBtn.style.display = 'inline-flex';
            }
        } else {
            authStatus.classList.remove('connected');
            authStatus.classList.add('disconnected');
            authStatus.querySelector('.auth-text').textContent = 'Not connected';
            authBtn.textContent = 'Connect Calendar';
            authBtn.onclick = () => { window.location.href = '/auth/login'; };

            // Hide dashboard link and button
            if (dashboardLink) {
                dashboardLink.style.display = 'none';
            }
            if (dashboardBtn) {
                dashboardBtn.style.display = 'none';
            }
        }
    } catch (error) {
        console.error('Auth status check failed:', error);
    }
}


function handleAuth() {
    window.location.href = '/auth/login';
}

// ==================== WebSocket ====================
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    websocket = new WebSocket(wsUrl);

    websocket.onopen = () => {
        console.log('WebSocket connected');
        isConnected = true;
        updateStatus('connected', 'Connected to server');
    };

    websocket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleServerMessage(data);
    };

    websocket.onclose = () => {
        console.log('WebSocket disconnected');
        isConnected = false;
        isListening = false;
        updateStatus('idle', 'Disconnected');
    };

    websocket.onerror = (error) => {
        console.error('WebSocket error:', error);
        updateStatus('error', 'Connection error');
    };
}

function handleServerMessage(data) {
    switch (data.type) {
        case 'status':
            handleStatusUpdate(data);
            break;
        case 'audio':
            handleAudioData(data.audio);
            break;
        case 'transcript':
            handleTranscriptDelta(data);
            break;
        case 'transcript_done':
            handleTranscriptDone(data);
            break;
        case 'function_result':
            handleFunctionResult(data);
            break;
        case 'auth_status':
            if (data.authenticated) {
                authStatus.classList.add('connected');
                authStatus.classList.remove('disconnected');
                const authText = authStatus.querySelector('.auth-text');
                if (data.email) {
                    authText.textContent = `Connected as ${data.email}`;
                } else {
                    authText.textContent = 'Connected';
                }
            } else {
                authStatus.classList.remove('connected');
                authStatus.classList.add('disconnected');
                authStatus.querySelector('.auth-text').textContent = 'Not connected';
            }
            break;
        case 'error':
            handleError(data.message);
            break;
    }
}

function handleStatusUpdate(data) {
    switch (data.status) {
        case 'connected':
            updateStatus('idle', 'Connected - Starting session...');
            break;
        case 'ready':
            updateStatus('idle', 'Ready');
            // Send start signal to begin conversation
            websocket.send(JSON.stringify({ type: 'start' }));
            break;
        case 'listening':
            isListening = true;
            updateStatus('listening', 'Listening...');
            break;
        case 'speaking':
            isListening = false;
            updateStatus('speaking', 'Speaking...');
            break;
    }
}

function handleTranscriptDelta(data) {
    // Real-time transcript updates
    if (data.role === 'assistant') {
        // Update or create assistant message
        let lastMsg = transcript.querySelector('.transcript-message.assistant:last-of-type');
        if (!lastMsg || lastMsg.dataset.complete === 'true') {
            lastMsg = createTranscriptMessage('assistant', '');
            transcript.appendChild(lastMsg);
        }
        lastMsg.querySelector('.transcript-text').textContent += data.delta;
        scrollTranscript();
    }
}

function handleTranscriptDone(data) {
    if (data.role === 'user') {
        // Add completed user message
        const msg = createTranscriptMessage('user', data.text);
        transcript.appendChild(msg);
        scrollTranscript();
    } else if (data.role === 'assistant') {
        // Mark assistant message as complete
        let lastMsg = transcript.querySelector('.transcript-message.assistant:last-of-type');
        if (lastMsg) {
            lastMsg.dataset.complete = 'true';
        }
    }

    // Remove placeholder if it exists
    const placeholder = transcript.querySelector('.transcript-placeholder');
    if (placeholder) {
        placeholder.remove();
    }
}

function handleFunctionResult(data) {
    if (data.name === 'add_calendar_event') {
        if (data.result.success) {
            addSystemMessage(`Calendar event created: ${data.result.summary}`, 'success');
        } else {
            addSystemMessage(`Failed to create event: ${data.result.error}`, 'error');
        }
    }
}

function handleError(message) {
    updateStatus('error', message);
    addSystemMessage(`Error: ${message}`, 'error');
}

// ==================== Audio ====================
async function initAudio() {
    try {
        // Request microphone access
        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                sampleRate: 24000,
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
            }
        });

        // Create audio context at 24kHz
        audioContext = new AudioContext({ sampleRate: 24000 });

        // Create AudioWorklet for PCM processing
        const processorCode = document.getElementById('audio-processor').textContent;
        const blob = new Blob([processorCode], { type: 'application/javascript' });
        const url = URL.createObjectURL(blob);

        await audioContext.audioWorklet.addModule(url);

        const source = audioContext.createMediaStreamSource(mediaStream);
        audioWorkletNode = new AudioWorkletNode(audioContext, 'pcm-processor');

        audioWorkletNode.port.onmessage = (event) => {
            if (isConnected && isListening) {
                const audioData = event.data;
                const base64Audio = arrayBufferToBase64(audioData);
                websocket.send(JSON.stringify({
                    type: 'audio',
                    audio: base64Audio
                }));
            }
        };

        source.connect(audioWorkletNode);
        audioWorkletNode.connect(audioContext.destination);

        console.log('Audio initialized');
        return true;
    } catch (error) {
        console.error('Audio init failed:', error);
        addSystemMessage(`Microphone access denied: ${error.message}`, 'error');
        return false;
    }
}

function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
}

function base64ToArrayBuffer(base64) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
}

async function handleAudioData(base64Audio) {
    if (!audioContext) return;

    try {
        const audioData = base64ToArrayBuffer(base64Audio);
        const int16Array = new Int16Array(audioData);

        // Convert Int16 to Float32
        const float32Array = new Float32Array(int16Array.length);
        for (let i = 0; i < int16Array.length; i++) {
            float32Array[i] = int16Array[i] / 32768;
        }

        // Create audio buffer
        const audioBuffer = audioContext.createBuffer(1, float32Array.length, 24000);
        audioBuffer.getChannelData(0).set(float32Array);

        // Queue for playback
        audioQueue.push(audioBuffer);
        playNextAudio();
    } catch (error) {
        console.error('Audio playback error:', error);
    }
}

function playNextAudio() {
    if (isPlaying || audioQueue.length === 0) return;

    isPlaying = true;
    const buffer = audioQueue.shift();

    const source = audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(audioContext.destination);

    source.onended = () => {
        isPlaying = false;
        playNextAudio();
    };

    source.start();
}

// ==================== UI ====================
function updateStatus(state, message) {
    voiceOrb.className = 'voice-orb ' + state;
    statusText.textContent = message;
}

function createTranscriptMessage(role, text) {
    const msg = document.createElement('div');
    msg.className = `transcript-message ${role}`;
    msg.innerHTML = `
        <div class="transcript-role">${role === 'user' ? 'You' : 'Assistant'}</div>
        <div class="transcript-text">${text}</div>
    `;
    return msg;
}

function addSystemMessage(text, type = 'system') {
    const msg = document.createElement('div');
    msg.className = `transcript-message system ${type}`;
    msg.innerHTML = `<div class="transcript-text">${text}</div>`;
    transcript.appendChild(msg);
    scrollTranscript();

    // Remove placeholder if it exists
    const placeholder = transcript.querySelector('.transcript-placeholder');
    if (placeholder) {
        placeholder.remove();
    }
}

function scrollTranscript() {
    transcript.scrollTop = transcript.scrollHeight;
}

function clearTranscript() {
    transcript.innerHTML = '<div class="transcript-placeholder">Your conversation will appear here...</div>';
}

// ==================== Controls ====================
async function startConversation() {
    // Check if Google Calendar is connected
    const authResponse = await fetch('/auth/status', {
        credentials: 'include'  // Include cookies
    });
    const authData = await authResponse.json();

    if (!authData.authenticated) {
        addSystemMessage('Please connect your Google Calendar first', 'error');
        return;
    }

    // Initialize audio
    const audioReady = await initAudio();
    if (!audioReady) return;

    // Connect WebSocket
    connectWebSocket();

    // Update UI
    startBtn.style.display = 'none';
    stopBtn.style.display = 'inline-flex';
    clearTranscript();
    addSystemMessage('Starting conversation...', 'system');
    updateStatus('idle', 'Connecting...');
}

function stopConversation() {
    // Close WebSocket
    if (websocket) {
        websocket.close();
        websocket = null;
    }

    // Stop audio
    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
    }

    if (audioContext) {
        audioContext.close();
        audioContext = null;
    }

    // Clear audio queue
    audioQueue = [];
    isPlaying = false;
    isConnected = false;
    isListening = false;

    // Update UI
    startBtn.style.display = 'inline-flex';
    stopBtn.style.display = 'none';
    updateStatus('idle', 'Click Start to begin');
    addSystemMessage('Conversation ended', 'system');
}
