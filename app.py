import eventlet
eventlet.monkey_patch()

import os
import json
import glob
import shutil
from pathlib import Path
from typing import Dict, Optional
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from retrieval_engine import RetrievalEngine
from tools.crawl_site import crawl_site
from tools.index_kb import index_kb
from tools.process_docs import process_uploaded_documents
from agent_storage import AgentStorage
from models import db, Conversation, Intent
import threading

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET', 'dev-secret-key')

storage = AgentStorage()

app.config['SQLALCHEMY_DATABASE_URI'] = storage.sqlite_url
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,
    "connect_args": {"check_same_thread": False},
}
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

storage.migrate_legacy_app_config(Path("config.json"))
db.init_app(app)

# Database availability flag
DB_AVAILABLE = False

def init_database():
    """Initialize database in background to avoid blocking startup"""
    global DB_AVAILABLE
    with app.app_context():
        try:
            db.create_all()
            DB_AVAILABLE = True
            print(f"Database initialized successfully at {storage.sqlite_path}")
        except Exception as e:
            print(f"❌ Database initialization error: {e}")
            print("⚠️  Conversation logging is DISABLED. Chat will work but conversations won't be saved.")

# Start database initialization in background
threading.Thread(target=init_database, daemon=True).start()


def activate_agent(agent_id: str, display_name: Optional[str] = None) -> Dict[str, str]:
    """Switch the active agent and rebind dependent services."""

    global _retrieval, DB_AVAILABLE

    metadata = storage.switch_agent(agent_id, display_name)
    app.config['SQLALCHEMY_DATABASE_URI'] = storage.sqlite_url

    with app.app_context():
        db.session.remove()
        try:
            db.engine.dispose()
        except Exception:
            pass

    DB_AVAILABLE = False
    init_database()
    _retrieval = None
    return metadata

DEFAULT_CONFIG = {}

# Lazy-loaded retrieval engine
_retrieval = None

def get_retrieval():
    """Get or initialize retrieval engine (lazy loading)"""
    global _retrieval
    if _retrieval is None:
        startup_config = DEFAULT_CONFIG.copy()
        try:
            startup_config.update(load_config())
        except Exception as e:
            print(f"Warning: Could not load agent app settings: {e}")
        
        _retrieval = RetrievalEngine(
            similarity_threshold=startup_config.get('similarity_threshold', 0.40), 
            top_k=startup_config.get('top_k', 4)
        )
    return _retrieval

def load_config():
    config = storage.load_app_settings()
    return config if config else DEFAULT_CONFIG.copy()


def save_config(config):
    storage.save_app_settings(config)

@app.route('/health')
def health():
    """Fast health check endpoint for deployment"""
    return jsonify({"status": "ok"}), 200

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/agents', methods=['GET'])
def list_agents():
    return jsonify({
        "agents": storage.list_agents(),
        "active_agent": storage.current_agent(),
    })


@app.route('/api/agents', methods=['POST'])
def create_agent():
    payload = request.json or {}

    identifier = (
        payload.get('id')
        or payload.get('identifier')
        or payload.get('agent_id')
        or payload.get('name')
        or ""
    )
    display_name = payload.get('display_name') or payload.get('name')

    if not str(identifier).strip():
        return jsonify({"error": "Agent identifier is required"}), 400

    try:
        agent = storage.create_agent(str(identifier), display_name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileExistsError as exc:
        return jsonify({"error": str(exc)}), 409

    return jsonify({"status": "created", "agent": agent}), 201


@app.route('/api/agents/select', methods=['POST'])
def select_agent():
    payload = request.json or {}
    identifier = (
        payload.get('id')
        or payload.get('identifier')
        or payload.get('agent_id')
        or ""
    )
    display_name = payload.get('display_name') or payload.get('name')

    if not str(identifier).strip():
        return jsonify({"error": "Agent identifier is required"}), 400

    try:
        agent = activate_agent(str(identifier), display_name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"status": "success", "agent": agent})


@app.route('/api/config', methods=['GET'])
def get_config():
    config = load_config()
    return jsonify(config)

@app.route('/api/config', methods=['POST'])
def update_config():
    config = request.json
    save_config(config)
    # Update retrieval engine settings immediately
    get_retrieval().similarity_threshold = config.get('similarity_threshold', 0.40)
    get_retrieval().top_k = config.get('top_k', 4)
    return jsonify({"status": "success", "config": config})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    stats = get_retrieval().get_stats()
    
    # Get raw document count and sources
    raw_files = glob.glob("kb/raw/*.json")
    stats['raw_documents'] = len(raw_files)
    
    # Extract document sources (URLs)
    sources = []
    for raw_file in raw_files:
        try:
            with open(raw_file, 'r') as f:
                doc = json.load(f)
                url = doc.get('url', 'Unknown')
                sources.append(url)
        except Exception:
            pass
    
    stats['document_sources'] = sorted(list(set(sources)))  # Unique, sorted URLs
    
    config = load_config()
    stats['configured_url'] = config.get('url', '')
    
    return jsonify(stats)

@app.route('/api/crawl', methods=['POST'])
def start_crawl():
    data = request.json
    url = data.get('url', 'https://www.officems.co.za/')
    max_pages = data.get('max_pages', 500)
    
    def crawl_progress(status_type, message):
        socketio.emit('crawl_progress', {'type': status_type, 'message': message})
    
    def crawl_task():
        try:
            socketio.emit('crawl_status', {'status': 'started', 'message': f'Starting crawl of {url}...'})
            result = crawl_site(url, max_pages, progress_callback=crawl_progress)
            socketio.emit('crawl_status', {'status': 'completed', 'result': result})
        except Exception as e:
            socketio.emit('crawl_status', {'status': 'error', 'message': str(e)})
    
    thread = threading.Thread(target=crawl_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({"status": "started"})

@app.route('/api/index', methods=['POST'])
def start_indexing():
    data = request.json
    chunk_size = data.get('chunk_size', 900)
    chunk_overlap = data.get('chunk_overlap', 150)
    
    def index_progress(status_type, message):
        socketio.emit('index_progress', {'type': status_type, 'message': message})
    
    def index_task():
        try:
            socketio.emit('index_status', {'status': 'started', 'message': 'Building vector index...'})
            result = index_kb(chunk_size, chunk_overlap, progress_callback=index_progress)
            get_retrieval()._loaded = False
            socketio.emit('index_status', {'status': 'completed', 'result': result})
        except Exception as e:
            socketio.emit('index_status', {'status': 'error', 'message': str(e)})
    
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
    url = request.form.get('url', '').strip()
    max_pages = int(request.form.get('max_pages', 500))
    chunk_size = int(request.form.get('chunk_size', 900))
    chunk_overlap = int(request.form.get('chunk_overlap', 150))
    uploaded_files_raw = request.files.getlist('documents')
    
    uploaded_files = []
    for file in uploaded_files_raw:
        uploaded_files.append({
            'filename': file.filename,
            'content': file.read()
        })
    
    def progress(status_type, message):
        socketio.emit('index_progress', {'type': status_type, 'message': message})
    
    def combined_task():
        try:
            # Step 1: Clear existing index
            progress('info', 'Clearing previous knowledge base...')
            if os.path.exists('kb/raw'):
                shutil.rmtree('kb/raw')
                os.makedirs('kb/raw')
            if os.path.exists('kb/index'):
                shutil.rmtree('kb/index')
                os.makedirs('kb/index')
            
            # Step 2: Crawl website if URL provided
            if url:
                progress('info', f'Learning from website: {url}...')
                socketio.emit('index_status', {'status': 'started', 'message': f'Learning from {url}...'})
                
                def crawl_progress(status_type, msg):
                    progress(status_type, msg)
                
                crawl_result = crawl_site(url, max_pages, progress_callback=crawl_progress)
                progress('success', f"✓ Learned from {crawl_result['pages']} pages")
            
            # Step 3: Process uploaded documents
            if uploaded_files:
                progress('info', f'Processing {len(uploaded_files)} uploaded documents...')
                processed = process_uploaded_documents(uploaded_files)
                progress('success', f"Processed {len(processed)} documents")
            
            # Step 4: Index everything
            progress('info', 'Building knowledge index...')
            
            def index_progress_cb(status_type, msg):
                progress(status_type, msg)
            
            index_result = index_kb(chunk_size, chunk_overlap, progress_callback=index_progress_cb)
            get_retrieval()._loaded = False
            
            progress('success', f"✓ Knowledge base ready! {index_result['total_chunks']} chunks indexed")
            socketio.emit('index_status', {'status': 'completed', 'result': index_result})
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"Indexing error: {error_details}")
            progress('error', str(e))
            socketio.emit('index_status', {'status': 'error', 'message': str(e)})
    
    thread = threading.Thread(target=combined_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({"status": "started"})

@app.route('/api/conversations', methods=['GET'])
def get_conversations():
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable", "conversations": []}), 503
    
    try:
        conversations = Conversation.query.order_by(Conversation.timestamp.desc()).limit(50).all()
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
    
    try:
        db.session.query(Conversation).delete()
        db.session.commit()
        return jsonify({"status": "success", "message": "All conversation history cleared"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/clear-bot', methods=['POST'])
def clear_bot():
    try:
        # Clear database data if database is available
        if DB_AVAILABLE:
            try:
                # Clear conversations
                db.session.query(Conversation).delete()
                # Clear intents
                db.session.query(Intent).delete()
                db.session.commit()
            except Exception as db_error:
                db.session.rollback()
                print(f"Warning: Failed to clear database data: {db_error}")
        
        # Clear crawled documents
        if os.path.exists('kb/raw'):
            shutil.rmtree('kb/raw')
            os.makedirs('kb/raw')
        
        # Clear index
        if os.path.exists('kb/index'):
            shutil.rmtree('kb/index')
            os.makedirs('kb/index')
        
        # Reset config to default and reload
        save_config(DEFAULT_CONFIG.copy())
        reset_config = load_config()
        
        # Reset retrieval engine with reloaded config
        get_retrieval()._loaded = False
        get_retrieval().similarity_threshold = reset_config.get('similarity_threshold', 0.40)
        get_retrieval().top_k = reset_config.get('top_k', 4)
        
        return jsonify({
            "status": "success", 
            "message": "All bot data cleared and reset to defaults"
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@socketio.on('chat_message')
def handle_chat_message(data):
    from openai_service import detect_intent, generate_greeting, generate_fallback_response
    from tools.detect_intents import match_intent_pattern
    from actions import ActionHandler
    
    query = data.get('message', '')
    
    # Step 1: Check custom intents first (pattern matching)
    matched_custom_intent = None
    if DB_AVAILABLE:
        try:
            custom_intents = Intent.query.filter_by(enabled=True).all()
            for custom_intent in custom_intents:
                if match_intent_pattern(query, custom_intent.patterns or []):
                    matched_custom_intent = custom_intent
                    print(f"Matched custom intent: {custom_intent.name}")
                    break
        except Exception as e:
            print(f"Error checking custom intents: {e}")
    
    # Step 2: If custom intent matched, execute its action
    if matched_custom_intent:
        result = ActionHandler.execute_action(matched_custom_intent, query, query, get_retrieval())
        result['custom_intent'] = True
    else:
        # Step 3: Fall back to OpenAI intent detection
        intent, confidence = detect_intent(query)
        print(f"Detected intent: {intent} (confidence: {confidence})")
        
        # Route based on intent
        if intent == "greeting":
            # Generate friendly greeting with bot capabilities
            stats_data = get_retrieval().get_stats()
            raw_count = len(glob.glob("kb/raw/*.json"))
            config = load_config()
            stats = {
                'raw_docs': raw_count,
                'url': config.get('url', 'configured website'),
                'chunks': stats_data.get('total_chunks', 0)
            }
            answer = generate_greeting(stats)
            result = {
                'answer': answer,
                'sources': [],
                'confidence': 1.0,
                'intent': 'greeting'
            }
        elif intent in ["chitchat", "out_of_scope"]:
            # Friendly decline with contact details - NO GPT to prevent hallucination
            from openai_service import get_company_name_from_url, get_contact_details
            
            config = load_config()
            company_name = get_company_name_from_url(config.get('url', ''))
            contact_details = get_contact_details(get_retrieval())
            answer = generate_fallback_response(company_name, contact_details)
            
            result = {
                'answer': answer,
                'sources': [],
                'confidence': 1.0,
                'intent': intent
            }
        else:
            # Use retrieval for factual questions
            result = get_retrieval().get_answer(query)
            result['intent'] = 'factual_question'
    
    # Log conversation to database if available
    if DB_AVAILABLE:
        try:
            conversation = Conversation(
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
    
    result = auto_detect_intents()
    if result.get('status') == 'error':
        return jsonify(result), 500
    
    return jsonify(result)

@app.route('/api/intents', methods=['GET'])
def get_intents():
    """Get all stored intents"""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable", "intents": []}), 503
    
    try:
        intents = Intent.query.order_by(Intent.created_at.desc()).all()
        return jsonify([intent.to_dict() for intent in intents])
    except Exception as e:
        return jsonify({"error": str(e), "intents": []}), 500

@app.route('/api/intents', methods=['POST'])
def create_intent():
    """Create a new intent"""
    if not DB_AVAILABLE:
        return jsonify({"error": "Database unavailable"}), 503
    
    from actions import ActionHandler
    
    data = request.json
    try:
        defaults = ActionHandler.get_default_responses_for_intent(
            data['name'], 
            data.get('description', '')
        )
        
        intent = Intent(
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
    
    data = request.json
    try:
        intent = Intent.query.get(intent_id)
        if not intent:
            return jsonify({"error": "Intent not found"}), 404
        
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
    
    try:
        intent = Intent.query.get(intent_id)
        if not intent:
            return jsonify({"error": "Intent not found"}), 404
        
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
        
        retrieval_result = get_retrieval().get_answer(sample_query)
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
    
    try:
        intents = Intent.query.filter_by(enabled=True).all()
        conversations = Conversation.query.limit(100).all()
        
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
