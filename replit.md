# IntelliBot - Hybrid AI Chatbot

## Overview

IntelliBot is a Flask-based intelligent chatbot system that combines conversational AI with knowledge base retrieval. The application crawls websites, processes documents, and provides intelligent responses using either OpenAI's GPT-4o-mini for conversational interactions or vector-based semantic search for factual questions. It features a real-time chat interface, intent management system, and optional Rasa integration for advanced dialogue management.

The system is designed to be deployed in an LXC container environment and uses a hybrid architecture that intelligently routes questions between different response strategies based on intent classification.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Core Design Patterns

**Lazy-Loaded Retrieval Engine**
- The retrieval engine initializes on-demand rather than at application startup
- Prevents blocking during health checks and initial server spin-up
- Allows the application to start quickly even when knowledge base indexing is in progress
- Maintains a `_loaded` flag to ensure embeddings are loaded only once

**Background Database Initialization**
- SQLite database setup occurs in a background thread to avoid blocking Flask initialization
- Prevents startup delays when database migrations or initial setup is required
- Uses thread-safe database connection handling

**Eventlet-Based Concurrency**
- Uses Eventlet monkey patching for non-blocking I/O operations
- Single Gunicorn worker with Eventlet worker class handles WebSocket connections efficiently
- Configured with `--worker-connections 1000` for high concurrent user support
- Enables real-time bidirectional communication for crawling progress updates

**Hybrid Response Strategy**
- Routes user messages through intent classification using OpenAI GPT-4o-mini
- Four intent categories: greeting, factual_question, chitchat, out_of_scope
- Factual questions trigger semantic search with configurable similarity threshold (default: 0.40)
- Static responses for greetings, conversational responses for chitchat
- Prevents hallucination by returning retrieval results only when similarity exceeds threshold

### Frontend Architecture

**Real-Time Communication**
- Socket.IO for bidirectional WebSocket connections
- Progress events for long-running operations (crawling, indexing, training)
- Status overlay system with automatic dismissal and manual controls
- Inline status notifications for non-blocking feedback

**Bot Management Interface**
- Multi-bot support with bot switching and creation
- Inline intent editing with live preview
- Configurable retrieval parameters (chunk size, overlap, similarity threshold, top-k)
- Visual feedback for bot initialization and training status

**Responsive Layout**
- Bootstrap 5-based responsive design
- Three-column layout: sidebar (settings), chat interface, status panels
- Collapsible sidebar for mobile viewports
- Fixed-height chat window with auto-scrolling message display

### Backend Architecture

**Intent Management System**
- Database-backed intent storage (SQLite)
- Three action types: static (predefined responses), retrieval (semantic search), hybrid (template + retrieval)
- Auto-detection feature uses OpenAI to discover intent patterns from knowledge base
- Supports multiple response variations per intent (random selection)
- Enables/disables intents without deletion

**Knowledge Base Processing**
- Multi-stage pipeline: crawl → extract → chunk → embed → index
- Trafilatura for HTML content extraction with BeautifulSoup fallback
- Configurable chunking with overlap for context preservation (default: 900 chars, 150 overlap)
- OpenAI text-embedding-3-small for vector embeddings (1536 dimensions)
- Numpy-based cosine similarity search (no external vector database dependency)

**Document Upload Support**
- PDF and Markdown file processing
- PyPDF2 for PDF text extraction
- Normalized text storage with content hash deduplication
- Integration with main knowledge base indexing pipeline

**Rasa Integration (Optional)**
- Separate Python virtual environment (.venv-rasa) for Rasa 3.6.x
- Dynamic project generation from database intents
- YAML-based configuration (nlu.yml, stories.yml, rules.yml, domain.yml)
- Training orchestration with progress callbacks
- Async message handling via standalone CLI script (rasa_respond.py)

**Bot Instance Management**
- Multi-tenancy support with per-bot configurations
- File-based storage in bots_store directory (configurable via INTELLIBOT_BOTS_DIR)
- Project cloning from starter template
- Unique slug generation for bot identifiers
- Isolated knowledge bases and conversation histories per bot

### Data Storage

**SQLite Database Schema**
- `conversations`: Question-answer pairs with sources, similarity scores, feedback, timestamps, bot association
- `intents`: Intent definitions with patterns, examples, action types, responses, auto-detection flags, bot association
- `bots`: Bot instances with names, slugs, project paths, configuration JSON, training status

**File-Based Knowledge Base**
- Raw documents: JSON files with URL, text, metadata, content hash
- Indexed data: Numpy embeddings (.npy), metadata JSON
- Uploads: Stored separately with URL prefix for serving
- Company profiles: YAML files with brand voice, mission, products (generated from knowledge base)

**Configuration Management**
- Global config.json for retrieval settings
- Per-bot config stored in database JSON column
- Environment variables for secrets (OPENAI_API_KEY, SESSION_SECRET)
- Runtime configuration updates via REST API

### API Structure

**REST Endpoints**
- `GET /`: Main chat interface
- `GET /history`: Conversation history retrieval
- `POST /save_config`: Update retrieval configuration
- `POST /upload_docs`: Document upload handler
- `POST /feedback`: User feedback submission
- `GET /intents`: Intent management interface
- `GET /api/intents`: Intent listing
- `POST /api/intents`: Intent creation
- `PUT /api/intents/<id>`: Intent updates
- `DELETE /api/intents/<id>`: Intent deletion
- `POST /auto_detect_intents`: AI-powered intent discovery
- Bot management endpoints: create, list, switch, status, delete

**Socket.IO Events**
- Client → Server: `send_message`, `crawl_request`, `index_request`, `init_bot`, `train_bot`
- Server → Client: `response`, `progress`, `error`, `status`
- Real-time progress updates for long operations
- Error handling with user-friendly messages

### Security Considerations

**Environment-Based Secrets**
- OpenAI API key loaded from environment variable
- Session secret for Flask session management
- No hardcoded credentials in codebase

**File Upload Validation**
- Secure filename sanitization using werkzeug
- File extension validation (PDF, MD only)
- Content hash verification to prevent duplicates

**Container Isolation**
- LXC container with bind-mounted workspace
- systemd service management
- Restricted filesystem access

## External Dependencies

### Third-Party APIs

**OpenAI API**
- Models: GPT-4o-mini (chat completions), text-embedding-3-small (embeddings)
- Used for: Intent classification, conversational responses, intent auto-detection, embeddings generation
- Rate limiting handled by client library
- API key required via OPENAI_API_KEY environment variable

### Python Libraries

**Core Framework**
- Flask: Web framework
- Flask-SocketIO: WebSocket support
- Eventlet: Async I/O (required for SocketIO worker)
- Gunicorn: Production WSGI server

**Database**
- Flask-SQLAlchemy: ORM layer
- SQLite: Embedded database (no external server)

**AI/ML Processing**
- OpenAI Python SDK: API client
- Numpy: Vector operations and cosine similarity
- Rasa (optional): Advanced dialogue management in separate venv

**Content Processing**
- Trafilatura: HTML content extraction
- BeautifulSoup4: HTML parsing fallback
- PyPDF2: PDF text extraction
- lxml_html_clean: HTML sanitization

**Utilities**
- PyYAML: Configuration file parsing
- Requests: HTTP client for crawling

### External Services

**Web Crawling**
- Respects robots.txt via robotparser
- Custom User-Agent: "IntelliBot/1.0"
- Sitemap.xml parsing for efficient crawling
- Rate limiting and duplicate URL detection

### Deployment Environment

**LXC Container**
- Ubuntu Server 24.04 LTS base
- Container name: IntelliBot
- IPv4: 10.130.0.134 (internal)
- Bind-mounted workspace with UID/GID shifting

**System Services**
- systemd service: intellibot.service
- Gunicorn binding: 0.0.0.0:80 (container internal)
- Environment file: /etc/intellibot.env
- Log management: journalctl integration

**File Storage**
- Project root: /workspace/IntelliBot
- Knowledge base: kb/ directory (raw, index, uploads subdirectories)
- Bot storage: bots_store/ (configurable)
- SQLite database: intellibot.db (project root)

**Python Environments**
- Main app: .venv (Flask, OpenAI, numpy, etc.)
- Rasa (optional): .venv-rasa (Rasa 3.6.x with dedicated dependencies)