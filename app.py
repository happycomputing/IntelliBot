import eventlet
eventlet.monkey_patch()

import os
import json
import glob
import shutil
import subprocess
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, abort
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from retrieval_engine import RetrievalEngine
from tools.crawl_site import crawl_site
from tools.index_kb import index_kb
from tools.process_docs import process_uploaded_documents
from models import db, Conversation, Intent, Bot
from bot_manager import (
    rasa_available,
    slugify_name,
    unique_slug,
    project_path_for,
    ensure_absolute_project_path,
    to_relative_project_path,
    latest_model_path,
    RASA_PYTHON,
    init_rasa_project,
    train_rasa_project,
)
import threading
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET', 'dev-secret-key')

# Enable development-centric settings when requested
env_setting = os.environ.get('INTELLIBOT_ENV', os.environ.get('FLASK_ENV', '')).lower()
if env_setting == 'development' or os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes'):
    app.config.update(
        ENV='development',
        DEBUG=True,
        TEMPLATES_AUTO_RELOAD=True,
    )
    app.jinja_env.auto_reload = True

# Database configuration (SQLite only)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, 'intellibot.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{SQLITE_PATH}"
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

db.init_app(app)

# Database availability flag
DB_AVAILABLE = False

BOT_STATUS_INITIALIZING = 'initializing'
BOT_STATUS_IDLE = 'idle'
BOT_STATUS_TRAINING = 'training'
BOT_STATUS_READY = 'ready'
BOT_STATUS_ERROR = 'error'

RASA_RESPONSE_TIMEOUT = int(os.environ.get('RASA_RESPONSE_TIMEOUT', '60'))
RASA_BRIDGE_SCRIPT = os.path.join(BASE_DIR, 'scripts', 'rasa_respond.py')


def ensure_column_exists(table_name, column_name, ddl):
    """Ensure a specific column exists on a table (light-weight migration)."""
    try:
        result = db.session.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
        existing = {row[1] for row in result}
        if column_name not in existing:
            db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))
            db.session.commit()
    except Exception as exc:
        db.session.rollback()
        app.logger.warning("Unable to ensure column %s.%s: %s", table_name, column_name, exc)


def ensure_schema_columns():
    """Add new columns introduced after initial deploy (SQLite-friendly)."""
    ensure_column_exists('conversations', 'bot_id', 'bot_id INTEGER')
    ensure_column_exists('intents', 'bot_id', 'bot_id INTEGER')


def resolve_bot(bot_id):
    """Fetch a Bot instance from an identifier."""
    if not bot_id or not DB_AVAILABLE:
        return None
    try:
        bot_id_int = int(bot_id)
    except (TypeError, ValueError):
        return None
    return Bot.query.get(bot_id_int)


def get_retrieval_cache_key(bot):
    return bot.slug if bot else '__default__'


def get_storage_paths(bot=None):
    """Return filesystem locations for knowledge assets for a bot/default."""
    if bot:
        base_dir = os.path.join('kb', bot.slug)
        config_path = os.path.join(base_dir, 'config.json')
    else:
        base_dir = 'kb'
        config_path = CONFIG_FILE

    base_abs = os.path.abspath(os.path.join(os.getcwd(), base_dir))
    config_abs = os.path.abspath(os.path.join(os.getcwd(), config_path))
    raw_dir = os.path.join(base_abs, 'raw')
    index_dir = os.path.join(base_abs, 'index')
    uploads_dir = os.path.join(base_abs, 'uploads')

    return {
        'base_dir': base_abs,
        'raw_dir': raw_dir,
        'index_dir': index_dir,
        'uploads_dir': uploads_dir,
        'config_path': config_abs,
    }


def ensure_storage_dirs(paths):
    """Create directories for bot knowledge storage."""
    os.makedirs(paths['base_dir'], exist_ok=True)
    os.makedirs(paths['raw_dir'], exist_ok=True)
    os.makedirs(paths['index_dir'], exist_ok=True)
    os.makedirs(paths['uploads_dir'], exist_ok=True)
    config_dir = os.path.dirname(paths['config_path'])
    if config_dir and not os.path.exists(config_dir):
        os.makedirs(config_dir, exist_ok=True)


def run_background_task(target, *args, **kwargs):
    """Helper to start daemonised background work."""
    if hasattr(eventlet, 'spawn_n'):
        # Prefer eventlet greenlets when available to avoid thread scheduling issues
        eventlet.spawn_n(target, *args, **kwargs)
        return None
    thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    thread.start()
    return thread


def safe_commit():
    """Commit the current database session, rolling back on failure."""
    try:
        db.session.commit()
        return True, None
    except SQLAlchemyError as exc:
        db.session.rollback()
        return False, exc


def emit_bot_update(bot):
    """Send a websocket update for the supplied bot instance."""
    if bot:
        socketio.emit('bot_update', bot.to_dict())


def generate_bot_slug(name):
    """Create a unique slug for a bot based on its display name."""
    base_slug = slugify_name(name or '')

    def exists(candidate):
        if Bot.query.filter_by(slug=candidate).first():
            return True
        candidate_path = project_path_for(candidate)
        return os.path.exists(candidate_path)

    return unique_slug(base_slug, exists)


def run_rasa_turn(bot, message, sender_id=None):
    """Invoke the Rasa runtime for a single user message."""
    if not rasa_available():
        return {"status": "error", "message": "Rasa runtime not available"}

    model_path = latest_model_path(bot.project_path)
    if not model_path:
        return {"status": "error", "message": "Bot has no trained model yet"}

    if not os.path.exists(RASA_BRIDGE_SCRIPT):
        return {"status": "error", "message": "Bridge script missing"}

    env = os.environ.copy()
    env.setdefault('RASA_TELEMETRY_ENABLED', 'false')
    env.setdefault('RASA_LOG_LEVEL', 'ERROR')
    env.setdefault('LOG_LEVEL', 'ERROR')

    command = [
        RASA_PYTHON,
        RASA_BRIDGE_SCRIPT,
        '--model',
        model_path,
        '--message',
        message,
    ]
    if sender_id:
        command.extend(['--sender', sender_id])

    project_path = ensure_absolute_project_path(bot.project_path)

    result = subprocess.run(
        command,
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=RASA_RESPONSE_TIMEOUT,
        check=False,
        env=env,
    )

    if result.returncode != 0:
        stderr = (result.stderr or '').strip()
        stdout = (result.stdout or '').strip()
        error_message = stderr or stdout or 'Rasa response failed'
        return {"status": "error", "message": error_message}

    payload_raw = (result.stdout or '').strip()
    if not payload_raw:
        return {"status": "error", "message": "Empty response from Rasa"}

    def try_decode(raw_text):
        raw_text = (raw_text or '').strip()
        if not raw_text:
            return None
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            return None

    payload = try_decode(payload_raw)
    if payload is None and '\n' in payload_raw:
        for line in reversed(payload_raw.splitlines()):
            payload = try_decode(line)
            if payload is not None:
                break

    if payload is None:
        if result.stderr:
            app.logger.debug("Rasa stderr: %s", result.stderr.strip())
        app.logger.debug("Unable to decode Rasa response: %s", payload_raw)
        return {
            "status": "error",
            "message": "Malformed response from Rasa runtime",
        }

    return payload


def initialize_bot_project(bot_id, project_path):
    """Background task to create the filesystem structure for a bot."""
    with app.app_context():
        try:
            abs_path = ensure_absolute_project_path(project_path)
            init_rasa_project(abs_path)
        except Exception as exc:
            db.session.rollback()
            bot = Bot.query.get(bot_id)
            if not bot:
                return
            bot.status = BOT_STATUS_ERROR
            bot.last_error = str(exc)
            bot.updated_at = datetime.utcnow()
            ok, db_err = safe_commit()
            if not ok:
                bot = Bot.query.get(bot_id)
                if not bot:
                    return
                bot.status = BOT_STATUS_ERROR
                bot.last_error = f"{str(exc)} (db: {db_err})"
                bot.updated_at = datetime.utcnow()
                safe_commit()
            emit_bot_update(bot)
            return

        bot = Bot.query.get(bot_id)
        if not bot:
            return
        bot.status = BOT_STATUS_IDLE
        bot.last_error = ''
        bot.updated_at = datetime.utcnow()
        ok, db_err = safe_commit()
        if not ok:
            bot = Bot.query.get(bot_id)
            if not bot:
                return
            bot.status = BOT_STATUS_ERROR
            bot.last_error = f"Database error after init: {db_err}"
            bot.updated_at = datetime.utcnow()
            safe_commit()
        emit_bot_update(bot)


def train_bot_project(bot_id, project_path):
    """Background task to run rasa train for a bot."""
    with app.app_context():
        abs_path = ensure_absolute_project_path(project_path)
        success, error = train_rasa_project(abs_path)
        bot = Bot.query.get(bot_id)
        if not bot:
            return

        if success:
            bot.status = BOT_STATUS_READY
            bot.last_error = ''
            bot.last_trained_at = datetime.utcnow()
        else:
            bot.status = BOT_STATUS_ERROR
            bot.last_error = error or 'Training failed'
        bot.updated_at = datetime.utcnow()

        ok, db_err = safe_commit()
        if not ok:
            bot = Bot.query.get(bot_id)
            if not bot:
                return
            bot.status = BOT_STATUS_ERROR
            bot.last_error = f"Database error after training: {db_err}"
            bot.updated_at = datetime.utcnow()
            safe_commit()
        emit_bot_update(bot)

def init_database():
    """Initialize database in background to avoid blocking startup"""
    global DB_AVAILABLE
    with app.app_context():
        try:
            db.create_all()
            DB_AVAILABLE = True
            ensure_schema_columns()
            print("Database initialized successfully")
        except Exception as e:
            print(f"❌ Database initialization error: {e}")
            print("⚠️  Conversation logging is DISABLED. Chat will work but conversations won't be saved.")

# Start database initialization in background
threading.Thread(target=init_database, daemon=True).start()

CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "url": "",
    "max_pages": 500,
    "chunk_size": 900,
    "chunk_overlap": 150,
    "similarity_threshold": 0.40,
    "top_k": 4,
}

# Lazy-loaded retrieval engines per bot/default
_retrieval_cache = {}

def normalize_url(url, default=None):
    """Ensure URLs include a scheme and strip whitespace."""
    if url is None:
        return default or ''
    url = url.strip()
    if not url:
        return default or ''
    if not url.lower().startswith(('http://', 'https://')):
        url = f'https://{url}'
    return url

def get_retrieval(bot=None):
    """Get or initialize retrieval engine (lazy loading) for the selected bot."""
    key = get_retrieval_cache_key(bot)
    engine = _retrieval_cache.get(key)
    if engine is None:
        storage = get_storage_paths(bot)
        ensure_storage_dirs(storage)
        config = load_config(bot)
        engine = RetrievalEngine(
            index_dir=storage['index_dir'],
            similarity_threshold=config.get('similarity_threshold', DEFAULT_CONFIG['similarity_threshold']),
            top_k=config.get('top_k', DEFAULT_CONFIG['top_k'])
        )
        _retrieval_cache[key] = engine
    return engine

def load_config(bot=None):
    storage = get_storage_paths(bot)
    ensure_storage_dirs(storage)
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(storage['config_path']):
        try:
            with open(storage['config_path'], 'r') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                config.update({k: v for k, v in loaded.items() if v is not None})
        except Exception as exc:
            app.logger.warning("Unable to load config %s: %s", storage['config_path'], exc)
    return config

def save_config(config, bot=None):
    storage = get_storage_paths(bot)
    ensure_storage_dirs(storage)
    with open(storage['config_path'], 'w') as f:
        json.dump(config, f, indent=2)

@app.route('/health')
def health():
    """Fast health check endpoint for deployment"""
    return jsonify({"status": "ok"}), 200

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/config', methods=['GET'])
def get_config():
    bot_id = request.args.get('bot_id')
    bot = resolve_bot(bot_id)
    if bot_id and bot is None:
        return jsonify({"error": "Bot not found"}), 404
    config = load_config(bot)
    return jsonify(config)

@app.route('/api/config', methods=['POST'])
def update_config():
    payload = request.json or {}
    bot_id = payload.pop('bot_id', None)
    bot = resolve_bot(bot_id)
    if bot_id and bot is None:
        return jsonify({"error": "Bot not found"}), 404
    updated_config = load_config(bot)
    if isinstance(payload, dict):
        payload_url = payload.get('url', updated_config.get('url'))
        payload['url'] = normalize_url(payload_url)
        updated_config.update({k: v for k, v in payload.items() if v is not None})
    save_config(updated_config, bot)
    # Update retrieval engine settings immediately
    engine = get_retrieval(bot)
    engine.similarity_threshold = updated_config.get('similarity_threshold', engine.similarity_threshold)
    engine.top_k = updated_config.get('top_k', engine.top_k)
    engine._loaded = False
    return jsonify({"status": "success", "config": updated_config})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    bot_id = request.args.get('bot_id')
    bot = resolve_bot(bot_id)
    if bot_id and bot is None:
        return jsonify({"error": "Bot not found"}), 404

    storage = get_storage_paths(bot)
    ensure_storage_dirs(storage)

    stats = get_retrieval(bot).get_stats()
    
    # Get raw document count and sources
    raw_files = glob.glob(os.path.join(storage['raw_dir'], "*.json"))
    stats['raw_documents'] = len(raw_files)
    
    # Extract document sources (URLs)
    sources = []
    seen_sources = set()
    for raw_file in raw_files:
        try:
            with open(raw_file, 'r') as f:
                doc = json.load(f)
                url = doc.get('url', 'Unknown')
                label = doc.get('label') or url
                if not url:
                    continue
                if url in seen_sources:
                    continue
                seen_sources.add(url)
                sources.append({
                    "url": url,
                    "label": label
                })
        except Exception:
            pass
    
    stats['document_sources'] = sorted(
        sources,
        key=lambda entry: str(entry.get('label') or entry.get('url', '')).lower()
    )
    
    config = load_config(bot)
    stats['configured_url'] = config.get('url', '')
    stats['bot_id'] = bot.id if bot else None
    
    return jsonify(stats)

@app.route('/uploads/<path:filename>')
def serve_uploaded_document(filename):
    uploads_dir = os.path.abspath(os.path.join(os.getcwd(), 'kb', 'uploads'))
    relative_path = filename

    parts = filename.split('/', 1)
    if len(parts) == 2:
        candidate_slug, remainder = parts
        candidate_dir = os.path.abspath(os.path.join(os.getcwd(), 'kb', candidate_slug, 'uploads'))
        if os.path.isdir(candidate_dir):
            uploads_dir = candidate_dir
            relative_path = remainder

    requested_path = os.path.abspath(os.path.join(uploads_dir, relative_path))
    if os.path.commonpath([uploads_dir, requested_path]) != uploads_dir:
        abort(404)
    if not os.path.exists(requested_path):
        abort(404)
    return send_from_directory(uploads_dir, relative_path)

@app.route('/api/crawl', methods=['POST'])
def start_crawl():
    data = request.json
    bot = resolve_bot(data.get('bot_id'))
    if data.get('bot_id') and bot is None:
        return jsonify({"error": "Bot not found"}), 404
    storage = get_storage_paths(bot)
    ensure_storage_dirs(storage)
    url = normalize_url(data.get('url'), default='https://www.officems.co.za/')
    max_pages = data.get('max_pages', 500)
    
    def crawl_progress(status_type, message):
        socketio.emit('crawl_progress', {
            'type': status_type,
            'message': message,
            'bot_id': bot.id if bot else None
        })
    
    def crawl_task():
        try:
            socketio.emit('crawl_status', {
                'status': 'started',
                'message': f'Starting crawl of {url}...',
                'bot_id': bot.id if bot else None
            })
            result = crawl_site(
                url,
                max_pages,
                progress_callback=crawl_progress,
                output_dir=storage['raw_dir']
            )
            socketio.emit('crawl_status', {
                'status': 'completed',
                'result': result,
                'bot_id': bot.id if bot else None
            })
        except Exception as e:
            socketio.emit('crawl_status', {
                'status': 'error',
                'message': str(e),
                'bot_id': bot.id if bot else None
            })
    
    thread = threading.Thread(target=crawl_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({"status": "started"})

@app.route('/api/index', methods=['POST'])
def start_indexing():
    data = request.json
    bot = resolve_bot(data.get('bot_id'))
    if data.get('bot_id') and bot is None:
        return jsonify({"error": "Bot not found"}), 404
    storage = get_storage_paths(bot)
    ensure_storage_dirs(storage)
    chunk_size = data.get('chunk_size', 900)
    chunk_overlap = data.get('chunk_overlap', 150)
    
    def index_progress(status_type, message):
        socketio.emit('index_progress', {
            'type': status_type,
            'message': message,
            'bot_id': bot.id if bot else None
        })

    def index_task():
        try:
            socketio.emit('index_status', {
                'status': 'started',
                'message': 'Building vector index...',
                'bot_id': bot.id if bot else None
            })
            result = index_kb(
                chunk_size,
                chunk_overlap,
                progress_callback=index_progress,
                raw_dir=storage['raw_dir'],
                index_dir=storage['index_dir'],
                config_path=storage['config_path'],
            )
            get_retrieval(bot)._loaded = False
            socketio.emit('index_status', {'status': 'completed', 'result': result, 'bot_id': bot.id if bot else None})
        except Exception as e:
            socketio.emit('index_status', {'status': 'error', 'message': str(e), 'bot_id': bot.id if bot else None})
    
    thread = threading.Thread(target=index_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({"status": "started"})

@app.route('/api/index_all', methods=['POST'])
def index_all():
    """
    Combined workflow: Clear existing index, crawl URL (if provided),
    process uploaded documents, and index everything together.
    """
    bot_id = request.form.get('bot_id')
    bot = resolve_bot(bot_id)
    if bot_id and bot is None:
        return jsonify({"error": "Bot not found"}), 404

    storage = get_storage_paths(bot)
    ensure_storage_dirs(storage)

    url = normalize_url(request.form.get('url'))

    def parse_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def parse_float(value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    current_config = load_config(bot)
    max_pages = parse_int(request.form.get('max_pages'), current_config.get('max_pages', 500))
    chunk_size = parse_int(request.form.get('chunk_size'), current_config.get('chunk_size', 900))
    chunk_overlap = parse_int(request.form.get('chunk_overlap'), current_config.get('chunk_overlap', 150))
    similarity_threshold = parse_float(
        request.form.get('similarity_threshold'),
        current_config.get('similarity_threshold', 0.40)
    )
    top_k = parse_int(request.form.get('top_k'), current_config.get('top_k', 4))

    uploaded_files_raw = request.files.getlist('documents')
    uploaded_files = []
    for file in uploaded_files_raw:
        uploaded_files.append({
            'filename': file.filename,
            'content': file.read()
        })

    new_config = current_config.copy()
    new_config.update({
        'url': url or current_config.get('url', ''),
        'max_pages': max_pages,
        'chunk_size': chunk_size,
        'chunk_overlap': chunk_overlap,
        'similarity_threshold': similarity_threshold,
        'top_k': top_k
    })
    save_config(new_config, bot)

    retrieval = get_retrieval(bot)
    retrieval.similarity_threshold = similarity_threshold
    retrieval.top_k = top_k

    url_prefix = f"/uploads/{bot.slug}" if bot else "/uploads"

    def progress(status_type, message):
        socketio.emit('index_progress', {
            'type': status_type,
            'message': message,
            'bot_id': bot.id if bot else None
        })

    def combined_task():
        try:
            progress('info', 'Clearing previous knowledge base...')
            for path in (storage['raw_dir'], storage['index_dir'], storage['uploads_dir']):
                if os.path.exists(path):
                    shutil.rmtree(path)
                os.makedirs(path, exist_ok=True)

            if url:
                progress('info', f'Learning from website: {url}...')
                socketio.emit('index_status', {
                    'status': 'started',
                    'message': f'Learning from {url}...',
                    'bot_id': bot.id if bot else None
                })

                def crawl_progress(status_type, msg):
                    progress(status_type, msg)

                crawl_result = crawl_site(
                    url,
                    max_pages,
                    progress_callback=crawl_progress,
                    output_dir=storage['raw_dir']
                )
                progress('success', f"✓ Learned from {crawl_result['pages']} pages")

            if uploaded_files:
                progress('info', f'Processing {len(uploaded_files)} uploaded documents...')
                processed = process_uploaded_documents(
                    uploaded_files,
                    raw_dir=storage['raw_dir'],
                    upload_dir=storage['uploads_dir'],
                    url_prefix=url_prefix,
                )
                progress('success', f"Processed {len(processed)} documents")

            progress('info', 'Building knowledge index...')

            def index_progress_cb(status_type, msg):
                progress(status_type, msg)

            index_result = index_kb(
                chunk_size,
                chunk_overlap,
                progress_callback=index_progress_cb,
                raw_dir=storage['raw_dir'],
                index_dir=storage['index_dir'],
                config_path=storage['config_path'],
            )
            get_retrieval(bot)._loaded = False

            intents_detected = 0
            if DB_AVAILABLE:
                progress('info', 'Detecting intents from indexed knowledge...')
                from tools.detect_intents import auto_detect_intents
                from actions import ActionHandler
                from sqlalchemy.exc import SQLAlchemyError

                with app.app_context():
                    try:
                        detection_result = auto_detect_intents(raw_dir=storage['raw_dir'])
                    except Exception as e:
                        progress('warning', f"Intent detection failed: {e}")
                        detection_result = {'status': 'error', 'error': str(e)}

                    if detection_result.get('status') == 'success':
                        suggested_intents = detection_result.get('intents', []) or []
                        if suggested_intents:
                            try:
                                Intent.query.filter_by(auto_detected=True, bot_id=bot.id if bot else None).delete(synchronize_session=False)
                                db.session.commit()
                            except SQLAlchemyError as e:
                                db.session.rollback()
                                progress('warning', f"Could not clear previous auto intents: {e}")

                            for intent_data in suggested_intents:
                                name = (intent_data.get('name') or '').strip()
                                if not name:
                                    continue

                                existing_intent = Intent.query.filter_by(name=name, bot_id=bot.id if bot else None).first()
                                if existing_intent:
                                    if not existing_intent.auto_detected:
                                        continue
                                    db.session.delete(existing_intent)
                                    db.session.flush()

                                description = intent_data.get('description', '')
                                patterns = intent_data.get('patterns') or []
                                if isinstance(patterns, str):
                                    patterns = [p.strip() for p in patterns.split(',') if p.strip()]
                                examples = intent_data.get('examples') or []
                                if isinstance(examples, str):
                                    examples = [line.strip() for line in examples.splitlines() if line.strip()]

                                defaults = ActionHandler.get_default_responses_for_intent(name, description)
                                responses = intent_data.get('responses')
                                if responses is None or isinstance(responses, str):
                                    responses = defaults['responses']
                                action_type = intent_data.get('action_type') or defaults['action_type']

                                intent = Intent(
                                    bot_id=bot.id if bot else None,
                                    name=name,
                                    description=description,
                                    patterns=patterns,
                                    examples=examples,
                                    auto_detected=True,
                                    enabled=True,
                                    action_type=action_type,
                                    responses=responses
                                )
                                db.session.add(intent)
                                intents_detected += 1

                            try:
                                db.session.commit()
                                progress('success', f"✓ Auto-detected {intents_detected} intents")
                            except SQLAlchemyError as e:
                                db.session.rollback()
                                progress('warning', f"Failed to save auto intents: {e}")
                                intents_detected = 0
                        else:
                            progress('warning', 'No intents detected from content.')
                    elif detection_result.get('error'):
                        progress('warning', detection_result['error'])
            else:
                progress('warning', 'Database unavailable: skipping intent detection.')
                intents_detected = 0

            index_result['intents_detected'] = intents_detected
            index_result['bot_id'] = bot.id if bot else None
            progress('success', f"✓ Knowledge base ready! {index_result['total_chunks']} chunks indexed")
            socketio.emit('index_status', {
                'status': 'completed',
                'result': index_result,
                'bot_id': bot.id if bot else None
            })

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            app.logger.error("Indexing error: %s", error_details)
            progress('error', str(e))
            socketio.emit('index_status', {
                'status': 'error',
                'message': str(e),
                'bot_id': bot.id if bot else None
            })

    thread = threading.Thread(target=combined_task, daemon=True)
    thread.start()

    return jsonify({"status": "started"})


@app.route('/api/conversations', methods=['GET'])
def get_conversations():
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable", "conversations": []}), 503
    
    bot_id = request.args.get('bot_id')
    bot = resolve_bot(bot_id)
    if bot_id and bot is None:
        return jsonify({"error": "Bot not found", "conversations": []}), 404

    try:
        query = Conversation.query.order_by(Conversation.timestamp.desc())
        if bot:
            query = query.filter(Conversation.bot_id == bot.id)
        else:
            query = query.filter(Conversation.bot_id.is_(None))
        conversations = query.limit(50).all()
        return jsonify([conv.to_dict() for conv in conversations])
    except Exception as e:
        return jsonify({"error": str(e), "conversations": []}), 500

@app.route('/api/conversations/<int:conv_id>/feedback', methods=['POST'])
def add_feedback(conv_id):
    if not DB_AVAILABLE:
        return jsonify({"status": "error", "message": "Database unavailable"}), 503
    
    data = request.json
    feedback = data.get('feedback', '')
    
    try:
        conversation = Conversation.query.get(conv_id)
        if conversation:
            conversation.feedback = feedback
            db.session.commit()
            return jsonify({"status": "success", "conversation": conversation.to_dict()})
        return jsonify({"status": "error", "message": "Conversation not found"}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/clear-conversations', methods=['POST'])
def clear_conversations():
    if not DB_AVAILABLE:
        return jsonify({"status": "error", "message": "Database unavailable"}), 503
    
    data = request.get_json(silent=True) or {}
    bot_id = data.get('bot_id')
    bot = resolve_bot(bot_id)
    if bot_id and bot is None:
        return jsonify({"status": "error", "message": "Bot not found"}), 404

    try:
        query = db.session.query(Conversation)
        if bot:
            query = query.filter(Conversation.bot_id == bot.id)
        else:
            query = query.filter(Conversation.bot_id.is_(None))
        query.delete()
        db.session.commit()
        return jsonify({"status": "success", "message": "All conversation history cleared"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/clear-bot', methods=['POST'])
def clear_bot():
    data = request.get_json(silent=True) or {}
    bot_id = data.get('bot_id')
    bot = resolve_bot(bot_id)
    if bot_id and bot is None:
        return jsonify({"status": "error", "message": "Bot not found"}), 404

    storage = get_storage_paths(bot)
    ensure_storage_dirs(storage)

    try:
        # Clear database data if database is available
        if DB_AVAILABLE:
            try:
                query_conv = db.session.query(Conversation)
                query_intents = db.session.query(Intent)
                if bot:
                    query_conv = query_conv.filter(Conversation.bot_id == bot.id)
                    query_intents = query_intents.filter(Intent.bot_id == bot.id)
                else:
                    query_conv = query_conv.filter(Conversation.bot_id.is_(None))
                    query_intents = query_intents.filter(Intent.bot_id.is_(None))
                query_conv.delete()
                query_intents.delete()
                db.session.commit()
            except Exception as db_error:
                db.session.rollback()
                app.logger.warning("Failed to clear database data: %s", db_error)
        
        # Clear crawled documents and uploads
        for path in (storage['raw_dir'], storage['index_dir'], storage['uploads_dir']):
            if os.path.exists(path):
                shutil.rmtree(path)
            os.makedirs(path, exist_ok=True)
        
        # Reset config to default and reload
        save_config(DEFAULT_CONFIG.copy(), bot)
        reset_config = load_config(bot)
        
        # Reset retrieval engine with reloaded config
        engine = get_retrieval(bot)
        engine._loaded = False
        engine.similarity_threshold = reset_config.get('similarity_threshold', DEFAULT_CONFIG['similarity_threshold'])
        engine.top_k = reset_config.get('top_k', DEFAULT_CONFIG['top_k'])
        
        return jsonify({
            "status": "success",
            "message": "Bot data cleared and reset to defaults",
            "bot_id": bot.id if bot else None
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/bots', methods=['GET', 'POST'])
def manage_bots():
    """List all bots or create a new Rasa bot project."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 503

    if request.method == 'GET':
        bots = Bot.query.order_by(Bot.created_at.asc()).all()
        return jsonify([bot.to_dict() for bot in bots])

    if not rasa_available():
        return jsonify({"error": "Rasa environment not available"}), 503

    payload = request.json or {}
    name = (payload.get('name') or '').strip()
    description = (payload.get('description') or '').strip()

    if not name:
        return jsonify({"error": "Bot name is required"}), 400

    try:
        slug = generate_bot_slug(name)
    except SQLAlchemyError as exc:
        db.session.rollback()
        return jsonify({"error": f"Unable to generate bot slug: {exc}"}), 500

    project_path = project_path_for(slug)
    project_path_rel = to_relative_project_path(project_path)

    bot = Bot(
        name=name,
        description=description,
        slug=slug,
        project_path=project_path_rel,
        status=BOT_STATUS_INITIALIZING,
        last_error='',
    )

    db.session.add(bot)
    ok, err = safe_commit()
    if not ok:
        return jsonify({"error": f"Failed to create bot: {err}"}), 500

    storage = get_storage_paths(bot)
    ensure_storage_dirs(storage)
    save_config(DEFAULT_CONFIG.copy(), bot)

    emit_bot_update(bot)
    run_background_task(initialize_bot_project, bot.id, project_path)

    return jsonify(bot.to_dict()), 202


@app.route('/api/bots/<int:bot_id>/train', methods=['POST'])
def train_bot(bot_id):
    """Trigger asynchronous training for a specific bot."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 503
    if not rasa_available():
        return jsonify({"error": "Rasa environment not available"}), 503

    bot = Bot.query.get(bot_id)
    if not bot:
        return jsonify({"error": "Bot not found"}), 404

    if bot.status in {BOT_STATUS_INITIALIZING, BOT_STATUS_TRAINING}:
        return jsonify({"error": "Bot is busy"}), 409

    bot.status = BOT_STATUS_TRAINING
    bot.last_error = ''
    bot.updated_at = datetime.utcnow()

    ok, err = safe_commit()
    if not ok:
        return jsonify({"error": f"Failed to update bot: {err}"}), 500

    emit_bot_update(bot)
    run_background_task(train_bot_project, bot.id, bot.project_path)

    return jsonify({"status": "started", "bot": bot.to_dict()}), 202


@app.route('/api/bots/<int:bot_id>', methods=['DELETE'])
def delete_bot(bot_id):
    """Completely remove a bot, its knowledge base, and related data."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 503

    bot = Bot.query.get(bot_id)
    if not bot:
        return jsonify({"error": "Bot not found"}), 404

    storage = get_storage_paths(bot)
    project_path = ensure_absolute_project_path(bot.project_path)
    cache_key = get_retrieval_cache_key(bot)

    try:
        # Remove filesystem assets first
        if os.path.exists(storage['base_dir']):
            shutil.rmtree(storage['base_dir'])
        if project_path and os.path.exists(project_path):
            shutil.rmtree(project_path)
    except Exception as exc:
        return jsonify({"error": f"Failed to remove bot assets: {exc}"}), 500

    try:
        Conversation.query.filter_by(bot_id=bot.id).delete(synchronize_session=False)
        Intent.query.filter_by(bot_id=bot.id).delete(synchronize_session=False)
        db.session.delete(bot)
        ok, err = safe_commit()
        if not ok:
            return jsonify({"error": f"Failed to delete bot: {err}"}), 500
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 500

    _retrieval_cache.pop(cache_key, None)
    socketio.emit('bot_update', {'id': bot_id, 'deleted': True})
    return jsonify({"status": "deleted", "bot_id": bot_id}), 200

@socketio.on('chat_message')
def handle_chat_message(data):
    query = (data.get('message') or '').strip()
    if not query:
        emit('chat_response', {'answer': 'Please enter a message.'})
        return

    bot_id = data.get('bot_id')
    active_bot = None
    result = None
    rasa_used = False

    if bot_id and DB_AVAILABLE:
        active_bot = Bot.query.get(bot_id)

    if active_bot is None:
        emit('chat_response', {
            'answer': 'Please select a bot before chatting.',
            'error': True,
            'rasa': True
        })
        return

    if active_bot.status == BOT_STATUS_READY:
        sender_id = data.get('sender_id') or getattr(request, 'sid', None) or f"socket-{bot_id}"
        rasa_payload = run_rasa_turn(active_bot, query, sender_id=str(sender_id))
        if rasa_payload.get('status') == 'success':
            rasa_used = True
            responses = rasa_payload.get('responses', []) or []
            text_parts = []
            for response in responses:
                if isinstance(response, dict):
                    text = response.get('text')
                    if text:
                        text_parts.append(str(text))
            answer_text = '\n'.join(part.strip() for part in text_parts if part).strip()
            if not answer_text:
                answer_text = 'Rasa did not return a response.'
            result = {
                'answer': answer_text,
                'sources': [],
                'intent': rasa_payload.get('intent') or 'rasa',
                'confidence': rasa_payload.get('confidence'),
                'bot_id': active_bot.id,
                'responses': responses,
                'rasa': True,
            }
        else:
            app.logger.warning(
                "Rasa error for bot %s: %s", active_bot.id, rasa_payload.get('message', 'unknown error')
            )
            emit('chat_response', {
                'answer': "I ran into a problem while talking to the trained bot. Please try again in a moment.",
                'error': True,
                'bot_id': active_bot.id,
                'rasa': True,
            })
            return
    else:
        emit('chat_response', {
            'answer': f"Bot '{active_bot.name}' is not ready. Train the bot before chatting.",
            'error': True,
            'bot_id': active_bot.id,
            'rasa': True,
        })
        return

    if rasa_used:
        result.setdefault('rasa', True)

    result.setdefault('bot_id', active_bot.id)

    # Log conversation to database if available
    if DB_AVAILABLE:
        try:
            conversation = Conversation(
                bot_id=active_bot.id,
                question=query,
                answer=result.get('answer', ''),
                sources=result.get('sources', []),
                similarity_scores=result.get('similarity_scores', [])
            )
            db.session.add(conversation)
            db.session.commit()
            result['conversation_id'] = conversation.id
        except Exception as e:
            db.session.rollback()
            print(f"Error logging conversation: {e}")
    
    emit('chat_response', result)

@app.route('/api/auto-detect-intents', methods=['POST'])
def api_auto_detect_intents():
    """Auto-detect intents from indexed content"""
    from tools.detect_intents import auto_detect_intents

    data = request.get_json(silent=True) or {}
    bot_id = data.get('bot_id')
    bot = resolve_bot(bot_id)
    if bot_id and bot is None:
        return jsonify({"status": "error", "error": "Bot not found"}), 404

    storage = get_storage_paths(bot)
    ensure_storage_dirs(storage)

    result = auto_detect_intents(raw_dir=storage['raw_dir'])
    if result.get('status') == 'error':
        return jsonify(result), 500

    result['bot_id'] = bot.id if bot else None
    return jsonify(result)

@app.route('/api/intents', methods=['GET'])
def get_intents():
    """Get all stored intents"""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable", "intents": []}), 503

    bot_id = request.args.get('bot_id')
    bot = resolve_bot(bot_id)
    if bot_id and bot is None:
        return jsonify({"error": "Bot not found", "intents": []}), 404

    try:
        query = Intent.query.order_by(Intent.created_at.desc())
        if bot:
            query = query.filter(Intent.bot_id == bot.id)
        else:
            query = query.filter(Intent.bot_id.is_(None))
        intents = query.all()
        return jsonify([intent.to_dict() for intent in intents])
    except Exception as e:
        return jsonify({"error": str(e), "intents": []}), 500

@app.route('/api/intents', methods=['POST'])
def create_intent():
    """Create a new intent"""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 503
    
    from actions import ActionHandler
    
    data = request.get_json(force=True)
    bot_id = data.get('bot_id')
    bot = resolve_bot(bot_id)
    if bot_id and bot is None:
        return jsonify({"error": "Bot not found"}), 404
    try:
        defaults = ActionHandler.get_default_responses_for_intent(
            data['name'],
            data.get('description', '')
        )

        intent = Intent(
            bot_id=bot.id if bot else None,
            name=data['name'],
            description=data.get('description', ''),
            patterns=data.get('patterns', []),
            examples=data.get('examples', []),
            auto_detected=data.get('auto_detected', False),
            enabled=data.get('enabled', True),
            action_type=data.get('action_type', defaults['action_type']),
            responses=data.get('responses', defaults['responses'])
        )
        db.session.add(intent)
        db.session.commit()
        return jsonify(intent.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/intents/<int:intent_id>', methods=['PUT'])
def update_intent(intent_id):
    """Update an existing intent"""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 503
    
    data = request.get_json(force=True)
    bot_id = data.get('bot_id')
    bot = resolve_bot(bot_id)
    if bot_id and bot is None:
        return jsonify({"error": "Bot not found"}), 404
    try:
        intent = Intent.query.get(intent_id)
        if not intent:
            return jsonify({"error": "Intent not found"}), 404
        if bot and intent.bot_id != bot.id:
            return jsonify({"error": "Intent belongs to a different bot"}), 403
        if not bot and intent.bot_id is not None:
            return jsonify({"error": "Intent belongs to a bot"}), 403

        if 'name' in data:
            intent.name = data['name']
        if 'description' in data:
            intent.description = data['description']
        if 'patterns' in data:
            intent.patterns = data['patterns']
        if 'examples' in data:
            intent.examples = data['examples']
        if 'enabled' in data:
            intent.enabled = data['enabled']
        if 'action_type' in data:
            intent.action_type = data['action_type']
        if 'responses' in data:
            intent.responses = data['responses']
        
        db.session.commit()
        return jsonify(intent.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/intents/<int:intent_id>', methods=['DELETE'])
def delete_intent(intent_id):
    """Delete an intent"""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 503
    
    data = request.get_json(silent=True) or {}
    bot_id = data.get('bot_id')
    bot = resolve_bot(bot_id)
    if bot_id and bot is None:
        return jsonify({"error": "Bot not found"}), 404
    try:
        intent = Intent.query.get(intent_id)
        if not intent:
            return jsonify({"error": "Intent not found"}), 404
        if bot and intent.bot_id != bot.id:
            return jsonify({"error": "Intent belongs to a different bot"}), 403
        if not bot and intent.bot_id is not None:
            return jsonify({"error": "Intent belongs to a bot"}), 403

        db.session.delete(intent)
        db.session.commit()
        return jsonify({"status": "deleted"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/intents/<int:intent_id>/preview', methods=['POST'])
def preview_intent(intent_id):
    """Preview hybrid template with real knowledge base data"""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 503
    
    try:
        intent = Intent.query.get(intent_id)
        if not intent:
            return jsonify({"error": "Intent not found"}), 404
        
        if intent.action_type != 'hybrid':
            return jsonify({"error": "Only hybrid intents can be previewed"}), 400
        
        sample_query = intent.examples[0] if intent.examples else "Tell me about your services"
        
        retrieval_result = get_retrieval(intent.bot).get_answer(sample_query) if intent else {}
        context = retrieval_result.get('answer', 'No relevant information found')
        
        previews = []
        for template in (intent.responses or []):
            preview = template.replace('{context}', context)
            preview = preview.replace('{sources_count}', str(len(retrieval_result.get('sources', []))))
            previews.append({
                'template': template,
                'preview': preview
            })
        
        return jsonify({
            'sample_query': sample_query,
            'previews': previews
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/training-export', methods=['GET'])
def export_training_data():
    """Export intents and conversations in Rasa YAML format"""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 503

    bot_id = request.args.get('bot_id')
    bot = resolve_bot(bot_id)
    if bot_id and bot is None:
        return jsonify({"error": "Bot not found"}), 404

    try:
        intent_query = Intent.query.filter_by(enabled=True)
        convo_query = Conversation.query
        if bot:
            intent_query = intent_query.filter(Intent.bot_id == bot.id)
            convo_query = convo_query.filter(Conversation.bot_id == bot.id)
        else:
            intent_query = intent_query.filter(Intent.bot_id.is_(None))
            convo_query = convo_query.filter(Conversation.bot_id.is_(None))

        intents = intent_query.all()
        conversations = convo_query.limit(100).all()

        rasa_data = {
            'version': '3.1',
            'nlu': [],
            'responses': {}
        }
        
        for intent in intents:
            intent_data = {
                'intent': intent.name,
                'examples': '|'
            }
            
            if intent.examples:
                examples_text = '\n'.join([f"      - {ex}" for ex in intent.examples])
                intent_data['examples'] = f"|\n{examples_text}"
            
            rasa_data['nlu'].append(intent_data)
        
        import yaml
        yaml_output = yaml.dump(rasa_data, default_flow_style=False, sort_keys=False)
        
        return jsonify({
            'status': 'success',
            'yaml': yaml_output,
            'intents_count': len(intents),
            'examples_count': sum(len(i.examples or []) for i in intents)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@socketio.on('connect')
def handle_connect():
    emit('connected', {'data': 'Connected to chatbot'})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
