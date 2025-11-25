let statusOverlayActive = false;
let statusHideTimer = null;
let inlineStatusTimer = null;
let chatHistoryLoaded = false;
const ACTIVE_BOT_STORAGE_KEY = 'intellibot.activeBot';
let bots = new Map();
let activeBotId = null;
let createBotModalInstance = null;
let currentConfig = null;
let botInitOverlayActive = false;
const botStatusCache = new Map();
const DEFAULT_CLIENT_CONFIG = {
  url: '',
  max_pages: 500,
  chunk_size: 900,
  chunk_overlap: 150,
  similarity_threshold: 0.4,
  top_k: 4,
};

function getInputValue(id) {
  const el = document.getElementById(id);
  return el ? el.value : '';
}

function setInputValue(id, value) {
  const el = document.getElementById(id);
  if (!el) {
    return;
  }
  el.value = value === undefined || value === null ? '' : value;
}

function applyConfigToForm(config = {}) {
  const merged = { ...DEFAULT_CLIENT_CONFIG, ...config };
  setInputValue('url-input', merged.url || '');
  setInputValue('max-pages-input', merged.max_pages);
  setInputValue('chunk-size-input', merged.chunk_size);
  setInputValue('chunk-overlap-input', merged.chunk_overlap);
  setInputValue('similarity-threshold', merged.similarity_threshold);
  setInputValue('top-k', merged.top_k);
}

function collectConfigFromForm() {
  const parseInteger = (value, fallback) => {
    const parsed = parseInt(value, 10);
    return Number.isNaN(parsed) ? fallback : parsed;
  };
  const parseFloatValue = (value, fallback) => {
    const parsed = parseFloat(value);
    return Number.isNaN(parsed) ? fallback : parsed;
  };

  return {
    url: getInputValue('url-input').trim(),
    max_pages: parseInteger(getInputValue('max-pages-input'), DEFAULT_CLIENT_CONFIG.max_pages),
    chunk_size: parseInteger(getInputValue('chunk-size-input'), DEFAULT_CLIENT_CONFIG.chunk_size),
    chunk_overlap: parseInteger(getInputValue('chunk-overlap-input'), DEFAULT_CLIENT_CONFIG.chunk_overlap),
    similarity_threshold: parseFloatValue(getInputValue('similarity-threshold'), DEFAULT_CLIENT_CONFIG.similarity_threshold),
    top_k: parseInteger(getInputValue('top-k'), DEFAULT_CLIENT_CONFIG.top_k)
  };
}

function getBot(botId) {
  const normalized = normalizeBotId(botId);
  if (normalized === null) {
    return null;
  }
  return bots.get(normalized) || null;
}

function getActiveBot() {
  return getBot(activeBotId);
}

function updateBotStatusBanner(bot) {
  const banner = document.getElementById('bot-status-banner');
  if (!banner) {
    return;
  }

  if (!bot) {
    banner.classList.add('d-none');
    banner.textContent = '';
    return;
  }

  const status = (bot.status || '').toLowerCase();
  const name = bot.name || 'Bot';
  let message = '';
  let cssClass = 'alert alert-info small';

  switch (status) {
    case 'initializing':
      cssClass = 'alert alert-info small';
      message = `"${name}" is initialising. This may take up to a minute.`;
      break;
    case 'training':
      cssClass = 'alert alert-warning small';
      message = `"${name}" is training a new model…`;
      break;
    case 'ready':
      cssClass = 'alert alert-success small';
      message = `"${name}" is ready to chat.`;
      break;
    case 'idle':
      cssClass = 'alert alert-info small';
      message = `"${name}" is idle. Index the knowledge base to train it.`;
      break;
    case 'error':
      cssClass = 'alert alert-danger small';
      message = bot.last_error
        ? `Setup error for "${name}": ${bot.last_error}`
        : `"${name}" encountered an error during setup.`;
      break;
    default:
      if (!status) {
        banner.classList.add('d-none');
        banner.textContent = '';
        return;
      }
      cssClass = 'alert alert-secondary small';
      message = `"${name}" status: ${status}.`;
      break;
  }

  banner.className = cssClass;
  banner.innerHTML = `<i class="bi bi-info-circle me-2"></i>${escapeHtml(message)}`;
  banner.classList.remove('d-none');
}

function handleBotLifecycle(bot, { force = false } = {}) {
  if (!bot) {
    return;
  }
  const status = (bot.status || '').toLowerCase();
  const name = bot.name || 'Bot';
  const previousStatus = botStatusCache.get(bot.id);
  if (!force && previousStatus === status) {
    return;
  }
  botStatusCache.set(bot.id, status);

  const notify = (msg, level = 'info') => showStatus(msg, level);

  if (status === 'initializing') {
    notify(`"${name}" is initialising. We'll let you know when it's ready.`, 'info');
    if (!statusOverlayActive && !botInitOverlayActive) {
      beginStatusSession(`Initialising ${name}…`);
      appendStatusLine('Preparing Rasa workspace…', 'info');
      botInitOverlayActive = true;
    } else if (botInitOverlayActive) {
      appendStatusLine('Still setting things up…', 'info');
    } else if (statusOverlayActive) {
      appendStatusLine(`Initialising ${name}…`, 'info');
    }
    return;
  }

  if (status === 'error') {
    const errMsg = bot.last_error
      ? `Error for "${name}": ${bot.last_error}`
      : `"${name}" encountered an error.`;
    notify(errMsg, 'error');
    if (botInitOverlayActive) {
      completeStatusSession(errMsg, 'error', { autoHide: false });
      botInitOverlayActive = false;
    }
    return;
  }

  if (botInitOverlayActive && (status === 'ready' || status === 'idle')) {
    completeStatusSession(`"${name}" is ready.`, 'success');
    botInitOverlayActive = false;
  }

  if (status === 'training') {
    notify(`"${name}" is training a new model…`, 'info');
    return;
  }

  if (status === 'ready') {
    notify(`"${name}" is ready.`, 'success');
    return;
  }

  if (status === 'idle') {
    notify(`"${name}" is idle and ready for indexing.`, 'info');
  }
}

function loadConfig() {
  if (normalizeBotId(activeBotId) === null) {
    currentConfig = null;
    applyConfigToForm({});
    return Promise.resolve(null);
  }

  return fetch(`/api/config${buildBotQuery()}`)
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || (data && data.error)) {
        const message = data && data.error ? data.error : 'Failed to load configuration.';
        showStatus(message, 'error');
        return null;
      }
      currentConfig = { ...data };
      applyConfigToForm(currentConfig);
      return currentConfig;
    })
    .catch(err => {
      console.error('Unable to load config', err);
      showStatus('Unable to load configuration.', 'error');
      return null;
    });
}

function saveConfig() {
  if (!ensureBotSelected('Select a bot before applying settings.')) {
    return;
  }

  const payload = collectConfigFromForm();
  showStatus('Saving configuration…', 'info');

  fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(attachBotId(payload))
  })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || (data && data.error)) {
        const message = data && data.error ? data.error : 'Unable to save configuration.';
        showStatus(message, 'error');
        return;
      }
      const updated = data && data.config ? data.config : payload;
      currentConfig = { ...updated };
      applyConfigToForm(currentConfig);
      showStatus('Configuration updated.', 'success');
    })
    .catch(err => {
      console.error('Failed to save configuration', err);
      showStatus('Unable to save configuration.', 'error');
    });
}

function renderStats(stats) {
  const statsCard = document.getElementById('stats-card');
  const placeholder = document.getElementById('stats-placeholder');
  const configCard = document.getElementById('config-card');
  const showConfigBtn = document.getElementById('show-config-btn');
  const forceConfig = configCard && configCard.dataset && configCard.dataset.forceVisible === 'true';

  if (!stats || stats.indexed === false) {
    if (statsCard) statsCard.classList.add('d-none');
    if (placeholder) placeholder.classList.add('d-none');
    if (configCard) {
      configCard.classList.remove('d-none');
      configCard.dataset.forceVisible = 'true';
    }
    if (showConfigBtn) {
      showConfigBtn.classList.add('d-none');
    }
    return;
  }

  if (placeholder) placeholder.classList.add('d-none');
  if (statsCard) statsCard.classList.remove('d-none');
  if (configCard) {
    if (forceConfig) {
      configCard.classList.remove('d-none');
    } else {
      configCard.classList.add('d-none');
      delete configCard.dataset.forceVisible;
    }
  }
  if (showConfigBtn) {
    showConfigBtn.classList.toggle('d-none', Boolean(forceConfig));
  }

  const urlEl = document.getElementById('stat-url');
  if (urlEl) {
    urlEl.textContent = stats.configured_url ? stats.configured_url : 'No URL configured';
  }

  const rawDocsEl = document.getElementById('stat-raw-docs');
  if (rawDocsEl) {
    const rawDocs = typeof stats.raw_documents === 'number' ? stats.raw_documents : 0;
    rawDocsEl.textContent = rawDocs.toLocaleString();
  }

  const chunksEl = document.getElementById('stat-chunks');
  if (chunksEl) {
    const chunks = typeof stats.total_chunks === 'number' ? stats.total_chunks : 0;
    chunksEl.textContent = chunks.toLocaleString();
  }

  const newEmbeddingsEl = document.getElementById('stat-new-embeddings');
  if (newEmbeddingsEl) {
    const newEmbeddings = typeof stats.new_embeddings === 'number' ? stats.new_embeddings : 0;
    newEmbeddingsEl.textContent = newEmbeddings.toLocaleString();
  }

  const reusedEmbeddingsEl = document.getElementById('stat-reused-embeddings');
  if (reusedEmbeddingsEl) {
    const reusedEmbeddings = typeof stats.reused_embeddings === 'number' ? stats.reused_embeddings : 0;
    reusedEmbeddingsEl.textContent = reusedEmbeddings.toLocaleString();
  }

  const profileEl = document.getElementById('stat-profile');
  if (profileEl) {
    const profile = stats.profile;
    if (profile && profile.error) {
      profileEl.innerHTML = `<span class="text-danger">${escapeHtml(String(profile.error))}</span>`;
    } else if (profile && profile.company_name) {
      const name = escapeHtml(String(profile.company_name));
      const summary = profile.summary ? escapeHtml(String(profile.summary)) : 'No summary available.';
      const voice = profile.brand_voice ? escapeHtml(String(profile.brand_voice)) : 'professional';
      const contact = profile.contact && typeof profile.contact === 'object' ? profile.contact : {};
      const contactLines = ['phone', 'email', 'website', 'address']
        .map(key => contact[key] ? `<div>${escapeHtml(key.charAt(0).toUpperCase() + key.slice(1))}: ${escapeHtml(String(contact[key]))}</div>` : '')
        .filter(Boolean)
        .join('');
      const voiceBadge = `<span class="badge bg-info text-dark">Voice: ${voice}</span>`;
      profileEl.innerHTML = `
        <div><strong>${name}</strong> ${voiceBadge}</div>
        <div class="mt-1">${summary}</div>
        ${contactLines ? `<div class="mt-1">${contactLines}</div>` : ''}
      `;
    } else {
      profileEl.innerHTML = '<em>Profile not generated</em>';
    }
  }

  const sourcesEl = document.getElementById('stat-sources');
  if (sourcesEl) {
    const sources = Array.isArray(stats.document_sources) ? stats.document_sources : [];
    if (sources.length === 0) {
      sourcesEl.innerHTML = '<em>No documents loaded</em>';
    } else {
      const listHtml = sources.map((src, idx) => {
        const href = src.url || '#';
        const label = src.label || href;
        const safeLabel = escapeHtml(String(label));
        const safeHref = escapeHtml(String(href));
        return `<div class="mb-1">
            <a href="${safeHref}" target="_blank" rel="noopener noreferrer" class="source-link">
              ${safeLabel}
            </a>
          </div>`;
      }).join('');
      sourcesEl.innerHTML = listHtml;
    }
  }
}

function loadStats() {
  if (normalizeBotId(activeBotId) === null) {
    renderStats(null);
    return Promise.resolve(null);
  }

  return fetch(`/api/stats${buildBotQuery()}`)
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || (data && data.error)) {
        const message = data && data.error ? data.error : 'Unable to load statistics.';
        showStatus(message, 'error');
        renderStats(null);
        return null;
      }
      renderStats(data);
      return data;
    })
    .catch(err => {
      console.error('Unable to load statistics', err);
      showStatus('Unable to load statistics.', 'error');
      renderStats(null);
      return null;
    });
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
  const safeText = escapeHtml(typeof text === 'string' ? text : '');

  messageDiv.innerHTML = `
        <div class="message-header">
            <i class="bi bi-person-circle"></i> You
        </div>
        <div class="message-content">${safeText.replace(/\n/g, '<br>')}</div>
        ${timestampText ? `<div class="message-meta">${escapeHtml(timestampText)}</div>` : ''}
    `;
  container.appendChild(messageDiv);

  if (!options.suppressScroll) {
    container.scrollTop = container.scrollHeight;
  }
}

function loadChatHistory(options = {}) {
  const { force = false } = options;
  if (chatHistoryLoaded && !force) {
    return;
  }

  if (normalizeBotId(activeBotId) === null) {
    resetChatView();
    chatHistoryLoaded = true;
    return;
  }

  fetch(`/api/conversations${buildBotQuery()}`)
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok) {
        const message = data && data.error ? data.error : 'Unable to load chat history.';
        showStatus(message, 'error');
        resetChatView();
        chatHistoryLoaded = false;
        return;
      }

      const conversations = Array.isArray(data) ? data : (data.conversations || []);
      const container = document.getElementById('chat-messages');
      if (!container) {
        return;
      }

      container.innerHTML = '';

      if (!conversations.length) {
        resetChatView();
        chatHistoryLoaded = true;
        return;
      }

      const ordered = [...conversations].reverse();
      ordered.forEach(conv => {
        if (conv.question) {
          addUserMessage(conv.question, { timestamp: conv.timestamp, suppressScroll: true });
        }
        const botPayload = {
          answer: conv.answer,
          sources: conv.sources || [],
          similarity_scores: conv.similarity_scores || [],
          bot_id: conv.bot_id,
          rasa: true
        };
        addBotMessage(botPayload, {
          conversationId: conv.id,
          timestamp: conv.timestamp,
          feedback: conv.feedback || '',
          suppressScroll: true
        });
      });

      container.scrollTop = container.scrollHeight;
      chatHistoryLoaded = true;
    })
    .catch(err => {
      console.error('Unable to load chat history', err);
      showStatus('Unable to load chat history.', 'error');
      chatHistoryLoaded = false;
    });
}

function normalizeBotId(value) {
  if (value === undefined || value === null || value === '') {
    return null;
  }
  const parsed = parseInt(value, 10);
  return Number.isNaN(parsed) ? null : parsed;
}

function matchesActiveBot(botId) {
  return normalizeBotId(botId) === normalizeBotId(activeBotId);
}

function resetChatView() {
  const container = document.getElementById('chat-messages');
  if (!container) {
    return;
  }
  const message = normalizeBotId(activeBotId) === null ? 'Select or create a bot to begin chatting.' : 'Welcome!';
  container.innerHTML = `<div class="alert alert-info chat-placeholder">${message}</div>`;
  chatHistoryLoaded = false;
}

function refreshBotScopedData(options = {}) {
  const { resetChat = true } = options;
  if (resetChat) {
    resetChatView();
  }
  if (normalizeBotId(activeBotId) === null) {
    const statsCard = document.getElementById('stats-card');
    const placeholder = document.getElementById('stats-placeholder');
    if (statsCard) statsCard.classList.add('d-none');
    if (placeholder) placeholder.classList.remove('d-none');
    const configFields = [
      'url-input',
      'max-pages-input',
      'chunk-size-input',
      'chunk-overlap-input',
      'similarity-threshold',
      'top-k'
    ];
    configFields.forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.value = '';
      }
    });
    updateBotDependentControls();
    return;
  }
  updateBotDependentControls();
  loadConfig();
  loadStats();
  loadChatHistory();
  loadIntents();
  loadPanelConversations();
}

function buildBotQuery(params = {}) {
  const query = new URLSearchParams(params);
  const botId = normalizeBotId(activeBotId);
  if (botId !== null) {
    query.set('bot_id', botId);
  }
  const queryString = query.toString();
  return queryString ? `?${queryString}` : '';
}

function attachBotId(payload = {}) {
  const botId = normalizeBotId(activeBotId);
  if (botId !== null) {
    return { ...payload, bot_id: botId };
  }
  return { ...payload };
}

function startIndexing() {
  if (!ensureBotSelected('Select a bot before indexing.')) {
    return;
  }

  const botId = normalizeBotId(activeBotId);
  const indexBtn = document.getElementById('index-btn');
  if (indexBtn) {
    indexBtn.disabled = true;
  }

  const configCard = document.getElementById('config-card');
  if (configCard) {
    configCard.dataset.forceVisible = 'true';
    configCard.classList.remove('d-none');
  }
  const showConfigBtn = document.getElementById('show-config-btn');
  if (showConfigBtn) {
    showConfigBtn.classList.add('d-none');
  }

  const formData = new FormData();
  formData.append('bot_id', botId);

  const urlInput = document.getElementById('url-input');
  const maxPagesInput = document.getElementById('max-pages-input');
  const chunkSizeInput = document.getElementById('chunk-size-input');
  const chunkOverlapInput = document.getElementById('chunk-overlap-input');
  const similarityInput = document.getElementById('similarity-threshold');
  const topKInput = document.getElementById('top-k');

  const urlValue = urlInput && typeof urlInput.value === 'string' ? urlInput.value.trim() : '';
  if (urlValue) {
    formData.append('url', urlValue);
  }

  const coerceInt = (input, fallback) => {
    if (!input || input.value === undefined) {
      return fallback;
    }
    const parsed = parseInt(input.value, 10);
    return Number.isFinite(parsed) ? parsed : fallback;
  };

  const coerceFloat = (input, fallback) => {
    if (!input || input.value === undefined) {
      return fallback;
    }
    const parsed = parseFloat(input.value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };

  const maxPages = coerceInt(maxPagesInput, 500);
  const chunkSize = coerceInt(chunkSizeInput, 900);
  const chunkOverlap = coerceInt(chunkOverlapInput, 150);
  const similarityThreshold = coerceFloat(similarityInput, 0.4);
  const topK = coerceInt(topKInput, 4);

  formData.append('max_pages', maxPages);
  formData.append('chunk_size', chunkSize);
  formData.append('chunk_overlap', chunkOverlap);
  formData.append('similarity_threshold', similarityThreshold);
  formData.append('top_k', topK);

  const uploadInput = document.getElementById('doc-upload');
  if (uploadInput && uploadInput.files) {
    Array.from(uploadInput.files).forEach(file => {
      formData.append('documents', file);
    });
  }

  beginStatusSession('Processing knowledge base');
  showStatus('Starting knowledge base build…', 'info');

  fetch('/api/index_all', {
    method: 'POST',
    body: formData
  })
    .then(r => r.json().then(data => ({ ok: r.ok, status: r.status, data })))
    .then(({ ok, status, data }) => {
      if (!ok || (data && data.error)) {
        const message = data && data.error ? data.error : `Failed to start indexing (status ${status})`;
        appendStatusLine(message, 'error');
        completeStatusSession('Indexing failed to start.', 'error', { autoHide: false });
        showStatus(message, 'error');
        return;
      }
      appendStatusLine('Indexing job accepted. Follow progress below.', 'info');
    })
    .catch(err => {
      console.error('Failed to start indexing', err);
      appendStatusLine('Unable to start indexing job.', 'error');
      completeStatusSession('Indexing failed to start.', 'error', { autoHide: false });
      showStatus('Unable to start indexing.', 'error');
    })
    .finally(() => {
      if (indexBtn) {
        indexBtn.disabled = false;
      }
    });
}

function sendMessage() {
  if (!ensureBotSelected('Select a bot before chatting.')) {
    return;
  }
  const input = document.getElementById('chat-input');
  if (!input) {
    return;
  }
  const text = input.value.trim();
  if (!text) {
    return;
  }
  const sendBtn = document.getElementById('chat-send-btn');
  if (sendBtn) {
    sendBtn.disabled = true;
    setTimeout(() => {
      if (sendBtn.disabled) {
        sendBtn.disabled = false;
      }
    }, 6000);
  }
  addUserMessage(text);
  socket.emit('chat_message', attachBotId({ message: text }));
  input.value = '';
  input.focus();
}

function updateBotDependentControls() {
  const activeBot = getActiveBot();
  const hasBot = Boolean(activeBot);
  const status = activeBot ? (activeBot.status || '').toLowerCase() : '';
  const botReady = status === 'ready' || status === 'idle';

  updateBotStatusBanner(activeBot);

  const dependentInputs = [
    'url-input',
    'max-pages-input',
    'chunk-size-input',
    'chunk-overlap-input',
    'similarity-threshold',
    'top-k',
    'doc-upload',
    'chat-input'
  ];
  dependentInputs.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.disabled = !botReady;
    }
  });
  const indexBtn = document.getElementById('index-btn');
  if (indexBtn) indexBtn.disabled = !botReady;
  const saveBtn = document.getElementById('save-config-btn');
  if (saveBtn) saveBtn.disabled = !botReady;
  const sendBtn = document.getElementById('chat-send-btn');
  if (sendBtn) sendBtn.disabled = !botReady;
  const clearBtn = document.getElementById('bot-clear-history-btn');
  if (clearBtn) clearBtn.disabled = !hasBot;
  const destroyBtn = document.getElementById('bot-destroy-btn');
  if (destroyBtn) destroyBtn.disabled = !hasBot;
  const restartBtn = document.getElementById('bot-restart-service-btn');
  if (restartBtn) restartBtn.disabled = !(hasBot && status === 'ready');
}

function setActiveBot(botId, options = {}) {
  const normalized = normalizeBotId(botId);
  const previous = normalizeBotId(activeBotId);
  const shouldRefresh = options.refresh !== false;

  if (normalized === null) {
    activeBotId = null;
    localStorage.removeItem(ACTIVE_BOT_STORAGE_KEY);
    renderBotSelector();
    updateBotDependentControls();
    if (botInitOverlayActive && statusOverlayActive) {
      completeStatusSession('No active bot selected.', 'info');
    }
    botInitOverlayActive = false;
    if (shouldRefresh) {
      const resetChat = previous !== null;
      refreshBotScopedData({ resetChat });
    }
    return;
  }

  if (!bots.has(normalized)) {
    showStatus('Selected bot is not available. Reloading bots…', 'warning');
    loadBots();
    return;
  }

  activeBotId = normalized;
  localStorage.setItem(ACTIVE_BOT_STORAGE_KEY, String(normalized));

  renderBotSelector();
  updateBotDependentControls();
  handleBotLifecycle(getActiveBot(), { force: true });

  if (shouldRefresh) {
    const resetChat = previous !== normalized;
    refreshBotScopedData({ resetChat });
  }

  if (options.focusChatInput) {
    const input = document.getElementById('chat-input');
    if (input) {
      setTimeout(() => input.focus(), 50);
    }
  }
}

function submitCreateBotForm(event) {
  event.preventDefault();
  const form = event.target;
  const nameInput = form.querySelector('#create-bot-name');
  const descInput = form.querySelector('#create-bot-description');
  const submitBtn = form.querySelector('#create-bot-submit');
  const name = (nameInput ? nameInput.value : '').trim();
  const description = descInput ? descInput.value.trim() : '';

  if (!name) {
    showStatus('Bot name is required.', 'error');
    return;
  }

  if (submitBtn) submitBtn.disabled = true;
  showStatus('Creating bot…', 'info');

  fetch('/api/bots', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description })
  })
    .then(r => r.json().then(data => ({ ok: r.ok, status: r.status, data })))
    .then(({ ok, status, data }) => {
      if (!ok) {
        const message = data && data.error ? data.error : `Failed to create bot (status ${status})`;
        showStatus(message, 'error');
        return;
      }
      if (createBotModalInstance) {
        createBotModalInstance.hide();
      }
      if (form) {
        form.reset();
      }
      bots.set(data.id, data);
      setActiveBot(data.id);
      loadBots();
      showStatus(`Bot "${data.name}" created.`, 'success');
    })
    .catch(err => {
      console.error('Failed to create bot', err);
      showStatus('Unable to create bot', 'error');
    })
    .finally(() => {
      if (submitBtn) submitBtn.disabled = false;
    });
}

function destroyActiveBot() {
  if (!ensureBotSelected('Select a bot before destroying it.')) {
    return;
  }
  const botId = normalizeBotId(activeBotId);
  const bot = botId !== null ? bots.get(botId) : null;
  const botName = bot && bot.name ? bot.name : 'this bot';
  if (!confirm(`This will permanently delete "${botName}" and all of its trained data. Continue?`)) {
    return;
  }
  showStatus('Destroying bot…', 'info');
  fetch(`/api/bots/${botId}`, {
    method: 'DELETE'
  })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || (data && data.error)) {
        const message = data && data.error ? data.error : 'Failed to destroy bot.';
        showStatus(message, 'error');
        return;
      }
      bots.delete(botId);
      if (normalizeBotId(activeBotId) === botId) {
        activeBotId = null;
        localStorage.removeItem(ACTIVE_BOT_STORAGE_KEY);
        resetChatView();
      }
      updateBotDependentControls();
      loadBots();
      showStatus('Bot destroyed successfully.', 'success');
    })
    .catch(err => {
      console.error('Failed to destroy bot', err);
      showStatus('Unable to destroy bot', 'error');
    });
}

function restartBotService() {
  if (!ensureBotSelected('Select a bot before restarting its service.')) {
    return;
  }
  const botId = normalizeBotId(activeBotId);
  const bot = botId !== null ? bots.get(botId) : null;
  const botName = bot && bot.name ? bot.name : 'this bot';
  const btn = document.getElementById('bot-restart-service-btn');
  if (btn) btn.disabled = true;
  showStatus(`Restarting Rasa service for "${botName}"…`, 'info');

  fetch(`/api/bots/${botId}/restart-service`, { method: 'POST' })
    .then(r => r.json().then(data => ({ ok: r.ok, status: r.status, data })))
    .then(({ ok, status, data }) => {
      if (!ok || (data && data.error)) {
        const message = data && data.error
          ? data.error
          : `Failed to restart service (status ${status}).`;
        showStatus(message, 'error');
        return;
      }
      if (data && data.bot) {
        upsertBot(data.bot);
        renderBotSelector();
        updateBotDependentControls();
      }
      showStatus(`Rasa service restarted for "${botName}".`, 'success');
    })
    .catch(err => {
      console.error('Failed to restart Rasa service', err);
      showStatus('Unable to restart Rasa service.', 'error');
    })
    .finally(() => {
      if (btn) btn.disabled = false;
    });
}

document.addEventListener('DOMContentLoaded', () => {
  const modalEl = document.getElementById('createBotModal');
  if (modalEl && typeof bootstrap !== 'undefined') {
    createBotModalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
    modalEl.addEventListener('hidden.bs.modal', () => {
      const form = document.getElementById('create-bot-form');
      if (form) {
        form.reset();
        const submitBtn = form.querySelector('#create-bot-submit');
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }
  const createForm = document.getElementById('create-bot-form');
  if (createForm) {
    createForm.addEventListener('submit', submitCreateBotForm);
  }
  const showConfigBtnEl = document.getElementById('show-config-btn');
  if (showConfigBtnEl) {
    showConfigBtnEl.addEventListener('click', () => {
      const card = document.getElementById('config-card');
      if (card) {
        card.dataset.forceVisible = 'true';
        card.classList.remove('d-none');
        card.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      showConfigBtnEl.classList.add('d-none');
      const indexBtn = document.getElementById('index-btn');
      if (indexBtn) {
        indexBtn.focus();
      }
    });
  }
  updateBotDependentControls();
});


function ensureBotSelected(message) {
  const botId = normalizeBotId(activeBotId);
  if (botId === null) {
    if (message) {
      showStatus(message, 'warning');
    }
    return false;
  }
  return true;
}

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

function loadBots() {
  fetch('/api/bots')
    .then(r => r.json())
    .then(list => {
      if (!Array.isArray(list)) {
        return;
      }
      const previousActive = normalizeBotId(activeBotId);
      const previousCount = bots.size || 0;

      bots = new Map(list.map(bot => [bot.id, bot]));
      for (const key of Array.from(botStatusCache.keys())) {
        if (!bots.has(key)) {
          botStatusCache.delete(key);
        }
      }

      if (bots.size === 0) {
        activeBotId = null;
        localStorage.removeItem(ACTIVE_BOT_STORAGE_KEY);
        renderBotSelector();
        updateBotDependentControls();
        resetChatView();
        if (previousCount > 0) {
          showStatus('All bots removed. Create a new bot to begin.', 'warning');
        }
        return;
      }

      restoreActiveBotSelection();
      renderBotSelector();
      updateBotDependentControls();

      const currentActive = normalizeBotId(activeBotId);
      const resetChat = currentActive !== previousActive;
      refreshBotScopedData({ resetChat });
      handleBotLifecycle(getActiveBot(), { force: true });
    })
    .catch(err => {
      console.error('Unable to load bots', err);
    });
}
function restoreActiveBotSelection() {
  const stored = localStorage.getItem(ACTIVE_BOT_STORAGE_KEY);
  let candidate = normalizeBotId(stored);
  if (candidate !== null && bots.has(candidate)) {
    activeBotId = candidate;
    return;
  }

  candidate = null;
  for (const bot of bots.values()) {
    if (bot.status === 'ready') {
      candidate = bot.id;
      break;
    }
  }
  if (candidate === null) {
    const iterator = bots.values().next();
    if (!iterator.done) {
      candidate = iterator.value.id;
    }
  }
  activeBotId = candidate;
  if (candidate === null) {
    localStorage.removeItem(ACTIVE_BOT_STORAGE_KEY);
  } else {
    localStorage.setItem(ACTIVE_BOT_STORAGE_KEY, String(candidate));
  }
}

function upsertBot(bot) {
  if (!bot || typeof bot.id === 'undefined') {
    return;
  }
  const previous = bots.get(bot.id);
  bots.set(bot.id, bot);
  if (activeBotId === null && bot.status === 'ready') {
    activeBotId = bot.id;
    localStorage.setItem(ACTIVE_BOT_STORAGE_KEY, String(bot.id));
  }
  const isActive = normalizeBotId(activeBotId) === normalizeBotId(bot.id);
  const statusChanged = previous && previous.status !== bot.status;
  renderBotSelector();
  updateBotDependentControls();
  handleBotLifecycle(bot, { force: Boolean(statusChanged) });
  if (isActive && statusChanged && bot.status === 'ready') {
    refreshBotScopedData({ resetChat: false });
  }
}

function renderBotSelector() {
  const select = document.getElementById('bot-selector');
  if (!select) {
    return;
  }

  const active = normalizeBotId(activeBotId);
  select.innerHTML = '';

  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = 'Select a bot…';
  placeholder.disabled = true;
  if (active === null) {
    placeholder.selected = true;
  }
  select.appendChild(placeholder);

  const sortedBots = Array.from(bots.values()).sort((a, b) => {
    return (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base' });
  });

  sortedBots.forEach(bot => {
    const option = document.createElement('option');
    option.value = String(bot.id);
    const statusLabel = (bot.status || 'unknown').replace(/_/g, ' ');
    option.textContent = `${bot.name || 'Bot'} (${statusLabel})`;
    if (normalizeBotId(bot.id) === active) {
      option.selected = true;
    }
    select.appendChild(option);
  });

  if (active === null) {
    select.value = '';
  }
}

function addBotMessage(data, options = {}) {
  const container = document.getElementById('chat-messages');
  if (!container) {
    return;
  }
  clearChatPlaceholder();
  const messageDiv = document.createElement('div');
  const isRasa = Boolean(data && data.rasa);
  const botInfo = isRasa && data && data.bot_id ? bots.get(data.bot_id) : null;
  const botLabel = botInfo && botInfo.name ? botInfo.name : (isRasa ? 'Rasa Bot' : 'IntelliBot');
  const headerIcon = isRasa ? 'bi-diagram-3' : 'bi-robot';
  messageDiv.className = `message message-bot${isRasa ? ' message-bot-rasa' : ''}`;
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
  const rasaBadge = isRasa ? '<span class="badge bg-secondary ms-2">Rasa</span>' : '';
  messageDiv.innerHTML = `
        <div class="message-header">
            <i class="bi ${headerIcon}"></i> ${escapeHtml(botLabel)}
            ${rasaBadge}
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
            <span class="text-muted small">${hasFeedback ? 'Feedback recorded:' : 'No feedback yet'}</span>
            ${hasFeedback ? `<span class="badge bg-light text-dark feedback-recorded">${safeFeedback}</span>` : ''}
            <button type="button" class="btn btn-link btn-sm p-0" onclick="toggleFeedbackForm(${conversationId})">${hasFeedback ? 'Update note' : 'Add note'}</button>
            <button type="button" class="btn btn-link btn-sm text-danger" onclick="deleteConversation(${conversationId})"><i class="bi bi-trash"></i> Delete</button>
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
    body: JSON.stringify(attachBotId({ feedback }))
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

function deleteConversation(conversationId) {
  if (!conversationId) {
    return;
  }
  if (!confirm('Delete this conversation? This cannot be undone.')) {
    return;
  }
  fetch(`/api/conversations/${conversationId}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(attachBotId({}))
  })
    .then(async response => {
      const text = await response.text();
      let payload = {};
      try {
        payload = text ? JSON.parse(text) : {};
      } catch (err) {
        console.error('Delete conversation parse error:', err, text);
        showStatus('Unable to delete conversation (invalid response).', 'error');
        return;
      }
      if (!response.ok || payload.status !== 'deleted') {
        showStatus(payload.message || payload.error || 'Unable to delete conversation.', 'error');
        return;
      }
      showStatus('Conversation deleted.', 'success');
      loadChatHistory({ force: true });
      loadPanelConversations();
    })
    .catch(err => {
      console.error('Unable to delete conversation', err);
      showStatus('Unable to delete conversation.', 'error');
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
        if (typeof data.result.new_embeddings === 'number') {
          summary += ` New vectors: ${data.result.new_embeddings}.`;
        }
        if (typeof data.result.reused_embeddings === 'number') {
          summary += ` Reused vectors: ${data.result.reused_embeddings}.`;
        }
        if (data.result.profile_summary && data.result.profile_summary.company_name) {
          summary += ` Profile: ${data.result.profile_summary.company_name}.`;
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
  if (!ensureBotSelected('Select a bot before clearing data.')) {
    return;
  }
  const confirmation = `⚠️ WARNING: This will permanently delete ALL crawled data, the search index, conversation history, and reset all settings to defaults.

Are you absolutely sure you want to continue?`;
  if (!confirm(confirmation)) {
    return;
  }

  beginStatusSession('Clearing bot data');
  appendStatusLine('Removing stored content…', 'info');

  fetch('/api/clear-bot', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(attachBotId({}))
  })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || data.status !== 'success') {
        const message = data && data.message ? data.message : 'Failed to clear bot data.';
        appendStatusLine(message, 'error');
        completeStatusSession('Bot reset failed.', 'error', { autoHide: false });
        showStatus(message, 'error');
        return;
      }
      appendStatusLine('Bot data cleared successfully.', 'success');
      completeStatusSession('Bot reset to defaults.', 'success');
      showStatus('Bot data cleared and reset.', 'success');
      refreshBotScopedData();
      loadBots();
    })
    .catch(err => {
      console.error(err);
      appendStatusLine('Unexpected error during bot reset.', 'error');
      completeStatusSession('Failed to clear bot.', 'error', { autoHide: false });
      showStatus('Error clearing bot', 'error');
    });
}



function clearConversationHistory() {
  if (!ensureBotSelected('Select a bot before clearing chat history.')) {
    return;
  }
  if (!confirm('Are you sure you want to delete all conversation history? This cannot be undone.')) {
    return;
  }

  fetch('/api/clear-conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(attachBotId({}))
  })
    .then(r => r.json())
    .then(data => {
      const historyContainer = document.getElementById('conversation-history');
      if (data.status === 'success') {
        showStatus('Conversation history cleared', 'success');
        if (historyContainer) {
          historyContainer.innerHTML = '<p class="text-muted small">No conversations yet.</p>';
        }
        resetChatView();
        loadChatHistory();
        loadPanelConversations();
      } else {
        showStatus('Error clearing history: ' + (data.message || 'Unknown error'), 'error');
      }
    })
    .catch(err => {
      console.error(err);
      showStatus('Error clearing history', 'error');
    });
}

function loadConversations() {
  if (normalizeBotId(activeBotId) === null) {
    const container = document.getElementById('conversation-history');
    if (container) {
      container.innerHTML = '<p class="text-muted small">Select or create a bot to view conversations.</p>';
    }
    return;
  }
  fetch(`/api/conversations${buildBotQuery()}`)
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
        const questionHtml = escapeHtml(conv.question || '').replace(/\n/g, '<br>');
        const answerHtml = escapeHtml(conv.answer || '').replace(/\n/g, '<br>');

        return `
          <div class="card mb-2 conversation-item">
            <div class="card-body p-2">
              <div class="small fw-semibold text-muted">Question</div>
              <div class="conversation-text">${questionHtml || '<em>No question text</em>'}</div>
              <div class="small fw-semibold text-muted mt-2">Answer</div>
              <div class="conversation-text text-muted">${answerHtml || '<em>No answer recorded</em>'}</div>
              <div class="text-muted" style="font-size: 0.75rem;">${date}</div>
              <div class="mt-2">
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
  if (!ensureBotSelected()) {
    return;
  }
  const feedbackInput = document.getElementById(`feedback-${convId}`);
  const feedback = feedbackInput.value.trim();

  fetch(`/api/conversations/${convId}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(attachBotId({ feedback }))
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
  if (normalizeBotId(activeBotId) === null) {
    renderPanelConversations([]);
    return;
  }
  const query = buildBotQuery();
  fetch(`/api/conversations${query}`)
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok && data && data.error) {
        showStatus(data.error, 'warning');
        allConversations = [];
        renderPanelConversations(allConversations);
        return;
      }
      allConversations = data.conversations || data;
      renderPanelConversations(allConversations);
    })
    .catch(err => {
      console.error('Unable to load panel conversations', err);
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
    const questionHtml = escapeHtml(conv.question || '').replace(/\n/g, '<br>');
    const answerHtml = escapeHtml(conv.answer || '').replace(/\n/g, '<br>');
    return `
            <div class="col-md-6 mb-2">
                <div class="card">
                    <div class="card-body p-2">
                        <div class="small fw-semibold text-muted">Question</div>
                        <div class="conversation-text">${questionHtml || '<em>No question text</em>'}</div>
                        <div class="small fw-semibold text-muted mt-2">Answer</div>
                        <div class="conversation-text text-muted">${answerHtml || '<em>No answer recorded</em>'}</div>
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
  if (!ensureBotSelected('Select a bot before detecting intents.')) {
    return;
  }
  beginStatusSession('Auto-detecting intents');
  appendStatusLine('Analyzing indexed content…', 'info');
  fetch('/api/auto-detect-intents', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(attachBotId({}))
  })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || data.status === 'error') {
        const errorMsg = (data && data.error) || 'Unable to detect intents.';
        appendStatusLine(errorMsg, 'error');
        completeStatusSession('Intent detection failed.', 'error', { autoHide: false });
        showStatus(`Error detecting intents: ${errorMsg}`, 'error');
        return Promise.reject('handled');
      }

      const intents = data.intents || [];
      appendStatusLine(`Detected ${intents.length} potential intents.`, 'success');

      const promises = intents.map(intent =>
        fetch('/api/intents', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(attachBotId({
            name: intent.name,
            description: intent.description,
            patterns: intent.patterns || [],
            examples: intent.examples || [],
            auto_detected: true
          }))
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
  if (normalizeBotId(activeBotId) === null) {
    renderIntents([]);
    return;
  }
  const query = buildBotQuery();
  fetch(`/api/intents${query}`)
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok && data && data.error) {
        showStatus(data.error, 'error');
        renderIntents([]);
        return;
      }
      allIntents = Array.isArray(data) ? data : (data.intents || []);
      renderIntents(allIntents);
    })
    .catch(err => {
      console.error('Unable to load intents', err);
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
  if (!ensureBotSelected('Select a bot before creating intents.')) {
    return;
  }
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
    body: JSON.stringify(attachBotId({ name, description, patterns, examples }))
  })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || (data && data.error)) {
        alert('Error: ' + (data && data.error ? data.error : 'Unable to create intent.'));
        return;
      }
      cancelCreateIntent();
      loadIntents();
      showStatus('✓ Intent created!', 'success');
      setTimeout(() => showStatus('', 'info'), 2000);
    });
}

function deleteIntent(intentId) {
  if (!ensureBotSelected('Select a bot before deleting intents.')) {
    return;
  }
  if (!confirm('Delete this intent?')) return;

  fetch(`/api/intents/${intentId}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(attachBotId({}))
  })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || (data && data.error)) {
        showStatus('Error deleting intent: ' + (data && data.error ? data.error : 'Unknown error'), 'error');
        return;
      }
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
  if (!ensureBotSelected('Select a bot before editing intents.')) {
    return;
  }
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
    body: JSON.stringify(attachBotId({
      action_type: newActionType,
      responses: responsesList
    }))
  })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || (data && data.error)) {
        alert('Error: ' + (data && data.error ? data.error : 'Unable to update intent.'));
        return;
      }
      loadIntents();
      showStatus('✓ Intent updated!', 'success');
      setTimeout(() => showStatus('', 'info'), 2000);
    });
}

function previewHybrid(intentId) {
  if (!ensureBotSelected('Select a bot to preview intents.')) {
    return;
  }
  const intent = allIntents.find(i => i.id === intentId);
  if (!intent || intent.action_type !== 'hybrid') return;

  showStatus('⟳ Generating preview...', 'info');

  fetch(`/api/intents/${intentId}/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(attachBotId({}))
  })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || (data && data.error)) {
        alert('Error: ' + (data && data.error ? data.error : 'Unable to generate preview.'));
        showStatus('', 'info');
        return;
      }

      const previews = data.previews || [];
      const previewHtml = previews.map((p, i) => `
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

function triggerTraining(includeConversations = true) {
  if (!ensureBotSelected('Select a bot before training.')) {
    return;
  }
  const bot = getActiveBot();
  if (!bot) {
    return;
  }
  showStatus(`Training "${bot.name}"...`, 'info');
  fetch(`/api/bots/${bot.id}/train`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ include_conversations: includeConversations })
  })
    .then(r => r.json().then(data => ({ ok: r.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || (data && data.error)) {
        showStatus(data.error || 'Unable to start training.', 'error');
        const stats = document.getElementById('training-stats');
        if (stats) {
          stats.style.display = 'block';
          stats.className = 'alert alert-danger';
          stats.textContent = data.error || 'Unable to start training.';
        }
        return;
      }
      showStatus('Training started.', 'success');
      const stats = document.getElementById('training-stats');
      if (stats) {
        stats.style.display = 'block';
        stats.className = 'alert alert-info';
        stats.textContent = 'Training started…';
      }
    })
    .catch(err => {
      console.error('Training error', err);
      showStatus('Unable to start training.', 'error');
      const stats = document.getElementById('training-stats');
      if (stats) {
        stats.style.display = 'block';
        stats.className = 'alert alert-danger';
        stats.textContent = 'Unable to start training.';
      }
    });
}

function exportTrainingData() {
  if (!ensureBotSelected('Select a bot before exporting training data.')) {
    return;
  }
  fetch(`/api/training-export${buildBotQuery()}`)
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

const socket = io();

socket.on('connect', () => {
  console.log('Connected to server');
  loadConfig();
  loadStats();
  loadChatHistory();
  loadBots();
});

socket.on('chat_response', (data) => {
  if (!matchesActiveBot(data.bot_id)) {
    return;
  }
  addBotMessage(data);
  const sendBtn = document.getElementById('chat-send-btn');
  if (sendBtn) {
    sendBtn.disabled = false;
  }
});

socket.on('crawl_status', (data) => {
  if (!matchesActiveBot(data.bot_id)) {
    return;
  }
  updateCrawlStatus(data);
  if (data.status === 'completed') {
    loadStats();
  }
});

socket.on('crawl_progress', (data) => {
  if (!matchesActiveBot(data.bot_id)) {
    return;
  }
  updateProgressStatus(data);
});

socket.on('index_status', (data) => {
  if (!matchesActiveBot(data.bot_id)) {
    return;
  }
  updateCrawlStatus(data);
  if (data.status === 'completed' || data.status === 'error') {
    const configCard = document.getElementById('config-card');
    if (configCard) {
      delete configCard.dataset.forceVisible;
    }
  }
  if (data.status === 'completed') {
    loadStats();
    loadIntents();
  }
});

socket.on('index_progress', (data) => {
  if (!matchesActiveBot(data.bot_id)) {
    return;
  }
  updateProgressStatus(data);
});

socket.on('bot_update', (data) => {
  if (data && data.deleted) {
    const id = normalizeBotId(data.id);
    if (id !== null) {
      bots.delete(id);
      botStatusCache.delete(id);
    }
  } else if (data) {
    upsertBot(data);
    const activeId = normalizeBotId(activeBotId);
    const incomingId = normalizeBotId(data.id);
    if (activeId !== null && incomingId === activeId) {
      const stats = document.getElementById('training-stats');
      if (stats && data.status) {
        stats.style.display = 'block';
        if (data.status === 'training') {
          stats.className = 'alert alert-info';
          stats.textContent = 'Training in progress…';
        } else if (data.status === 'ready') {
          stats.className = 'alert alert-success';
          stats.textContent = 'Training complete. Bot is ready.';
        } else if (data.status === 'error') {
          stats.className = 'alert alert-danger';
          stats.textContent = data.last_error || 'Training failed.';
        }
      }
    }
  }
  loadBots();
});
