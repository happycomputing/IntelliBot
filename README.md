# IntelliBot - Hybrid AI Chatbot

A Flask-based intelligent chatbot that combines OpenAI GPT-4o-mini for conversational interactions with vector-based retrieval for factual question answering. The system crawls websites, processes documents, and provides accurate, hallucination-free responses grounded in your knowledge base.

## Features

### Core Capabilities
- **Hybrid Response System**: Routes questions intelligently between GPT-4o-mini (conversational) and retrieval engine (factual)
- **Website Crawling**: Automatically learns from any website using Trafilatura for content extraction
- **Document Upload**: Supports Markdown and PDF file uploads for additional knowledge
- **Semantic Search**: Uses OpenAI embeddings (text-embedding-3-small) with numpy-based cosine similarity
- **Real-time Interface**: Socket.IO-powered chat with instant responses and progress updates
- **Intent Management**: Rasa-style intent-to-action system with static, retrieval, and hybrid response types
- **Conversation History**: PostgreSQL-backed logging with user feedback capabilities

### Advanced Features
- **Bot Intelligence Panel**: Manage intents with inline editing, action types, and live preview of hybrid templates
- **Auto-Detect Intents**: Automatically discovers conversation patterns from your knowledge base
- **Configurable Search**: Adjust chunk size, overlap, similarity threshold, and top-k results
- **Source Citations**: All factual answers include source URLs for verification
- **Background Processing**: Non-blocking crawling and indexing with real-time progress updates

## Architecture

### Technology Stack
- **Backend**: Flask + Flask-SocketIO + Eventlet
- **AI/ML**: OpenAI (GPT-4o-mini, text-embedding-3-small)
- **Search**: Numpy-based cosine similarity
- **Database**: PostgreSQL for conversations, File-based for knowledge base
- **Frontend**: Vanilla JavaScript, Bootstrap 5, Socket.IO

### Key Design Decisions
- **Lazy-loaded retrieval engine** for fast startup and health checks
- **Background database initialization** to avoid blocking requests
- **Eventlet worker** for efficient Socket.IO handling
- **Similarity threshold of 0.40** for reliable retrieval results

## Quick Start

### LXC Development Container

Use the bundled helper to spin up an Ubuntu Server 24.04 LTS container configured for IntelliBot development:

```bash
./scripts/setup_intellibot_lxc.sh
```

Key options can be overridden at runtime, for example:

```bash
LXC_IMAGE=ubuntu:24.04 CONTAINER_PORT=5000 HOST_PROJECT_PATH=$PWD ./scripts/setup_intellibot_lxc.sh
```

The script defaults to the official Ubuntu server images (`ubuntu:24.04`) and automatically falls back to the Ubuntu Images remote (`images:ubuntu/24.04/cloud`) if the primary alias is unavailable. See `scripts/setup_intellibot_lxc.sh` for additional knobs such as mounting a host workspace, forwarding ports, and optional PostgreSQL installation.

### Prerequisites
- Python 3.11+
- PostgreSQL database
- OpenAI API key

### Installation

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd intellibot
   ```

2. **Set up environment variables**
   ```bash
   export OPENAI_API_KEY="your-openai-api-key"
   export DATABASE_URL="postgresql://user:password@host:port/dbname"
   export SESSION_SECRET="your-secret-key"
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   # Or if using uv:
   uv pip install -r requirements.txt
   ```

4. **Run the application**
   ```bash
   python app.py
   ```
   
   The app will be available at http://localhost:5000

### First-Time Setup

1. **Configure a website URL** in the Configuration panel (default: https://www.officems.co.za/)
2. **Click "Index Knowledge Base"** to crawl and process the website
3. **Wait for indexing to complete** (progress shown in real-time)
4. **Start chatting!** The bot will answer questions based on the indexed knowledge

## Usage Guide

### Adding Knowledge

#### From a Website
1. Enter the website URL in Configuration panel
2. Set max pages to crawl (recommended: 5-50)
3. Click "Index Knowledge Base"
4. Wait for completion (typically 30-60 seconds)

#### From Documents
1. Click "Choose Files" in Knowledge Base section
2. Select Markdown (.md) or PDF files
3. Click "Index Knowledge Base" to process uploaded documents

### Managing Intents

1. **Navigate to Bot Intelligence** tab
2. **View existing intents** with their action types and responses
3. **Edit intents inline**: Click edit icon to modify action type or response templates
4. **Preview hybrid responses**: Click preview to see how templates render with real data
5. **Auto-detect new intents**: Click "Auto-Detect Intents" to discover patterns from your knowledge base

### Conversation Management

- **View history**: Check Conversations tab for all past interactions
- **Provide feedback**: Use thumbs up/down on responses
- **Export conversations**: Download conversation history for analysis

## Deployment

### Replit Deployment (Recommended)

The app is pre-configured for Replit VM deployment:

1. **Ensure secrets are set**:
   - `OPENAI_API_KEY`
   - `DATABASE_URL` (automatically set by Replit)
   - `SESSION_SECRET`

2. **Click Deploy** in Replit
3. **Select "Reserved VM"** deployment type
4. The deployment config is already optimized:
   - Gunicorn with Eventlet worker
   - Worker connections: 1000
   - Timeout: 60 seconds
   - Fast health checks enabled

### Manual Deployment

For production deployment on other platforms:

```bash
gunicorn --worker-class eventlet -w 1 --worker-connections 1000 app:app --bind 0.0.0.0:5000 --timeout 60
```

**Important**: The app requires `eventlet.monkey_patch()` at the very beginning of `app.py` before any imports. Do not modify this initialization order.

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key for GPT and embeddings | Required |
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `SESSION_SECRET` | Flask session secret key | `dev-secret-key` |

### Application Settings (config.json)

| Setting | Description | Default |
|---------|-------------|---------|
| `url` | Website to crawl | `https://www.officems.co.za/` |
| `max_pages` | Maximum pages to crawl | 5 |
| `chunk_size` | Text chunk size for indexing | 900 |
| `chunk_overlap` | Overlap between chunks | 150 |
| `similarity_threshold` | Minimum similarity for retrieval | 0.40 |
| `top_k` | Number of top results to return | 4 |

## Project Structure

```
.
├── app.py                 # Main Flask application with eventlet
├── models.py             # SQLAlchemy database models
├── retrieval_engine.py   # Vector search and retrieval
├── openai_service.py     # OpenAI API integration
├── actions.py            # Intent-to-action routing
├── tools/
│   ├── crawl_site.py    # Website crawler
│   ├── index_kb.py      # Knowledge base indexer
│   ├── process_docs.py  # Document processor
│   └── detect_intents.py # Intent auto-detection
├── static/              # CSS and JavaScript
├── templates/           # HTML templates
├── kb/                  # Knowledge base storage
│   ├── raw/            # Crawled content (JSON)
│   └── index/          # Embeddings and metadata
└── config.json         # Application configuration
```

## API Endpoints

### Core Endpoints
- `GET /` - Main application interface
- `GET /health` - Health check endpoint
- `GET /api/config` - Get configuration
- `POST /api/config` - Update configuration
- `GET /api/stats` - Get knowledge base statistics

### Intent Management
- `GET /api/intents` - List all intents
- `POST /api/intents` - Create new intent
- `PUT /api/intents/<id>` - Update intent
- `DELETE /api/intents/<id>` - Delete intent
- `GET /api/intents/<id>/preview` - Preview hybrid template

### Data Management
- `GET /api/conversations` - Get conversation history
- `POST /api/reset` - Reset all data (danger zone)

### Socket.IO Events
- `crawl_site` - Start website crawling
- `index_kb` - Start knowledge base indexing
- `upload_documents` - Process uploaded documents
- `chat_message` - Send chat message
- `auto_detect_intents` - Auto-detect intents from KB

## Troubleshooting

### Common Issues

**Q: Chatbot says "No relevant information found"**
- Check if knowledge base is indexed (Statistics panel shows indexed status)
- Try lowering similarity threshold in Configuration
- Ensure the question relates to indexed content

**Q: Indexing fails or hangs**
- Verify OPENAI_API_KEY is set correctly
- Check internet connection for API calls
- Look for errors in browser console (F12)

**Q: Deployment health checks fail**
- Ensure eventlet.monkey_patch() is at top of app.py
- Verify no blocking operations in startup code
- Check DATABASE_URL is accessible

**Q: Slow response times**
- Reduce chunk_size for faster search
- Lower top_k results count
- Check OpenAI API rate limits

## Development

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run in debug mode
python app.py

# The app will reload automatically on code changes
```

### Running Tests

```bash
# Test retrieval engine
python -c "from retrieval_engine import RetrievalEngine; print('OK')"

# Test OpenAI connection
python -c "from openai_service import detect_intent; print('OK')"
```

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is licensed under the MIT License.

## Acknowledgments

- OpenAI for GPT-4o-mini and embeddings API
- Trafilatura for robust web content extraction
- Flask-SocketIO for real-time communication
- Replit for hosting and deployment platform

## Support

For issues, questions, or suggestions:
- Open an issue on GitHub
- Check the troubleshooting section
- Review the documentation in `replit.md`

---

Built with ❤️ for reliable, knowledge-grounded AI assistance
