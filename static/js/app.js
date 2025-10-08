const socket = io();

socket.on('connect', () => {
    console.log('Connected to server');
    loadConfig();
    loadStats();
});

socket.on('chat_response', (data) => {
    addBotMessage(data);
});

socket.on('crawl_status', (data) => {
    updateCrawlStatus(data);
    if (data.status === 'completed') {
        loadStats();
    }
});

socket.on('crawl_progress', (data) => {
    updateProgressStatus(data);
});

socket.on('index_status', (data) => {
    updateCrawlStatus(data);
    if (data.status === 'completed') {
        loadStats();
    }
});

socket.on('index_progress', (data) => {
    updateProgressStatus(data);
});

function loadConfig() {
    fetch('/api/config')
        .then(r => r.json())
        .then(config => {
            document.getElementById('url-input').value = config.url || '';
            document.getElementById('max-pages-input').value = config.max_pages || 500;
            document.getElementById('chunk-size-input').value = config.chunk_size || 900;
            document.getElementById('chunk-overlap-input').value = config.chunk_overlap || 150;
            document.getElementById('similarity-threshold').value = config.similarity_threshold || 0.52;
            document.getElementById('top-k').value = config.top_k || 4;
        });
}

function saveConfig() {
    const config = {
        url: document.getElementById('url-input').value,
        max_pages: parseInt(document.getElementById('max-pages-input').value),
        chunk_size: parseInt(document.getElementById('chunk-size-input').value),
        chunk_overlap: parseInt(document.getElementById('chunk-overlap-input').value),
        similarity_threshold: parseFloat(document.getElementById('similarity-threshold').value),
        top_k: parseInt(document.getElementById('top-k').value)
    };
    
    fetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(config)
    })
    .then(r => r.json())
    .then(data => {
        showStatus('Configuration saved successfully', 'success');
    })
    .catch(err => {
        showStatus('Error saving configuration', 'error');
    });
}

function loadStats() {
    fetch('/api/stats')
        .then(r => r.json())
        .then(stats => {
            document.getElementById('stat-url').textContent = stats.configured_url || '-';
            document.getElementById('stat-raw-docs').textContent = stats.raw_documents || 0;
            document.getElementById('stat-chunks').textContent = stats.total_chunks || 0;
            
            const indexedBadge = document.getElementById('stat-indexed');
            const statusBadge = document.getElementById('status-badge');
            
            if (stats.indexed) {
                indexedBadge.textContent = 'Yes';
                indexedBadge.className = 'badge bg-success';
                statusBadge.textContent = 'Indexed & Ready';
                statusBadge.className = 'badge bg-success';
            } else {
                indexedBadge.textContent = 'No';
                indexedBadge.className = 'badge bg-secondary';
                statusBadge.textContent = 'Not Indexed';
                statusBadge.className = 'badge bg-warning text-dark';
            }
        });
}

function startCrawl() {
    const url = document.getElementById('url-input').value;
    const maxPages = parseInt(document.getElementById('max-pages-input').value);
    
    if (!url) {
        showStatus('Please enter a URL', 'error');
        return;
    }
    
    fetch('/api/crawl', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url, max_pages: maxPages})
    })
    .then(r => r.json())
    .then(data => {
        showStatus('Crawl started...', 'info');
    });
}

function startIndexing() {
    const url = document.getElementById('url-input').value;
    const maxPages = parseInt(document.getElementById('max-pages-input').value);
    const chunkSize = parseInt(document.getElementById('chunk-size-input').value);
    const chunkOverlap = parseInt(document.getElementById('chunk-overlap-input').value);
    const fileInput = document.getElementById('doc-upload');
    
    const formData = new FormData();
    formData.append('url', url || '');
    formData.append('max_pages', maxPages);
    formData.append('chunk_size', chunkSize);
    formData.append('chunk_overlap', chunkOverlap);
    
    if (fileInput.files.length > 0) {
        for (let i = 0; i < fileInput.files.length; i++) {
            formData.append('documents', fileInput.files[i]);
        }
    }
    
    if (!url && fileInput.files.length === 0) {
        showStatus('Please provide a URL or upload documents', 'error');
        return;
    }
    
    fetch('/api/index_all', {
        method: 'POST',
        body: formData
    })
    .then(r => r.json())
    .then(data => {
        showStatus('Building knowledge base...', 'info');
        fileInput.value = '';
    })
    .catch(err => {
        showStatus('Error starting indexing', 'error');
        console.error(err);
    });
}

function sendMessage() {
    const input = document.getElementById('chat-input');
    const message = input.value.trim();
    
    if (!message) return;
    
    addUserMessage(message);
    socket.emit('chat_message', {message});
    input.value = '';
}

function addUserMessage(text) {
    const container = document.getElementById('chat-messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message message-user';
    messageDiv.innerHTML = `
        <div class="message-header">
            <i class="bi bi-person-circle"></i> You
        </div>
        <div class="message-content">${escapeHtml(text)}</div>
    `;
    container.appendChild(messageDiv);
    container.scrollTop = container.scrollHeight;
}

function addBotMessage(data) {
    const container = document.getElementById('chat-messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message message-bot';
    
    const showRefs = document.getElementById('show-references').checked;
    
    let sourcesHtml = '';
    if (showRefs && data.sources && data.sources.length > 0) {
        sourcesHtml = '<div class="mt-2"><strong>References:</strong><br>';
        data.sources.forEach(src => {
            const score = (src.score * 100).toFixed(0);
            sourcesHtml += `<a href="${src.url}" target="_blank" class="source-link">${src.url}</a> <span class="badge bg-info confidence-badge">${score}%</span><br>`;
        });
        sourcesHtml += '</div>';
    }
    
    const confidence = data.confidence ? (data.confidence * 100).toFixed(0) : 0;
    const confidenceBadge = data.confidence > 0.5 ? 'bg-success' : 'bg-warning';
    
    messageDiv.innerHTML = `
        <div class="message-header">
            <i class="bi bi-robot"></i> AI Hub Bot
            ${data.confidence > 0 ? `<span class="badge ${confidenceBadge} confidence-badge ms-auto">${confidence}% confidence</span>` : ''}
        </div>
        <div class="message-content">${escapeHtml(data.answer).replace(/\n/g, '<br>')}</div>
        ${sourcesHtml}
    `;
    container.appendChild(messageDiv);
    container.scrollTop = container.scrollHeight;
}

function updateCrawlStatus(data) {
    const statusDiv = document.getElementById('crawl-status');
    let statusClass = 'status-info';
    let message = data.message || '';
    
    if (data.status === 'completed') {
        statusClass = 'status-success';
        if (data.result) {
            message = `✓ ${data.result.pages || 0} pages processed`;
        }
    } else if (data.status === 'error') {
        statusClass = 'status-error';
        message = `✗ ${message}`;
    } else if (data.status === 'started') {
        statusClass = 'status-info';
        message = `⟳ ${message}`;
    }
    
    statusDiv.className = statusClass;
    statusDiv.textContent = message;
}

function showStatus(message, type) {
    const statusDiv = document.getElementById('crawl-status');
    statusDiv.className = `status-${type}`;
    statusDiv.textContent = message;
}

function updateProgressStatus(data) {
    const statusDiv = document.getElementById('crawl-status');
    let statusClass = 'status-info';
    let icon = '⟳';
    
    if (data.type === 'success') {
        statusClass = 'status-success';
        icon = '✓';
    } else if (data.type === 'error') {
        statusClass = 'status-error';
        icon = '✗';
    } else if (data.type === 'warning') {
        statusClass = 'status-error';
        icon = '⚠';
    } else if (data.type === 'complete') {
        statusClass = 'status-success';
        icon = '✓';
    }
    
    statusDiv.className = statusClass;
    statusDiv.textContent = `${icon} ${data.message}`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function clearBot() {
    if (!confirm('⚠️ WARNING: This will permanently delete ALL crawled data, the search index, conversation history, and reset all settings to defaults.\n\nAre you absolutely sure you want to continue?')) {
        return;
    }
    
    fetch('/api/clear-bot', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            showStatus('✓ Bot cleared successfully! All data reset.', 'success');
            loadConfig();
            loadStats();
            document.getElementById('chat-messages').innerHTML = `
                <div class="alert alert-info">
                    Welcome! This chatbot only answers questions based on the indexed website content. 
                    Configure the URL, crawl the site, and build the index to get started.
                </div>
            `;
            document.getElementById('conversation-history').innerHTML = '';
        } else {
            showStatus('✗ Error clearing bot: ' + data.message, 'error');
        }
    })
    .catch(err => {
        showStatus('✗ Error clearing bot', 'error');
    });
}

function clearConversationHistory() {
    if (!confirm('Are you sure you want to delete all conversation history? This cannot be undone.')) {
        return;
    }
    
    fetch('/api/clear-conversations', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            showStatus('✓ Conversation history cleared', 'success');
            document.getElementById('conversation-history').innerHTML = '<p class="text-muted small">No conversations yet.</p>';
        } else {
            showStatus('✗ Error clearing history: ' + data.message, 'error');
        }
    })
    .catch(err => {
        showStatus('✗ Error clearing history', 'error');
    });
}

function loadConversations() {
    fetch('/api/conversations')
        .then(r => r.json())
        .then(data => {
            const container = document.getElementById('conversation-history');
            
            // Handle error response from server
            if (data.error) {
                container.innerHTML = `<p class="text-warning small">${escapeHtml(data.error)}</p>`;
                return;
            }
            
            const conversations = data.conversations || data;
            
            if (!Array.isArray(conversations) || conversations.length === 0) {
                container.innerHTML = '<p class="text-muted small">No conversations yet.</p>';
                return;
            }
            
            container.innerHTML = conversations.map(conv => {
                const date = new Date(conv.timestamp).toLocaleString();
                const feedback = conv.feedback ? conv.feedback : '';
                
                return `
                    <div class="card mb-2 conversation-item">
                        <div class="card-body p-2">
                            <div class="small"><strong>Q:</strong> ${escapeHtml(conv.question.substring(0, 80))}${conv.question.length > 80 ? '...' : ''}</div>
                            <div class="small text-muted"><strong>A:</strong> ${escapeHtml(conv.answer.substring(0, 100))}${conv.answer.length > 100 ? '...' : ''}</div>
                            <div class="text-muted" style="font-size: 0.75rem;">${date}</div>
                            <div class="mt-1">
                                <input type="text" class="form-control form-control-sm" id="feedback-${conv.id}" placeholder="Add feedback..." value="${escapeHtml(feedback)}">
                                <button class="btn btn-sm btn-outline-primary mt-1 w-100" onclick="saveFeedback(${conv.id})">
                                    <i class="bi bi-save"></i> Save Feedback
                                </button>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        })
        .catch(err => {
            console.error('Error loading conversations:', err);
            document.getElementById('conversation-history').innerHTML = '<p class="text-danger small">Error loading history.</p>';
        });
}

function saveFeedback(convId) {
    const feedbackInput = document.getElementById(`feedback-${convId}`);
    const feedback = feedbackInput.value.trim();
    
    fetch(`/api/conversations/${convId}/feedback`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({feedback})
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            showStatus('✓ Feedback saved!', 'success');
            setTimeout(() => showStatus('', 'info'), 2000);
        } else {
            showStatus('✗ Error saving feedback', 'error');
        }
    })
    .catch(err => {
        showStatus('✗ Error saving feedback', 'error');
    });
}
