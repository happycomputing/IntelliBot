import os
import json
import glob
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from retrieval_engine import RetrievalEngine
from tools.crawl_site import crawl_site
from tools.index_kb import index_kb
import threading

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET', 'dev-secret-key')
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

retrieval = RetrievalEngine(similarity_threshold=0.52, top_k=4)

CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "url": "https://aihub.org.za/",
    "max_pages": 500,
    "chunk_size": 900,
    "chunk_overlap": 150,
    "similarity_threshold": 0.52,
    "top_k": 4
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return DEFAULT_CONFIG.copy()

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/config', methods=['GET'])
def get_config():
    config = load_config()
    return jsonify(config)

@app.route('/api/config', methods=['POST'])
def update_config():
    config = request.json
    save_config(config)
    retrieval.similarity_threshold = config.get('similarity_threshold', 0.52)
    retrieval.top_k = config.get('top_k', 4)
    return jsonify({"status": "success", "config": config})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    stats = retrieval.get_stats()
    
    raw_count = len(glob.glob("kb/raw/*.json"))
    stats['raw_documents'] = raw_count
    
    config = load_config()
    stats['configured_url'] = config.get('url', '')
    
    return jsonify(stats)

@app.route('/api/crawl', methods=['POST'])
def start_crawl():
    data = request.json
    url = data.get('url', 'https://aihub.org.za/')
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
            retrieval._loaded = False
            socketio.emit('index_status', {'status': 'completed', 'result': result})
        except Exception as e:
            socketio.emit('index_status', {'status': 'error', 'message': str(e)})
    
    thread = threading.Thread(target=index_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({"status": "started"})

@socketio.on('chat_message')
def handle_chat_message(data):
    query = data.get('message', '')
    result = retrieval.get_answer(query)
    emit('chat_response', result)

@socketio.on('connect')
def handle_connect():
    emit('connected', {'data': 'Connected to chatbot'})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
