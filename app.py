import eventlet
eventlet.monkey_patch()

import os
import json
import glob
import shutil
import subprocess
import threading
import yaml
import logging
import socket
import time
import requests
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, abort
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from retrieval_engine import RetrievalEngine
from tools.crawl_site import crawl_site
from tools.index_kb import index_kb
from tools.process_docs import process_uploaded_documents
from tools.profile_builder import build_company_profile
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
    clone_starter_project,
)
from openai_service import (
    get_company_name_from_url,
    generate_fallback_response,
)
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET', 'dev-secret-key')
app.logger.setLevel(logging.INFO)
if not app.logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)

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
RASA_SERVICE_BASE_PORT = int(os.environ.get('RASA_SERVICE_BASE_PORT', '51000'))
RASA_SERVICE_PORT_WINDOW = int(os.environ.get('RASA_SERVICE_PORT_WINDOW', '200'))

_rasa_services = {}
_rasa_lock = threading.Lock()


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
    ensure_column_exists('bots', 'rasa_port', 'rasa_port INTEGER')


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
    profile_path = os.path.join(base_abs, 'profile.yaml')

    return {
        'base_dir': base_abs,
        'raw_dir': raw_dir,
        'index_dir': index_dir,
        'uploads_dir': uploads_dir,
        'config_path': config_abs,
        'profile_path': profile_path,
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
    thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    thread.start()
    return thread


def find_available_port(preferred=None):
    """Find a free local port for a Rasa service."""
    start_port = preferred or RASA_SERVICE_BASE_PORT
    max_port = RASA_SERVICE_BASE_PORT + max(RASA_SERVICE_PORT_WINDOW, 50)
    for port in range(start_port, max_port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    return None


def rasa_service_healthy(port):
    """Check whether a Rasa server is responding on the given port."""
    if not port:
        return False
    try:
        resp = requests.get(f"http://127.0.0.1:{port}/status", timeout=2)
        return resp.ok
    except Exception:
        return False


def stop_rasa_service(bot_id, clear_entry=False):
    """Stop a running Rasa HTTP service for a bot."""
    with _rasa_lock:
        proc_info = _rasa_services.pop(bot_id, None)

    if not proc_info:
        return

    process = proc_info.get('process')
    if process and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=10)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    if clear_entry:
        with _rasa_lock:
            _rasa_services.pop(bot_id, None)


def start_rasa_service(bot, force_restart=False):
    """Start or reuse a persistent Rasa HTTP server for a bot."""
    if not bot or not rasa_available():
        return False, "Rasa runtime not available", None

    model_path = latest_model_path(bot.project_path)
    if not model_path:
        return False, "Bot has no trained model yet", None

    # If a service already lives on the recorded port, reuse it.
    if bot.rasa_port and rasa_service_healthy(bot.rasa_port) and not force_restart:
        with _rasa_lock:
            _rasa_services.setdefault(bot.id, {
                'process': None,
                'port': bot.rasa_port,
                'model_path': model_path,
            })
        return True, None, bot.rasa_port

    with _rasa_lock:
        existing = _rasa_services.get(bot.id)
        if existing and existing.get('process') and existing['process'].poll() is None:
            if not force_restart and existing.get('model_path') == model_path:
                return True, None, existing['port']
            stop_rasa_service(bot.id)

    preferred_port = bot.rasa_port or (RASA_SERVICE_BASE_PORT + max(bot.id - 1, 0))
    port = find_available_port(preferred_port)
    if not port:
        return False, "No available port for Rasa service", None

    env = os.environ.copy()
    env.setdefault('RASA_TELEMETRY_ENABLED', 'false')
    env.setdefault('RASA_LOG_LEVEL', 'ERROR')
    env.setdefault('LOG_LEVEL', 'ERROR')

    command = [
        RASA_PYTHON,
        '-m',
        'rasa',
        'run',
        '--enable-api',
        '--model',
        model_path,
        '--port',
        str(port),
        '--cors',
        '*',
    ]

    project_path = ensure_absolute_project_path(bot.project_path)

    try:
        process = subprocess.Popen(
            command,
            cwd=project_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as exc:
        return False, str(exc), None

    # Wait briefly for the server to come up; capture early failures
    started = False
    for _ in range(480):  # wait up to ~120s
        if process.poll() is not None:
            stderr = (process.stderr.read() or '').strip() if process.stderr else ''
            stdout = (process.stdout.read() or '').strip() if process.stdout else ''
            return False, stderr or stdout or 'Rasa service exited unexpectedly', None
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            try:
                sock.connect(('127.0.0.1', port))
                started = True
                break
            except OSError:
                time.sleep(0.25)
    if not started:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        stop_rasa_service(bot.id, clear_entry=True)
        return False, f"Rasa service did not start on port {port}", None

    bot.rasa_port = port
    ok, db_err = safe_commit()
    if not ok:
        app.logger.warning("Unable to persist rasa_port for bot %s: %s", bot.id, db_err)

    with _rasa_lock:
        _rasa_services[bot.id] = {
            'process': process,
            'port': port,
            'model_path': model_path,
        }

    return True, None, port


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
    """Send a message to the persistent Rasa HTTP service for a bot."""
    ok, err, port = start_rasa_service(bot)
    if not ok or not port:
        return {"status": "error", "message": err or "Unable to start Rasa service"}

    url = f"http://127.0.0.1:{port}/webhooks/rest/webhook"
    payload = {"sender": sender_id or "default", "message": message}

    try:
        resp = requests.post(url, json=payload, timeout=RASA_RESPONSE_TIMEOUT)
    except Exception as exc:
        app.logger.warning("Rasa HTTP request failed for bot %s: %s", bot.id, exc)
        return {"status": "error", "message": str(exc)}

    if resp.status_code >= 400:
        return {"status": "error", "message": f"Rasa service error ({resp.status_code})"}

    try:
        responses = resp.json()
    except Exception as exc:
        return {"status": "error", "message": f"Invalid response from Rasa: {exc}"}

    intent_name = None
    confidence = None
    try:
        parse_resp = requests.post(
            f"http://127.0.0.1:{port}/model/parse",
            json={"text": message},
            timeout=10,
        )
        if parse_resp.ok:
            parse_payload = parse_resp.json()
            intent_data = parse_payload.get('intent') if isinstance(parse_payload, dict) else None
            if isinstance(intent_data, dict):
                intent_name = intent_data.get('name')
                confidence = intent_data.get('confidence')
    except Exception:
        pass

    return {
        "status": "success",
        "responses": responses if isinstance(responses, list) else [],
        "intent": intent_name,
        "confidence": confidence,
        "port": port,
    }


def initialize_bot_project(bot_id, project_path):
    """Background task to create the filesystem structure for a bot."""
    with app.app_context():
        try:
            abs_path = ensure_absolute_project_path(project_path)
            starter_model = None
            try:
                starter_model = clone_starter_project(abs_path)
            except Exception as starter_exc:
                app.logger.warning("Starter bootstrap failed, falling back to fresh init: %s", starter_exc)
            if not starter_model:
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
        bot.status = BOT_STATUS_READY
        bot.last_error = ''
        bot.last_trained_at = datetime.utcnow()
        bot.updated_at = datetime.utcnow()
        start_ok, start_err, port = start_rasa_service(bot, force_restart=True)
        if not start_ok:
            bot.status = BOT_STATUS_IDLE
            bot.last_error = f"Starter model not running: {start_err}"
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

def build_rasa_training_files(bot, include_conversations=False):
    """Render Rasa training files from stored intents and recent conversations."""
    project_path = ensure_absolute_project_path(bot.project_path)
    data_dir = os.path.join(project_path, 'data')
    os.makedirs(data_dir, exist_ok=True)

    intents = Intent.query.filter(
        (Intent.bot_id == bot.id) | (Intent.bot_id.is_(None))
    ).filter(Intent.enabled.is_(True)).all()

    domain_intents = []
    domain_responses = {}
    nlu_entries = []
    stories = []

    profile = load_profile_data(bot)
    company_name = profile.get('company_name') or "our company"
    contact = profile.get('contact') or {}
    contact_lines = []
    for key in ('phone', 'email', 'website', 'address'):
        value = contact.get(key)
        if value:
            contact_lines.append(f"{key.title()}: {value}")
    contact_block = "\n".join(contact_lines)
    contact_suffix = f"\nYou can reach us:\n{contact_block}" if contact_block else ""

    # Base safety/introduction intents to satisfy starter rules.
    base_intents = {
        "greet": {
            "examples": ["hello", "hi", "hey", "good morning", "good evening"],
            "responses": [f"Hello! I can help with questions about {company_name}. What would you like to know?"],
        },
        "goodbye": {
            "examples": ["bye", "goodbye", "see you", "talk to you later"],
            "responses": ["Goodbye!"],
        },
        "out_of_scope": {
            "examples": [
                "can you write code for me",
                "order me a pizza",
                "book a flight",
                "solve this math problem",
                "how do i get rid of rats",
                "pesting",
                "do you eat carrots"
            ],
            "responses": [f"I’m only able to help with questions about {company_name}.{contact_suffix}"],
        },
        "nlu_fallback": {
            "examples": ["asldkjasd", "???", "I don't know"],
            "responses": [f"I’m only able to help with questions about {company_name}. Could you rephrase about that?"],
        },
    }
    base_example_texts = {ex.strip().lower() for payload in base_intents.values() for ex in payload.get("examples", [])}

    def add_example_intent(name, description, examples, responses):
        if not name:
            return
        domain_intents.append(name)
        utter_name = f"utter_{name}"
        trimmed_responses = [r for r in (responses or []) if isinstance(r, str) and r.strip()]
        if not trimmed_responses:
            trimmed_responses = [description or f"Details about {name}."]
        domain_responses[utter_name] = [{"text": r} for r in trimmed_responses[:3]]
        example_lines = [ex for ex in (examples or []) if isinstance(ex, str) and ex.strip()]
        if not example_lines:
            example_lines = [description or f"Ask about {name}"]
        nlu_entries.append({
            "intent": name,
            "examples": example_lines[:15],
        })
        stories.append({
            "story": f"{name}_story",
            "steps": [
                {"intent": name},
                {"action": utter_name},
            ]
        })

    for intent in intents:
        add_example_intent(intent.name, intent.description, intent.examples, intent.responses)

    # inject base intents/responses
    for base_name, payload in base_intents.items():
        add_example_intent(base_name, "", payload.get("examples"), payload.get("responses"))

    if include_conversations:
        conversations = Conversation.query.filter(Conversation.bot_id == bot.id) \
            .order_by(Conversation.timestamp.desc()).limit(50).all()
        oos_queries = []
        for conv in conversations:
            if not conv.question:
                continue
            question_text = conv.question.strip()
            if not question_text:
                continue
            if question_text.lower() in base_example_texts:
                continue
            feedback = (conv.feedback or '').lower()
            if 'not helpful' in feedback or 'off topic' in feedback or 'irrelevant' in feedback:
                oos_queries.append(question_text)
                continue
            label = f"conversation_{conv.id}"
            examples = [question_text]
            if len(examples) < 2:
                continue
            add_example_intent(
                label,
                "Learned from conversation history",
                examples,
                [conv.answer] if conv.answer else ["I'll help with that."]
            )
        if oos_queries:
            existing_oos = base_intents.get("out_of_scope", {}).get("examples", [])
            merged = list({q.strip().lower(): q.strip() for q in existing_oos + oos_queries if q.strip()}.values())
            base_intents["out_of_scope"]["examples"] = merged

    domain_content = {
        "version": "3.1",
        "intents": domain_intents,
        "responses": domain_responses,
    }
    domain_path = os.path.join(project_path, 'domain.yml')
    with open(domain_path, 'w', encoding='utf-8') as domain_file:
        yaml.safe_dump(domain_content, domain_file, sort_keys=False, allow_unicode=True)

    # Build NLU YAML manually to avoid PyYAML quoting the block scalar.
    nlu_lines = ["version: '3.1'", "nlu:"]
    for entry in nlu_entries:
        nlu_lines.append(f"- intent: {entry['intent']}")
        nlu_lines.append("  examples: |")
        for ex in entry["examples"]:
            nlu_lines.append(f"    - {ex}")
    nlu_payload = "\n".join(nlu_lines) + "\n"
    nlu_path = os.path.join(data_dir, 'nlu.yml')
    with open(nlu_path, 'w', encoding='utf-8') as nlu_file:
        nlu_file.write(nlu_payload)

    stories_payload = {
        "version": "3.1",
        "stories": stories,
    }
    stories_path = os.path.join(data_dir, 'stories.yml')
    with open(stories_path, 'w', encoding='utf-8') as stories_file:
        yaml.safe_dump(stories_payload, stories_file, sort_keys=False, allow_unicode=True, width=200)

    # Rules aligned to available intents/actions to avoid contradictions.
    rules = []
    for intent_name in domain_intents:
        rules.append({
            "rule": f"respond to {intent_name}",
            "steps": [
                {"intent": intent_name},
                {"action": f"utter_{intent_name}"}
            ]
        })
    rules_payload = {
        "version": "3.1",
        "rules": rules,
    }
    rules_path = os.path.join(data_dir, 'rules.yml')
    with open(rules_path, 'w', encoding='utf-8') as rules_file:
        yaml.safe_dump(rules_payload, rules_file, sort_keys=False, allow_unicode=True, width=200)


def train_bot_project(bot_id, project_path, include_conversations=True):
    """Background task to run rasa train for a bot."""
    with app.app_context():
        abs_path = ensure_absolute_project_path(project_path)
        bot = Bot.query.get(bot_id)
        if bot:
            try:
                build_rasa_training_files(bot, include_conversations=include_conversations)
            except Exception as render_exc:
                app.logger.warning("Unable to render training files for bot %s: %s", bot_id, render_exc)
        app.logger.info("Running rasa train for bot %s using model path %s", bot_id, abs_path)
        success, error = train_rasa_project(abs_path)
        bot = Bot.query.get(bot_id)
        if not bot:
            return

        if success:
            bot.status = BOT_STATUS_READY
            bot.last_error = ''
            bot.last_trained_at = datetime.utcnow()
            start_ok, start_err, port = start_rasa_service(bot, force_restart=True)
            if not start_ok:
                bot.last_error = f"Training succeeded but Rasa service failed: {start_err}"
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

def start_ready_bot_services():
    """Preload Rasa services for all bots marked READY."""
    if not DB_AVAILABLE or not rasa_available():
        return
    with app.app_context():
        bots = Bot.query.filter(Bot.status == BOT_STATUS_READY).all()
        for bot in bots:
            start_rasa_service(bot, force_restart=False)

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
        else:
            run_background_task(start_ready_bot_services)

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

def load_profile_data(bot=None):
    """Load the generated company profile for a bot if present."""
    storage = get_storage_paths(bot)
    profile_path = storage.get('profile_path')
    if profile_path and os.path.exists(profile_path):
        try:
            with open(profile_path, 'r', encoding='utf-8') as profile_file:
                return yaml.safe_load(profile_file) or {}
        except Exception:
            return {}
    return {}

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

    profile_path = storage.get('profile_path')
    if profile_path and os.path.exists(profile_path):
        try:
            with open(profile_path, 'r', encoding='utf-8') as profile_file:
                profile_data = yaml.safe_load(profile_file) or {}
            stats['profile'] = {
                'company_name': profile_data.get('company_name'),
                'brand_voice': profile_data.get('brand_voice'),
                'summary': profile_data.get('summary'),
                'contact': profile_data.get('contact')
            }
        except Exception as exc:
            stats['profile'] = {'error': str(exc)}

    stats_path = os.path.join(storage['index_dir'], 'stats.json')
    if os.path.exists(stats_path):
        try:
            with open(stats_path, 'r', encoding='utf-8') as stats_file:
                index_stats = json.load(stats_file) or {}
            for key in ('new_embeddings', 'reused_embeddings', 'last_indexed_at'):
                if key in index_stats:
                    stats[key] = index_stats[key]
        except Exception:
            pass
    
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
            profile_generated = False
            profile_data = {}
            detection_result = {}
            progress('info', 'Clearing previous knowledge base...')
            for path in (storage['raw_dir'], storage['index_dir'], storage['uploads_dir']):
                if os.path.exists(path):
                    shutil.rmtree(path)
                os.makedirs(path, exist_ok=True)
            if os.path.exists(storage['profile_path']):
                try:
                    os.remove(storage['profile_path'])
                except OSError:
                    pass

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

            knowledge_docs = glob.glob(os.path.join(storage['raw_dir'], "*.json"))
            if knowledge_docs:
                try:
                    profile_data = build_company_profile(
                        raw_dir=storage['raw_dir'],
                        output_path=storage['profile_path'],
                        brand_voice='professional',
                        progress_callback=progress
                    )
                    profile_generated = True
                    progress('success', f"Profile ready for {profile_data.get('company_name', 'company')}")
                except Exception as exc:
                    progress('warning', f"Profile generation skipped: {exc}")
            else:
                progress('warning', 'No knowledge documents available for profile generation.')

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
            index_result['profile_generated'] = profile_generated
            if profile_generated:
                index_result['profile_summary'] = {
                    'company_name': profile_data.get('company_name'),
                    'summary': profile_data.get('summary'),
                    'brand_voice': profile_data.get('brand_voice')
                }

            intents_detected = 0
            detection_result = {}
            rasa_summary = {}
            training_started = False
            if DB_AVAILABLE:
                progress('info', 'Detecting intents from indexed knowledge...')
                from tools.detect_intents import auto_detect_intents
                from actions import ActionHandler
                from sqlalchemy.exc import SQLAlchemyError

                with app.app_context():
                    try:
                        detection_result = auto_detect_intents(
                            raw_dir=storage['raw_dir'],
                            profile_path=storage['profile_path'],
                            brand_voice='professional'
                        )
                    except Exception as e:
                        progress('warning', f"Intent detection failed: {e}")
                        detection_result = {'status': 'error', 'error': str(e)}

                    if detection_result.get('status') == 'success':
                        suggested_intents = detection_result.get('intents', []) or []
                        if detection_result.get('profile_used'):
                            progress('info', 'Company profile applied to intent drafting.')
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
                                notes = intent_data.get('required_context')
                                if notes:
                                    description = f"{description}\nNotes: {notes}".strip()

                                patterns = intent_data.get('patterns') or []
                                if isinstance(patterns, str):
                                    patterns = [p.strip() for p in patterns.split(',') if p.strip()]

                                examples = intent_data.get('examples') or []
                                if isinstance(examples, str):
                                    examples = [line.strip() for line in examples.splitlines() if line.strip()]

                                defaults = ActionHandler.get_default_responses_for_intent(name, description)
                                canonical_response = (intent_data.get('canonical_response') or '').strip()
                                source_urls = intent_data.get('source_urls') or []
                                if isinstance(source_urls, str):
                                    source_urls = [source_urls]
                                cleaned_sources = [s for s in source_urls if s]

                                responses = []
                                action_type = 'static'
                                if canonical_response:
                                    response_text = canonical_response
                                    if cleaned_sources:
                                        joined_sources = "\n".join(f"- {src}" for src in cleaned_sources)
                                        response_text = f"{response_text}\n\nSources:\n{joined_sources}"
                                    responses = [response_text]
                                else:
                                    responses = defaults['responses']
                                    action_type = defaults['action_type']

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
                detection_result = {}

            builder_profile = profile_data or {}
            if not builder_profile and os.path.exists(storage['profile_path']):
                try:
                    with open(storage['profile_path'], 'r', encoding='utf-8') as pf:
                        builder_profile = yaml.safe_load(pf) or {}
                except Exception as exc:
                    progress('warning', f"Could not load profile for Rasa assets: {exc}")
                    builder_profile = {}

            auto_train_enabled = os.environ.get('INDEX_AUTO_TRAIN', '0').lower() in ('1', 'true', 'yes')

            if bot and DB_AVAILABLE:
                try:
                    with app.app_context():
                        intent_records = Intent.query.filter_by(bot_id=bot.id, enabled=True).all()
                        project_abs = ensure_absolute_project_path(bot.project_path)
                        from tools.rasa_builder import build_rasa_assets
                        rasa_summary = build_rasa_assets(
                            project_path=project_abs,
                            intents=intent_records,
                            profile=builder_profile,
                            similarity_threshold=similarity_threshold,
                            top_k=top_k,
                        )
                    progress('success', f"Rasa assets updated ({rasa_summary.get('knowledge_intents', 0)} knowledge intents).")
                except Exception as exc:
                    rasa_summary = {'error': str(exc)}
                    progress('warning', f"Failed to build Rasa assets: {exc}")

                if auto_train_enabled and rasa_summary and not rasa_summary.get('error'):
                    with app.app_context():
                        try:
                            bot_entry = Bot.query.get(bot.id)
                            if bot_entry:
                                app.logger.info("Starting training for bot %s", bot_entry.id)
                                bot_entry.status = BOT_STATUS_TRAINING
                                bot_entry.last_error = ''
                                bot_entry.updated_at = datetime.utcnow()
                                db.session.commit()
                                emit_bot_update(bot_entry)
                                train_bot_project(bot_entry.id, bot_entry.project_path)
                                training_started = True
                                progress('info', 'Training Rasa model completed.')
                            else:
                                progress('warning', 'Bot record missing; skipping training trigger.')
                        except Exception as exc:
                            training_started = False
                            db.session.rollback()
                            progress('warning', f"Failed to start Rasa training: {exc}")
                elif not auto_train_enabled:
                    app.logger.info("Skipping automatic training for bot %s (INDEX_AUTO_TRAIN disabled)", bot.id)

            index_result['intents_detected'] = intents_detected
            index_result['profile_used_for_intents'] = bool(detection_result.get('profile_used')) if isinstance(detection_result, dict) else False
            index_result['rasa_assets'] = rasa_summary
            index_result['rasa_training_started'] = training_started
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

    payload = request.get_json(silent=True) or {}
    include_conversations = bool(payload.get('include_conversations', True))

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
    run_background_task(train_bot_project, bot.id, bot.project_path, include_conversations)

    return jsonify({"status": "started", "bot": bot.to_dict()}), 202


@app.route('/api/bots/<int:bot_id>/restart-service', methods=['POST'])
def restart_bot_service(bot_id):
    """Restart the persistent Rasa service for a bot."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 503
    if not rasa_available():
        return jsonify({"error": "Rasa environment not available"}), 503

    bot = Bot.query.get(bot_id)
    if not bot:
        return jsonify({"error": "Bot not found"}), 404
    if bot.status != BOT_STATUS_READY:
        return jsonify({"error": "Bot is not ready"}), 409

    stop_rasa_service(bot.id, clear_entry=True)
    ok, err, port = start_rasa_service(bot, force_restart=True)
    if not ok:
        bot.last_error = err or 'Failed to restart Rasa service'
        bot.updated_at = datetime.utcnow()
        safe_commit()
        emit_bot_update(bot)
        return jsonify({"error": bot.last_error}), 500

    bot.last_error = ''
    bot.updated_at = datetime.utcnow()
    ok, db_err = safe_commit()
    if not ok:
        app.logger.warning("Failed to persist restart status for bot %s: %s", bot.id, db_err)
    emit_bot_update(bot)
    return jsonify({"status": "restarted", "port": port, "bot": bot.to_dict()}), 200


@app.route('/api/bots/<int:bot_id>', methods=['DELETE'])
def delete_bot(bot_id):
    """Completely remove a bot, its knowledge base, and related data."""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 503

    bot = Bot.query.get(bot_id)
    if not bot:
        return jsonify({"error": "Bot not found"}), 404

    stop_rasa_service(bot.id, clear_entry=True)

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

def retrieval_fallback_response(bot, query):
    """Return a retrieval-based answer, enriched with company/contact info."""
    retrieval_engine = get_retrieval(bot)
    retrieval_result = retrieval_engine.get_answer(query)
    profile = load_profile_data(bot)
    config = load_config(bot)
    company_name = profile.get('company_name') or get_company_name_from_url(config.get('url'))
    contact = profile.get('contact') or {}
    contact_lines = []
    for key in ('phone', 'email', 'website', 'address'):
        value = contact.get(key)
        if value:
            contact_lines.append(f"{key.title()}: {value}")
    contact_block = "\n".join(contact_lines)

    answer_text = retrieval_result.get('answer') or ''
    if not answer_text or "couldn't find anything relevant" in answer_text.lower():
        fallback = generate_fallback_response(company_name, f"\n\nYou can reach us:\n{contact_block}" if contact_block else "")
        answer_text = fallback
    elif contact_block and contact_block not in answer_text:
        answer_text = f"{answer_text}\n\nContact:\n{contact_block}"

    retrieval_result['answer'] = answer_text
    retrieval_result['intent'] = retrieval_result.get('intent') or 'retrieval'
    retrieval_result['rasa'] = False
    return retrieval_result

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
            responses = rasa_payload.get('responses', []) or []
            text_parts = []
            for response in responses:
                if isinstance(response, dict):
                    text = response.get('text')
                    if text:
                        text_parts.append(str(text))
            answer_text = '\n'.join(part.strip() for part in text_parts if part).strip()
            if answer_text:
                rasa_used = True
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
                result = retrieval_fallback_response(active_bot, query)
        else:
            app.logger.warning(
                "Rasa error for bot %s: %s", active_bot.id, rasa_payload.get('message', 'unknown error')
            )
            result = retrieval_fallback_response(active_bot, query)
    else:
        result = retrieval_fallback_response(active_bot, query)

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

    result = auto_detect_intents(raw_dir=storage['raw_dir'], profile_path=storage.get('profile_path'))
    if result.get('status') == 'error':
        return jsonify(result), 500

    intents_payload = result.get('intents') or []
    # Remove previous auto-detected intents for this bot to avoid duplication
    try:
        existing = Intent.query.filter_by(bot_id=bot.id if bot else None, auto_detected=True).all()
        for intent in existing:
            db.session.delete(intent)
        db.session.commit()
    except Exception:
        db.session.rollback()

    created = 0
    for intent_data in intents_payload:
        name_raw = intent_data.get('name') or ''
        name = slugify_name(name_raw)
        if bot:
            name = f"{bot.slug}-{name}"
        intent = Intent(
            bot_id=bot.id if bot else None,
            name=name,
            description=intent_data.get('description'),
            patterns=[],
            examples=intent_data.get('examples') or [],
            auto_detected=True,
            enabled=True,
            action_type='static',
            responses=[intent_data.get('canonical_response')] if intent_data.get('canonical_response') else []
        )
        try:
            db.session.add(intent)
            db.session.commit()
            created += 1
        except Exception as exc:
            db.session.rollback()
            app.logger.warning("Failed to store auto intent %s: %s", name, exc)

    result['stored'] = created
    result['bot_id'] = bot.id if bot else None

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
