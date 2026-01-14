// Dashboard JavaScript

// State
let currentUser = null;
let conversations = [];
let events = [];

// Initialize dashboard
document.addEventListener('DOMContentLoaded', async () => {
    await loadUserInfo();
    await loadStats();
    await loadConversations();
    await loadEvents();
    setupTabs();
});

// Load user information
async function loadUserInfo() {
    try {
        const response = await fetch('/api/user/me', {
            credentials: 'include'
        });

        if (!response.ok) {
            // Not authenticated, show alert and redirect
            alert('Please connect your Google Calendar first to access the dashboard.');
            window.location.href = '/';
            return;
        }

        currentUser = await response.json();
        document.getElementById('user-email').textContent = currentUser.email || 'User';
    } catch (error) {
        console.error('Error loading user info:', error);
        alert('Error loading user information. Please try connecting your calendar again.');
        window.location.href = '/';
    }
}

// Load statistics
async function loadStats() {
    try {
        const response = await fetch('/api/stats', { credentials: 'include' });
        
        if (!response.ok) {
            throw new Error('Failed to load statistics');
        }

        const stats = await response.json();

        document.getElementById('stat-conversations').textContent = stats.total_conversations || 0;
        document.getElementById('stat-events').textContent = stats.total_calendar_events || 0;
        document.getElementById('stat-messages').textContent = stats.total_messages || 0;
    } catch (error) {
        console.error('Error loading stats:', error);
        // Fallback: try to calculate from conversations and events
        try {
            const [conversationsRes, eventsRes] = await Promise.all([
                fetch('/api/conversations?limit=1000', { credentials: 'include' }),
                fetch('/api/events?limit=1000', { credentials: 'include' })
            ]);

            if (conversationsRes.ok && eventsRes.ok) {
                const conversationsData = await conversationsRes.json();
                const eventsData = await eventsRes.json();
                const totalMessages = conversationsData.reduce((sum, conv) => sum + (conv.message_count || 0), 0);

                document.getElementById('stat-conversations').textContent = conversationsData.length || 0;
                document.getElementById('stat-events').textContent = eventsData.length || 0;
                document.getElementById('stat-messages').textContent = totalMessages || 0;
            }
        } catch (fallbackError) {
            console.error('Fallback stats loading also failed:', fallbackError);
        }
    }
}

// Load conversations
async function loadConversations() {
    const container = document.getElementById('conversations-list');

    try {
        const response = await fetch('/api/conversations?limit=100', {
            credentials: 'include'
        });

        if (!response.ok) {
            throw new Error('Failed to load conversations');
        }

        conversations = await response.json();

        if (conversations.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                    </svg>
                    <h3>No conversations yet</h3>
                    <p>Start a conversation with the voice agent to see it here</p>
                </div>
            `;
            return;
        }

        container.innerHTML = conversations.map(conv => createConversationCard(conv)).join('');
    } catch (error) {
        console.error('Error loading conversations:', error);
        container.innerHTML = `
            <div class="empty-state">
                <h3>Error loading conversations</h3>
                <p>${error.message}</p>
            </div>
        `;
    }
}

// Create conversation card HTML
function createConversationCard(conversation) {
    const startTime = new Date(conversation.started_at);
    const formattedTime = formatDateTime(startTime);
    const statusClass = conversation.status.toLowerCase();

    return `
        <div class="conversation-card" onclick="openConversationModal(${conversation.id})">
            <div class="conversation-header">
                <span class="conversation-status ${statusClass}">
                    ${conversation.status.charAt(0).toUpperCase() + conversation.status.slice(1)}
                </span>
                <span class="conversation-time">${formattedTime}</span>
            </div>
            <div class="conversation-meta">
                <div class="conversation-meta-item">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                    </svg>
                    <span>${conversation.message_count} messages</span>
                </div>
                <div class="conversation-meta-item">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/>
                    </svg>
                    <span>${conversation.events_created} events</span>
                </div>
            </div>
        </div>
    `;
}

// Load events
async function loadEvents() {
    const container = document.getElementById('events-list');

    try {
        const response = await fetch('/api/events?limit=100', {
            credentials: 'include'
        });

        if (!response.ok) {
            throw new Error('Failed to load events');
        }

        events = await response.json();

        if (events.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/>
                    </svg>
                    <h3>No events created yet</h3>
                    <p>Calendar events created by the voice agent will appear here</p>
                </div>
            `;
            return;
        }

        container.innerHTML = events.map(event => createEventCard(event)).join('');
    } catch (error) {
        console.error('Error loading events:', error);
        container.innerHTML = `
            <div class="empty-state">
                <h3>Error loading events</h3>
                <p>${error.message}</p>
            </div>
        `;
    }
}

// Create event card HTML
function createEventCard(event) {
    const startTime = new Date(event.start_time);
    const endTime = new Date(event.end_time);
    const formattedStart = formatDateTime(startTime);
    const formattedEnd = formatTime(endTime);

    return `
        <div class="event-card">
            <div class="event-title">${escapeHtml(event.summary)}</div>
            <div class="event-time">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"/>
                    <polyline points="12 6 12 12 16 14"/>
                </svg>
                <span>${formattedStart} - ${formattedEnd}</span>
            </div>
            ${event.attendee_name ? `
                <div class="event-attendee">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>
                    </svg>
                    <span>${escapeHtml(event.attendee_name)}</span>
                </div>
            ` : ''}
            ${event.html_link ? `
                <a href="${event.html_link}" target="_blank" class="event-link">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
                        <polyline points="15 3 21 3 21 9"/>
                        <line x1="10" y1="14" x2="21" y2="3"/>
                    </svg>
                    View in Google Calendar
                </a>
            ` : ''}
        </div>
    `;
}

// Open conversation modal
async function openConversationModal(conversationId) {
    const modal = document.getElementById('conversation-modal');
    const detailsContainer = document.getElementById('conversation-details');

    modal.classList.add('active');
    detailsContainer.innerHTML = '<div class="loading">Loading conversation details...</div>';

    try {
        const response = await fetch(`/api/conversations/${conversationId}`, {
            credentials: 'include'
        });

        if (!response.ok) {
            throw new Error('Failed to load conversation details');
        }

        const conversation = await response.json();

        let html = '';

        // Messages section
        if (conversation.messages && conversation.messages.length > 0) {
            html += `
                <div class="detail-section">
                    <h4>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                        </svg>
                        Messages
                    </h4>
                    ${conversation.messages.map(msg => `
                        <div class="message ${msg.role}">
                            <div class="message-role">${msg.role}</div>
                            <div class="message-content">${escapeHtml(msg.content)}</div>
                            <div class="message-time">${formatDateTime(new Date(msg.timestamp))}</div>
                        </div>
                    `).join('')}
                </div>
            `;
        }

        // Events section
        if (conversation.calendar_events && conversation.calendar_events.length > 0) {
            html += `
                <div class="detail-section">
                    <h4>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/>
                        </svg>
                        Events Created
                    </h4>
                    ${conversation.calendar_events.map(event => createEventCard(event)).join('')}
                </div>
            `;
        }

        if (!html) {
            html = '<div class="empty-state"><p>No details available</p></div>';
        }

        detailsContainer.innerHTML = html;
    } catch (error) {
        console.error('Error loading conversation details:', error);
        detailsContainer.innerHTML = `
            <div class="empty-state">
                <h3>Error loading details</h3>
                <p>${error.message}</p>
            </div>
        `;
    }
}

// Close conversation modal
function closeConversationModal() {
    const modal = document.getElementById('conversation-modal');
    modal.classList.remove('active');
}

// Close modal on background click
document.getElementById('conversation-modal')?.addEventListener('click', (e) => {
    if (e.target.id === 'conversation-modal') {
        closeConversationModal();
    }
});

// Setup tabs
function setupTabs() {
    const tabButtons = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');

    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const tabName = button.dataset.tab;

            // Update active states
            tabButtons.forEach(btn => btn.classList.remove('active'));
            tabContents.forEach(content => content.classList.remove('active'));

            button.classList.add('active');
            document.getElementById(`${tabName}-tab`).classList.add('active');
        });
    });
}

// Handle logout
async function handleLogout() {
    if (confirm('Are you sure you want to logout?')) {
        window.location.href = '/auth/logout';
    }
}

// Utility functions
function formatDateTime(date) {
    const now = new Date();
    const diff = now - date;
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));

    if (days === 0) {
        const hours = Math.floor(diff / (1000 * 60 * 60));
        if (hours === 0) {
            const minutes = Math.floor(diff / (1000 * 60));
            return minutes === 0 ? 'Just now' : `${minutes}m ago`;
        }
        return `${hours}h ago`;
    } else if (days === 1) {
        return 'Yesterday';
    } else if (days < 7) {
        return `${days} days ago`;
    } else {
        return date.toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
            year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined
        });
    }
}

function formatTime(date) {
    return date.toLocaleTimeString('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        hour12: true
    });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
