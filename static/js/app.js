let statusOverlayActive = false;
let statusHideTimer = null;
let inlineStatusTimer = null;
let chatHistoryLoaded = false;

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function formatTimestamp(timestamp) {
  if (!timestamp) {
    return '';
  }
  try {
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) {
      return '';
    }
    return date.toLocaleString();
  } catch (err) {
    return '';
  }
}

function clearChatPlaceholder() {
  const container = document.getElementById('chat-messages');
  if (!container) {
    return;
  }
  const placeholder = container.querySelector('.chat-placeholder');
  if (placeholder) {
    placeholder.remove();
  }
}

function beginStatusSession(title = 'Processing…') {
  const overlay = document.getElementById('status-overlay');
  if (!overlay) {
    return;
  }
  clearTimeout(statusHideTimer);
  overlay.classList.add('active');
  document.body.classList.add('status-overlay-open');
  statusOverlayActive = true;
  setStatusTitle(title);
  const log = document.getElementById('status-log');
  if (log) {
    log.innerHTML = '';
  }
  const spinner = document.getElementById('status-spinner');
  if (spinner) {
    spinner.classList.remove('d-none');
  }
  const closeBtn = document.getElementById('status-close-btn');
  if (closeBtn) {
    closeBtn.classList.add('d-none');
  }
}

function setStatusTitle(title) {
  const titleEl = document.getElementById('status-title');
  if (titleEl) {
    titleEl.textContent = title || 'Processing…';
  }
}

function normalizeStatusType(type) {
  switch ((type || 'info').toLowerCase()) {
    case 'success':
    case 'completed':
    case 'complete':
      return 'success';
    case 'error':
    case 'danger':
    case 'fail':
      return 'error';
    case 'warning':
    case 'warn':
      return 'warning';
    default:
      return 'info';
  }
}

function appendStatusLine(message, type = 'info') {
  if (!message) {
    return;
  }
  const log = document.getElementById('status-log');
  if (!log) {
    return;
  }
  if (!statusOverlayActive) {
    beginStatusSession();
  }
  const normalized = normalizeStatusType(type);
  const entry = document.createElement('div');
  entry.className = `status-entry status-${normalized}`;

  const iconSpan = document.createElement('span');
  iconSpan.className = 'status-icon';
  const iconMap = {
    info: '⟳',
    success: '✓',
    warning: '⚠',
    error: '✗'
  };
  iconSpan.textContent = iconMap[normalized] || '•';

  const messageSpan = document.createElement('span');
  messageSpan.innerHTML = escapeHtml(message || '');

  entry.appendChild(iconSpan);
  entry.appendChild(messageSpan);
  log.appendChild(entry);
  log.scrollTop = log.scrollHeight;
}

function completeStatusSession(message, type = 'success', options = {}) {
  appendStatusLine(message, type);
  const spinner = document.getElementById('status-spinner');
  if (spinner) {
    spinner.classList.add('d-none');
  }
  const closeBtn = document.getElementById('status-close-btn');
  if (closeBtn) {
    closeBtn.classList.remove('d-none');
  }
  statusOverlayActive = false;
  const autoHide = options.autoHide !== false;
  if (autoHide) {
    const delay = options.delay || 4000;
    clearTimeout(statusHideTimer);
    statusHideTimer = setTimeout(() => hideStatusOverlay(), delay);
  }
}

function hideStatusOverlay() {
  const overlay = document.getElementById('status-overlay');
  if (!overlay) {
    return;
  }
  overlay.classList.remove('active');
  document.body.classList.remove('status-overlay-open');
  statusOverlayActive = false;
  clearTimeout(statusHideTimer);
}

const socket = io();

socket.on('connect', () => {
  console.log('Connected to server');
  loadConfig();
  loadStats();
  loadChatHistory();
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
    loadIntents();
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
    let url = document.getElementById('url-input').value.trim();
    if (url && !/^https?:\/\//i.test(url)) {
        url = `https://${url}`;
        document.getElementById('url-input').value = url;
    }
    const config = {
        url,
        max_pages: parseInt(document.getElementById('max-pages-input').value),
        chunk_size: parseInt(document.getElementById('chunk-size-input').value),
        chunk_overlap: parseInt(document.getElementById('chunk-overlap-input').value),
        similarity_threshold: parseFloat(document.getElementById('similarity-threshold').value),
        top_k: parseInt(document.getElementById('top-k').value)
  };

  fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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
      const statsCard = document.getElementById('stats-card');
      const placeholder = document.getElementById('stats-placeholder');
      if (stats.indexed) {
        if (statsCard) statsCard.classList.remove('d-none');
        if (placeholder) placeholder.classList.add('d-none');
      } else {
        if (statsCard) statsCard.classList.add('d-none');
        if (placeholder) placeholder.classList.remove('d-none');
      }

            const urlEl = document.getElementById('stat-url');
            if (urlEl) {
                let configuredUrl = stats.configured_url;
                if (configuredUrl) {
                    const trimmedUrl = configuredUrl.trim();
                    const anchor = document.createElement('a');
                    const isHttp = /^https?:\/\//i.test(trimmedUrl);
                    urlEl.innerHTML = '';
                    if (isHttp) {
                        anchor.href = trimmedUrl;
                        anchor.target = '_blank';
                        anchor.rel = 'noopener';
                        anchor.textContent = trimmedUrl;
                        urlEl.appendChild(anchor);
                    } else {
                        anchor.href = '#';
                        anchor.className = 'text-muted text-decoration-none';
                        anchor.onclick = (event) => event.preventDefault();
                        anchor.textContent = trimmedUrl;
                        urlEl.appendChild(anchor);
                    }
                } else {
                    urlEl.textContent = 'No URL configured';
                }
            }
      const rawDocsEl = document.getElementById('stat-raw-docs');
      if (rawDocsEl) {
        rawDocsEl.textContent = stats.raw_documents || 0;
      }
      const chunksEl = document.getElementById('stat-chunks');
      if (chunksEl) {
        chunksEl.textContent = stats.total_chunks || 0;
      }

      const sourcesDiv = document.getElementById('stat-sources');
      if (sourcesDiv) {
        if (stats.document_sources && stats.document_sources.length > 0) {
          sourcesDiv.innerHTML = stats.document_sources.map(source => {
            const isString = typeof source === 'string';
            const href = isString ? source : (source.url || '#');
            const label = isString ? source : (source.label || source.url || 'Document');
            const safeHref = escapeHtml(href);
            const safeLabel = escapeHtml(label);
            return `<div class="mb-1">
                            <i class="bi bi-link-45deg"></i>
                            <a href="${safeHref}" target="_blank" rel="noopener" class="text-decoration-none">${safeLabel}</a>
                        </div>`;
          }).join('');
        } else {
          sourcesDiv.innerHTML = '<em>No documents loaded</em>';
        }
      }
    });
}

function loadChatHistory() {
  const container = document.getElementById('chat-messages');
  if (!container) {
    return;
  }
  fetch('/api/conversations')
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        if (!chatHistoryLoaded) {
          showStatus(data.error, 'warning');
        }
        return;
      }
      const conversations = data.conversations || data;
      if (!Array.isArray(conversations) || conversations.length === 0) {
        chatHistoryLoaded = true;
        return;
      }
      const sorted = conversations.slice().sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
      container.innerHTML = '';
      sorted.forEach(conv => {
        const scores = Array.isArray(conv.similarity_scores) ? conv.similarity_scores : [];
        const confidence = scores.length ? Math.max(...scores) : undefined;
        addUserMessage(conv.question, {
          suppressScroll: true,
          timestamp: conv.timestamp,
          fromHistory: true
        });
        addBotMessage({
          answer: conv.answer,
          sources: conv.sources || [],
          confidence,
          conversation_id: conv.id
        }, {
          suppressScroll: true,
          timestamp: conv.timestamp,
          feedback: conv.feedback || ''
        });
      });
      container.scrollTop = container.scrollHeight;
      chatHistoryLoaded = true;
    })
    .catch(err => {
      console.error('Error loading chat history:', err);
      if (!chatHistoryLoaded) {
        showStatus('Unable to load chat history', 'warning');
      }
    });
}

function startCrawl() {
  let url = document.getElementById('url-input').value.trim();
  if (url && !/^https?:\/\//i.test(url)) {
    url = `https://${url}`;
  }
  const maxPages = parseInt(document.getElementById('max-pages-input').value);

  if (!url) {
    showStatus('Please enter a URL', 'error');
    return;
  }

  beginStatusSession('Crawling website');
  appendStatusLine(`Preparing to crawl ${url}`, 'info');

  fetch('/api/crawl', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, max_pages: maxPages })
  })
    .then(r => r.json())
    .then(data => {
      appendStatusLine('Crawl started…', 'info');
    })
    .catch(err => {
      console.error(err);
      completeStatusSession('Failed to start crawl', 'error', { autoHide: false });
    });
}

function startIndexing() {
  const url = document.getElementById('url-input').value;
  const maxPages = parseInt(document.getElementById('max-pages-input').value);
  const chunkSize = parseInt(document.getElementById('chunk-size-input').value);
  const chunkOverlap = parseInt(document.getElementById('chunk-overlap-input').value);
  const similarityThreshold = parseFloat(document.getElementById('similarity-threshold').value);
  const topK = parseInt(document.getElementById('top-k').value, 10);
  const fileInput = document.getElementById('doc-upload');

  const formData = new FormData();
  formData.append('url', url || '');
  formData.append('max_pages', maxPages);
  formData.append('chunk_size', chunkSize);
  formData.append('chunk_overlap', chunkOverlap);
  if (!Number.isNaN(similarityThreshold)) {
    formData.append('similarity_threshold', similarityThreshold);
  }
  if (!Number.isNaN(topK)) {
    formData.append('top_k', topK);
  }

  if (fileInput.files.length > 0) {
    for (let i = 0; i < fileInput.files.length; i++) {
      formData.append('documents', fileInput.files[i]);
    }
  }

  if (!url && fileInput.files.length === 0) {
    showStatus('Please provide a URL or upload documents', 'error');
    return;
  }

  beginStatusSession('Building knowledge base');
  appendStatusLine('Submitting indexing request…', 'info');

  fetch('/api/index_all', {
    method: 'POST',
    body: formData
  })
    .then(r => r.json())
    .then(data => {
      fileInput.value = '';
    })
    .catch(err => {
      appendStatusLine('Error starting indexing', 'error');
      completeStatusSession('Failed to start indexing', 'error', { autoHide: false });
      console.error(err);
    });
}

function sendMessage() {
  const input = document.getElementById('chat-input');
  const message = input.value.trim();

  if (!message) return;

  addUserMessage(message);
  socket.emit('chat_message', { message });
  input.value = '';
}

function addUserMessage(text, options = {}) {
  const container = document.getElementById('chat-messages');
  if (!container) {
    return;
  }
  clearChatPlaceholder();
  const messageDiv = document.createElement('div');
  messageDiv.className = 'message message-user';
  const timestampText = options.timestamp ? formatTimestamp(options.timestamp) : '';
  messageDiv.innerHTML = `
        <div class="message-header">
            <i class="bi bi-person-circle"></i> You
        </div>
        <div class="message-content">${escapeHtml(text)}</div>
        ${timestampText ? `<div class="message-meta">${escapeHtml(timestampText)}</div>` : ''}
    `;
  container.appendChild(messageDiv);
  if (!options.suppressScroll) {
    container.scrollTop = container.scrollHeight;
  }
}

function addBotMessage(data, options = {}) {
  const container = document.getElementById('chat-messages');
  if (!container) {
    return;
  }
  clearChatPlaceholder();
  const messageDiv = document.createElement('div');
  messageDiv.className = 'message message-bot';
  const conversationId = data.conversation_id || options.conversationId || null;
  if (conversationId) {
    messageDiv.dataset.conversationId = conversationId;
  }
  const showRefsCheckbox = document.getElementById('show-references');
  const showRefs = showRefsCheckbox ? showRefsCheckbox.checked : true;
  const sources = Array.isArray(data.sources) ? data.sources : [];
  let sourcesHtml = '';
  if (showRefs && sources.length > 0) {
    sourcesHtml = '<div class="mt-2"><strong>References:</strong><br>';
    sources.forEach(src => {
      const url = src.url || src;
      if (!url) {
        return;
      }
      const scoreValue = typeof src.score === 'number' ? Math.round(src.score * 100) : null;
      const safeUrl = escapeHtml(url);
      sourcesHtml += `<a href="${safeUrl}" target="_blank" class="source-link">${safeUrl}</a>`;
      if (scoreValue !== null && !Number.isNaN(scoreValue)) {
        sourcesHtml += ` <span class="badge bg-info confidence-badge">${scoreValue}%</span>`;
      }
      sourcesHtml += '<br>';
    });
    sourcesHtml += '</div>';
  }
  const confidenceValue = typeof data.confidence === 'number' ? data.confidence : null;
  const confidencePercent = confidenceValue !== null ? Math.round(confidenceValue * 100) : 0;
  const confidenceBadge = confidenceValue !== null && confidenceValue > 0.5 ? 'bg-success' : 'bg-warning';
  const timestampText = options.timestamp ? formatTimestamp(options.timestamp) : '';
  const answerText = typeof data.answer === 'string' ? data.answer : '';
  messageDiv.innerHTML = `
        <div class="message-header">
            <i class="bi bi-robot"></i> IntelliBot
            ${confidenceValue !== null && confidenceValue > 0 ? `<span class="badge ${confidenceBadge} confidence-badge ms-auto">${confidencePercent}% confidence</span>` : ''}
        </div>
        <div class="message-content">${escapeHtml(answerText).replace(/\n/g, '<br>')}</div>
        ${sourcesHtml}
        ${timestampText ? `<div class="message-meta">${escapeHtml(timestampText)}</div>` : ''}
    `;
  container.appendChild(messageDiv);
  attachFeedbackControls(messageDiv, conversationId, options.feedback || data.feedback || '');
  if (!options.suppressScroll) {
    container.scrollTop = container.scrollHeight;
  }
}

function attachFeedbackControls(messageDiv, conversationId, feedback) {
  if (!conversationId || !messageDiv) {
    return;
  }
  let existing = messageDiv.querySelector('.message-feedback');
  if (existing) {
    existing.remove();
  }
  const feedbackElement = buildFeedbackElement(conversationId, feedback);
  messageDiv.appendChild(feedbackElement);
}

function buildFeedbackElement(conversationId, feedback) {
  const wrapper = document.createElement('div');
  wrapper.className = 'message-feedback';
  wrapper.dataset.conversationId = conversationId;
  const hasFeedback = Boolean(feedback && String(feedback).trim());
  const safeFeedback = hasFeedback ? escapeHtml(String(feedback).trim()) : '';
  wrapper.innerHTML = `
        <div class="d-flex flex-wrap align-items-center gap-2 feedback-actions">
            <span class="text-muted small">${hasFeedback ? 'Feedback recorded:' : 'Feedback: '}</span>
            ${hasFeedback ? `<span class="badge bg-light text-dark feedback-recorded">${safeFeedback}</span>` : `
                <button type="button" class="btn btn-outline-success btn-sm" onclick="submitQuickFeedback(${conversationId}, 'helpful')"><i class="bi bi-hand-thumbs-up"></i> Helpful</button>
                <button type="button" class="btn btn-outline-danger btn-sm" onclick="submitQuickFeedback(${conversationId}, 'not helpful')"><i class="bi bi-hand-thumbs-down"></i> Not Helpful</button>
            `}
            <button type="button" class="btn btn-link btn-sm p-0" onclick="toggleFeedbackForm(${conversationId})">${hasFeedback ? 'Update note' : 'Add note'}</button>
        </div>
        <div class="feedback-form mt-2 d-none">
            <textarea class="form-control form-control-sm" rows="2" id="feedback-text-${conversationId}" placeholder="Share more details..."></textarea>
            <div class="d-flex gap-2 mt-2">
                <button type="button" class="btn btn-primary btn-sm" onclick="submitFeedbackForm(${conversationId})">Submit</button>
                <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleFeedbackForm(${conversationId}, true)">Cancel</button>
            </div>
        </div>
    `;
  const textarea = wrapper.querySelector('textarea');
  if (textarea && hasFeedback) {
    textarea.value = feedback;
  }
  return wrapper;
}

function submitQuickFeedback(conversationId, value) {
  submitConversationFeedback(conversationId, value);
}

function toggleFeedbackForm(conversationId, hide = false) {
  const wrapper = document.querySelector(`.message-feedback[data-conversation-id="${conversationId}"]`);
  if (!wrapper) {
    return;
  }
  const form = wrapper.querySelector('.feedback-form');
  if (!form) {
    return;
  }
  if (hide === true) {
    form.classList.add('d-none');
    return;
  }
  form.classList.toggle('d-none');
  if (!form.classList.contains('d-none')) {
    const textarea = form.querySelector('textarea');
    if (textarea) {
      textarea.focus();
      textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    }
  }
}

function submitFeedbackForm(conversationId) {
  const textarea = document.getElementById(`feedback-text-${conversationId}`);
  if (!textarea) {
    return;
  }
  const text = textarea.value.trim();
  if (!text) {
    showStatus('Please add a note before submitting feedback.', 'warning');
    return;
  }
  submitConversationFeedback(conversationId, text);
}

function submitConversationFeedback(conversationId, feedback) {
  fetch(`/api/conversations/${conversationId}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ feedback })
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'success') {
        showStatus('Feedback saved.', 'success');
        updateFeedbackUI(conversationId, feedback);
      } else {
        showStatus(data.message || data.error || 'Error saving feedback.', 'error');
      }
    })
    .catch(err => {
      console.error('Error saving feedback:', err);
      showStatus('Error saving feedback.', 'error');
    });
}

function updateFeedbackUI(conversationId, feedback) {
  const message = document.querySelector(`.message-bot[data-conversation-id="${conversationId}"]`);
  if (!message) {
    return;
  }
  attachFeedbackControls(message, conversationId, feedback);
  toggleFeedbackForm(conversationId, true);
}

function updateCrawlStatus(data) {
  if (!data) {
    return;
  }

  const status = (data.status || '').toLowerCase();
  const message = data.message || '';

  if (status === 'started') {
    if (!statusOverlayActive) {
      beginStatusSession('Processing knowledge base');
    }
    if (message) {
      appendStatusLine(message, 'info');
    }
  } else if (status === 'completed') {
    let summary = 'Knowledge base ready.';
    if (data.result) {
      if (typeof data.result.total_chunks !== 'undefined') {
        summary = `Knowledge base ready — ${data.result.total_chunks} chunks indexed.`;
        if (typeof data.result.intents_detected !== 'undefined') {
          summary += ` Auto intents detected: ${data.result.intents_detected}.`;
        }
      } else if (typeof data.result.pages !== 'undefined') {
        summary = `Crawl completed — ${data.result.pages} pages processed.`;
      }
    }
    completeStatusSession(summary, 'success');
  } else if (status === 'error') {
    const errorMessage = message || 'An error occurred while processing.';
    completeStatusSession(errorMessage, 'error', { autoHide: false });
  } else {
    if (message) {
      appendStatusLine(message, 'info');
    }
  }
}

function showStatus(message, type = 'info') {
  const container = document.getElementById('inline-status');
  if (!container) {
    return;
  }
  if (!message) {
    container.classList.add('d-none');
    container.textContent = '';
    clearTimeout(inlineStatusTimer);
    return;
  }
  const classMap = {
    success: 'alert-success',
    error: 'alert-danger',
    warning: 'alert-warning',
    info: 'alert-info'
  };
  container.className = `alert ${classMap[type] || classMap.info}`;
  container.textContent = message;
  container.classList.remove('d-none');
  clearTimeout(inlineStatusTimer);
  inlineStatusTimer = setTimeout(() => container.classList.add('d-none'), 3000);
}

function updateProgressStatus(data) {
  if (!data) {
    return;
  }
  if (!statusOverlayActive) {
    beginStatusSession('Processing…');
  }
  const type = normalizeStatusType(data.type);
  if (data.message) {
    appendStatusLine(data.message, type);
  }
}

function clearBot() {
  if (!confirm('⚠️ WARNING: This will permanently delete ALL crawled data, the search index, conversation history, and reset all settings to defaults.\n\nAre you absolutely sure you want to continue?')) {
    return;
  }

  beginStatusSession('Clearing bot data');
  appendStatusLine('Removing stored content…', 'info');

  fetch('/api/clear-bot', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'success') {
        appendStatusLine('Bot data cleared successfully.', 'success');
        completeStatusSession('Bot reset to defaults.', 'success');
        showStatus('Bot data cleared and reset.', 'success');
        loadConfig();
        loadStats();
        document.getElementById('chat-messages').innerHTML = `
                <div class="alert alert-info chat-placeholder">
                    Welcome! This chatbot only answers questions based on the indexed website content. 
                    Configure the URL, crawl the site, and build the index to get started.
                </div>
            `;
        chatHistoryLoaded = false;
        const historyEl = document.getElementById('conversation-history');
        if (historyEl) {
          historyEl.innerHTML = '<p class="text-muted small">No conversations yet.</p>';
        }
      } else {
        const errorMsg = data.message || 'Unknown error clearing bot.';
        appendStatusLine(errorMsg, 'error');
        completeStatusSession('Failed to clear bot.', 'error', { autoHide: false });
        showStatus(`Error clearing bot: ${errorMsg}`, 'error');
      }
    })
    .catch(err => {
      console.error(err);
      appendStatusLine('Unexpected error during bot reset.', 'error');
      completeStatusSession('Failed to clear bot.', 'error', { autoHide: false });
      showStatus('Error clearing bot', 'error');
    });
}

function clearConversationHistory() {
  if (!confirm('Are you sure you want to delete all conversation history? This cannot be undone.')) {
    return;
  }

  fetch('/api/clear-conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  })
    .then(r => r.json())
    .then(data => {
      const historyContainer = document.getElementById('conversation-history');
      if (data.status === 'success') {
        showStatus('Conversation history cleared', 'success');
        if (historyContainer) {
          historyContainer.innerHTML = '<p class="text-muted small">No conversations yet.</p>';
        }
      } else {
        showStatus('Error clearing history: ' + data.message, 'error');
      }
    })
    .catch(err => {
      console.error(err);
      showStatus('Error clearing history', 'error');
    });
}

function loadConversations() {
  fetch('/api/conversations')
    .then(r => r.json())
    .then(data => {
      const container = document.getElementById('conversation-history');
      if (!container) {
        return;
      }

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
      const container = document.getElementById('conversation-history');
      if (container) {
        container.innerHTML = '<p class="text-danger small">Error loading history.</p>';
      }
    });
}

function saveFeedback(convId) {
  const feedbackInput = document.getElementById(`feedback-${convId}`);
  const feedback = feedbackInput.value.trim();

  fetch(`/api/conversations/${convId}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ feedback })
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

// Bot Intelligence Panel Functions
let intelligencePanelModal = null;
let allConversations = [];
let allIntents = [];

function openIntelligencePanel() {
  if (!intelligencePanelModal) {
    intelligencePanelModal = new bootstrap.Modal(document.getElementById('intelligencePanel'));
  }
  intelligencePanelModal.show();
  loadPanelConversations();
  loadIntents();
}

function loadPanelConversations() {
  fetch('/api/conversations')
    .then(r => r.json())
    .then(data => {
      allConversations = data.conversations || data;
      renderPanelConversations(allConversations);
    });
}

function renderPanelConversations(conversations) {
  const container = document.getElementById('panel-conversations');
  if (!conversations || conversations.length === 0) {
    container.innerHTML = '<p class="text-muted">No conversations yet.</p>';
    return;
  }

  container.innerHTML = conversations.map(conv => {
    const date = new Date(conv.timestamp).toLocaleString();
    const hasFeedback = conv.feedback && conv.feedback.length > 0;
    return `
            <div class="col-md-6 mb-2">
                <div class="card">
                    <div class="card-body p-2">
                        <div class="small"><strong>Q:</strong> ${escapeHtml(conv.question)}</div>
                        <div class="small text-muted"><strong>A:</strong> ${escapeHtml(conv.answer.substring(0, 150))}${conv.answer.length > 150 ? '...' : ''}</div>
                        <div class="text-muted" style="font-size: 0.7rem;">${date}</div>
                        ${hasFeedback ? `<div class="badge bg-warning text-dark mt-1"><i class="bi bi-chat-square-quote"></i> ${escapeHtml(conv.feedback)}</div>` : ''}
                    </div>
                </div>
            </div>
        `;
  }).join('');
}

function filterConversations() {
  const search = document.getElementById('conv-search').value.toLowerCase();
  const filtered = allConversations.filter(conv =>
    conv.question.toLowerCase().includes(search) ||
    conv.answer.toLowerCase().includes(search)
  );
  renderPanelConversations(filtered);
}

function autoDetectIntents() {
  beginStatusSession('Auto-detecting intents');
  appendStatusLine('Analyzing indexed content…', 'info');
  fetch('/api/auto-detect-intents', { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.status === 'error') {
        const errorMsg = data.error || 'Unable to detect intents.';
        appendStatusLine(errorMsg, 'error');
        completeStatusSession('Intent detection failed.', 'error', { autoHide: false });
        showStatus(`Error detecting intents: ${errorMsg}`, 'error');
        return Promise.reject('handled');
      }

      appendStatusLine(`Detected ${data.intents.length} potential intents.`, 'success');

      const promises = data.intents.map(intent =>
        fetch('/api/intents', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: intent.name,
            description: intent.description,
            patterns: intent.patterns || [],
            examples: intent.examples || [],
            auto_detected: true
          })
        })
      );

      return Promise.all(promises);
    })
    .then(() => {
      loadIntents();
      completeStatusSession('Intent detection complete.', 'success');
      showStatus('Intent detection complete.', 'success');
    })
    .catch(err => {
      if (err === 'handled') {
        return;
      }
      console.error(err);
      appendStatusLine('Unexpected error detecting intents.', 'error');
      completeStatusSession('Intent detection failed.', 'error', { autoHide: false });
      showStatus('Error detecting intents', 'error');
    });
}

function loadIntents() {
  fetch('/api/intents')
    .then(r => r.json())
    .then(data => {
      allIntents = data;
      renderIntents(data);
    });
}

function renderIntents(intents) {
  const container = document.getElementById('intents-list');
  if (!intents || intents.length === 0) {
    container.innerHTML = '<p class="text-muted">No intents defined. Click "Auto-Detect Intents" to get started!</p>';
    return;
  }

  container.innerHTML = intents.map((intent, idx) => {
    const actionTypeLabel = {
      'static': 'Static Response',
      'retrieval': 'Knowledge Base',
      'hybrid': 'Hybrid (Template + KB)'
    }[intent.action_type] || 'Unknown';

    const actionTypeBadge = {
      'static': 'bg-secondary',
      'retrieval': 'bg-primary',
      'hybrid': 'bg-warning text-dark'
    }[intent.action_type] || 'bg-secondary';

    const responses = intent.responses || [];
    const responsesHtml = responses.length > 0 ? responses.map((r, i) =>
      `<div class="mb-1 p-2 bg-light rounded">
                <small>${escapeHtml(r)}</small>
            </div>`
    ).join('') : '<small class="text-muted">No responses defined</small>';

    return `
        <div class="card mb-2">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-start">
                    <div class="flex-grow-1">
                        <h6>
                            ${escapeHtml(intent.name)} 
                            ${intent.auto_detected ? '<span class="badge bg-info">Auto</span>' : ''}
                            ${intent.enabled ? '<span class="badge bg-success">Active</span>' : '<span class="badge bg-secondary">Disabled</span>'}
                            <span class="badge ${actionTypeBadge}">${actionTypeLabel}</span>
                        </h6>
                        <p class="small text-muted mb-1">${escapeHtml(intent.description || '')}</p>
                        <div class="small">
                            <strong>Patterns:</strong> ${(intent.patterns || []).map(p => `<span class="badge bg-light text-dark">${escapeHtml(p)}</span>`).join(' ')}
                        </div>
                        <div class="small mt-1">
                            <strong>Examples:</strong> ${(intent.examples || []).length} questions
                        </div>
                        
                        <div class="mt-2">
                            <button class="btn btn-sm btn-link p-0" onclick="toggleResponses(${idx})">
                                <i class="bi bi-chevron-down" id="responses-icon-${idx}"></i>
                                ${responses.length} Response${responses.length !== 1 ? 's' : ''}
                            </button>
                        </div>
                        
                        <div id="responses-${idx}" style="display:none;" class="mt-2 border-top pt-2">
                            <div class="d-flex justify-content-between align-items-center mb-2">
                                <strong class="small">Responses:</strong>
                                <div>
                                    ${intent.action_type === 'hybrid' ?
        `<button class="btn btn-xs btn-outline-info btn-sm me-1" onclick="previewHybrid(${intent.id})">
                                            <i class="bi bi-eye"></i> Preview
                                        </button>` : ''}
                                    <button class="btn btn-xs btn-outline-primary btn-sm" onclick="editIntent(${intent.id})">
                                        <i class="bi bi-pencil"></i> Edit
                                    </button>
                                </div>
                            </div>
                            <div>${responsesHtml}</div>
                        </div>
                    </div>
                    <div>
                        <button class="btn btn-sm btn-outline-danger" onclick="deleteIntent(${intent.id})">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </div>
            </div>
        </div>
        `;
  }).join('');
}

function showCreateIntent() {
  document.getElementById('create-intent-form').style.display = 'block';
}

function cancelCreateIntent() {
  document.getElementById('create-intent-form').style.display = 'none';
  document.getElementById('new-intent-name').value = '';
  document.getElementById('new-intent-desc').value = '';
  document.getElementById('new-intent-patterns').value = '';
  document.getElementById('new-intent-examples').value = '';
}

function createIntent() {
  const name = document.getElementById('new-intent-name').value.trim();
  const description = document.getElementById('new-intent-desc').value.trim();
  const patterns = document.getElementById('new-intent-patterns').value.split(',').map(p => p.trim()).filter(p => p);
  const examples = document.getElementById('new-intent-examples').value.split('\n').filter(e => e.trim());

  if (!name) {
    alert('Please enter an intent name');
    return;
  }

  fetch('/api/intents', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description, patterns, examples })
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        alert('Error: ' + data.error);
      } else {
        cancelCreateIntent();
        loadIntents();
        showStatus('✓ Intent created!', 'success');
        setTimeout(() => showStatus('', 'info'), 2000);
      }
    });
}

function deleteIntent(intentId) {
  if (!confirm('Delete this intent?')) return;

  fetch(`/api/intents/${intentId}`, { method: 'DELETE' })
    .then(r => r.json())
    .then(data => {
      loadIntents();
      showStatus('✓ Intent deleted', 'success');
      setTimeout(() => showStatus('', 'info'), 2000);
    });
}

function toggleResponses(idx) {
  const responsesDiv = document.getElementById(`responses-${idx}`);
  const icon = document.getElementById(`responses-icon-${idx}`);
  if (responsesDiv.style.display === 'none') {
    responsesDiv.style.display = 'block';
    icon.className = 'bi bi-chevron-up';
  } else {
    responsesDiv.style.display = 'none';
    icon.className = 'bi bi-chevron-down';
  }
}

function editIntent(intentId) {
  const intent = allIntents.find(i => i.id === intentId);
  if (!intent) return;

  const responses = (intent.responses || []).join('\n');
  const newActionType = prompt(
    `Action Type for "${intent.name}":\n\n` +
    `Current: ${intent.action_type}\n\n` +
    `Options:\n` +
    `- static: Fixed response (no knowledge base)\n` +
    `- retrieval: Search knowledge base\n` +
    `- hybrid: Template + knowledge base (use {context} placeholder)\n\n` +
    `Enter new action type:`,
    intent.action_type
  );

  if (!newActionType || !['static', 'retrieval', 'hybrid'].includes(newActionType)) {
    if (newActionType !== null) alert('Invalid action type. Must be: static, retrieval, or hybrid');
    return;
  }

  const newResponses = prompt(
    `Response templates for "${intent.name}":\n\n` +
    `Action Type: ${newActionType}\n` +
    `${newActionType === 'hybrid' ? 'Use {context} for knowledge base content\n' : ''}\n` +
    `Enter one response per line:`,
    responses
  );

  if (newResponses === null) return;

  const responsesList = newResponses.split('\n').map(r => r.trim()).filter(r => r.length > 0);

  fetch(`/api/intents/${intentId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      action_type: newActionType,
      responses: responsesList
    })
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        alert('Error: ' + data.error);
      } else {
        loadIntents();
        showStatus('✓ Intent updated!', 'success');
        setTimeout(() => showStatus('', 'info'), 2000);
      }
    });
}

function previewHybrid(intentId) {
  const intent = allIntents.find(i => i.id === intentId);
  if (!intent || intent.action_type !== 'hybrid') return;

  showStatus('⟳ Generating preview...', 'info');

  fetch(`/api/intents/${intentId}/preview`, { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        alert('Error: ' + data.error);
        showStatus('', 'info');
        return;
      }

      const previewHtml = data.previews.map((p, i) => `
                <div class="mb-3">
                    <strong>Template ${i + 1}:</strong>
                    <div class="p-2 bg-light rounded mb-2"><small>${escapeHtml(p.template)}</small></div>
                    <strong>Preview:</strong>
                    <div class="p-2 bg-white border rounded"><small>${escapeHtml(p.preview)}</small></div>
                </div>
            `).join('');

      const modal = `
                <div class="modal fade" id="previewModal" tabindex="-1">
                    <div class="modal-dialog modal-lg">
                        <div class="modal-content">
                            <div class="modal-header">
                                <h5 class="modal-title">Hybrid Template Preview: ${escapeHtml(intent.name)}</h5>
                                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                            </div>
                            <div class="modal-body">
                                ${previewHtml}
                                <div class="alert alert-info small">
                                    <i class="bi bi-info-circle"></i> Preview uses sample query: "${escapeHtml(data.sample_query)}"
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;

      document.body.insertAdjacentHTML('beforeend', modal);
      const previewModal = new bootstrap.Modal(document.getElementById('previewModal'));
      document.getElementById('previewModal').addEventListener('hidden.bs.modal', function () {
        this.remove();
      });
      previewModal.show();
      showStatus('', 'info');
    });
}

function exportTrainingData() {
  fetch('/api/training-export')
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        alert('Error: ' + data.error);
        return;
      }

      document.getElementById('yaml-output').textContent = data.yaml;
      document.getElementById('training-stats').style.display = 'block';
      document.getElementById('training-stats').textContent =
        `✓ Exported ${data.intents_count} intents with ${data.examples_count} examples`;

      showStatus('✓ Training data exported!', 'success');
      setTimeout(() => showStatus('', 'info'), 2000);
    });
}
