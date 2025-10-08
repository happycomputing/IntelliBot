# AI Hub Chatbot - Hybrid Website-Grounded Q&A System

## Overview

This is a Flask-based web application that provides intelligent question-answering with a **hybrid architecture**: factual questions are answered using website-grounded retrieval (no hallucination), while conversational interactions use GPT-4o-mini for natural, helpful responses. The system crawls a specified website (default: aihub.org.za), extracts and indexes the content, and intelligently routes user messages based on intent detection to provide the most appropriate response type.

## User Preferences

Preferred communication style: Simple, everyday language.
- Avoid technical jargon like "crawling", "indexing", "database" - use "learning", "knowledge base", "knowledge available to me" instead
- Provide focused, specific answers rather than general information dumps
- Support document upload (markdown/PDF) alongside website crawling

## System Architecture

### Frontend Architecture

**Problem**: Need an interactive web interface for configuration, crawling, indexing, and chatting.

**Solution**: Single-page application using vanilla JavaScript with Socket.IO for real-time communication, Bootstrap 5 for UI components, and server-sent events for progress updates.

**Key Design Decisions**:
- Real-time updates via WebSockets (Socket.IO) for crawling/indexing progress and chat responses
- RESTful API endpoints for configuration management
- Bootstrap framework for responsive, professional UI without custom CSS complexity
- Separated concerns: configuration panel, action buttons, chat interface, conversation history, and status display
- Conversation history with feedback capability for iterative improvement
- Danger Zone with Clear Bot functionality to reset all data

**UI Components**:
1. **Configuration Panel**: URL, max pages, chunk size, overlap, similarity threshold, top-k settings
2. **Knowledge Base Management**: 
   - Document upload (markdown/PDF)
   - Single "Index Knowledge Base" button (combines clearing, crawling, document processing, and indexing)
   - Real-time progress indicators with status updates
3. **Chat Interface**: 
   - Real-time Q&A with optional source citations
   - "Show references" toggle to control source visibility
   - Confidence scores
4. **Statistics Dashboard**: Displays raw documents, chunks, indexed status, and configured URL
5. **Danger Zone**: Clear Bot button to wipe all data and reset to defaults
6. **Conversation History**: View past conversations and add feedback for training/improvement
7. **About Section**: System capabilities and technology stack

**Pros**: Simple to maintain, no build process required, real-time feedback, conversation tracking
**Cons**: Less structured than modern frameworks, limited state management

### Backend Architecture

**Problem**: Need to orchestrate web crawling, content indexing, and hybrid question-answering with real-time updates.

**Solution**: Flask web framework with Socket.IO for bidirectional communication, modular tool architecture for crawling and indexing, and OpenAI integration for conversational AI.

**Core Components**:

1. **Web Crawling** (`tools/crawl_site.py`)
   - Uses Trafilatura for robust article extraction from HTML
   - BFS-based crawler with URL normalization and deduplication
   - Respects same-origin policy (only crawls specified domain)
   - Stores raw extracted text as JSON documents with URL metadata
   - Configurable max pages and timeout settings

2. **Document Processing** (`tools/process_docs.py`)
   - Extracts text from uploaded markdown files (UTF-8 decoding)
   - Extracts text from PDF files using PyPDF2
   - Saves processed documents with uploaded:// URL prefix
   - Supports batch processing of multiple files

3. **Content Indexing** (`tools/index_kb.py`)
   - Chunks documents with configurable size and overlap for context preservation
   - Uses sentence-transformers (all-MiniLM-L6-v2 model) for semantic embeddings
   - FAISS IndexFlatIP for cosine similarity search (normalized embeddings)
   - Stores embeddings, metadata, and FAISS index for fast retrieval

4. **Retrieval Engine** (`retrieval_engine.py`)
   - Lazy-loading pattern for FAISS index and embedding model
   - Configurable similarity threshold and top-k results
   - Formats answers with source citations
   - Deduplicates results from the same URL

5. **OpenAI Service** (`openai_service.py`)
   - Intent detection using GPT-4o-mini with JSON structured output
   - Classifies messages as: greeting, factual_question, chitchat, out_of_scope
   - Generates friendly greetings with dynamic KB statistics
   - Creates helpful fallback responses for non-factual queries
   - All responses redirect users back to asking about indexed content

6. **Hybrid Chat Handler** (`app.py`)
   - Detects intent of incoming messages
   - Routes to appropriate handler:
     - Greetings → GPT-4o-mini (contextual welcome)
     - Factual questions → FAISS retrieval (grounded answers)
     - Chitchat/Out-of-scope → GPT-4o-mini (helpful redirection)
   - Logs all conversations to PostgreSQL for training and improvement

7. **Combined Indexing Workflow** (`/api/index_all`)
   - Clears previous knowledge base (raw docs and index)
   - Crawls website if URL provided
   - Processes uploaded documents (markdown/PDF)
   - Indexes everything together with progress updates
   - Single endpoint replaces separate crawl/index operations

**Threading Model**: Background threads for long-running crawl and index operations to prevent blocking the main Flask thread, with Socket.IO for progress updates.

**Pros**: Hybrid approach balances grounded answers with natural conversation, local FAISS for factual queries (no hallucination), modular design, configurable
**Cons**: Single-server architecture, no distributed crawling, requires OpenAI API key for conversational features

### Data Storage Solutions

**Problem**: Need to store raw crawled content, vector embeddings, configuration, and conversation history.

**Solution**: Hybrid approach with file-based storage for content/embeddings and PostgreSQL for conversation logging.

**Storage Structure**:
```
kb/
  raw/          # Crawled content as JSON (hash-based filenames)
  index/        # FAISS index, embeddings.npy, meta.json
config.json     # Application configuration
PostgreSQL      # Conversation history with feedback
```

**File Storage Rationale**: 
- No database overhead for simple read-heavy workload (crawled content)
- FAISS native binary format for efficient index loading
- JSON for human-readable metadata and configuration
- SHA-1 hashed filenames prevent URL encoding issues

**Database Storage (PostgreSQL)**:
- Stores conversation history (question, answer, sources, similarity_scores, timestamp, feedback)
- Enables conversation logging for training and improvement
- User feedback on bot responses
- Flask-SQLAlchemy ORM with connection pooling
- Graceful degradation: app continues to work for chat even if database is unavailable

**Pros**: Zero-config files for core features, ACID compliance for conversations, version-controllable content
**Cons**: Requires PostgreSQL for conversation logging (optional feature)

### Embedding and Search Architecture

**Problem**: Need fast semantic search over potentially thousands of text chunks.

**Solution**: Sentence transformers with FAISS vector database.

**Technical Choices**:

1. **Embedding Model**: sentence-transformers/all-MiniLM-L6-v2
   - 384-dimensional embeddings
   - Good balance of speed and quality for semantic search
   - Runs locally without external API calls
   - Optimized for semantic similarity tasks

2. **Vector Index**: FAISS IndexFlatIP (Inner Product)
   - Uses normalized embeddings for cosine similarity
   - Exact search (no approximation)
   - Simple, deterministic results

**Chunking Strategy**:
- Default 900 characters per chunk with 150 character overlap
- Overlap preserves context across chunk boundaries
- Configurable to tune for different content types

**Similarity Threshold**: 0.52 default to filter low-relevance results

**Alternatives Considered**: 
- OpenAI embeddings (rejected: requires API key, costs, external dependency)
- Approximate search (ANN) with FAISS IVF indices (rejected: unnecessary complexity for expected scale)

**Pros**: Fast, local, deterministic, no API costs
**Cons**: Fixed model (no fine-tuning), exact search scales linearly

## External Dependencies

### Python Libraries

**Web Framework**:
- `Flask`: Core web application framework
- `flask-socketio`: WebSocket support for real-time updates
- `flask-cors`: CORS handling for potential frontend separation
- `flask-sqlalchemy`: PostgreSQL ORM for conversation logging

**Web Crawling**:
- `requests`: HTTP client for fetching web pages
- `beautifulsoup4`: HTML parsing fallback
- `trafilatura`: Primary content extraction (article-focused)

**Machine Learning & Search**:
- `sentence-transformers`: Semantic embedding generation (all-MiniLM-L6-v2)
- `faiss-cpu`: Vector similarity search and indexing
- `numpy`: Numerical operations and array handling
- `openai`: OpenAI API client for GPT-4o-mini conversational AI

**Database**:
- `psycopg2`: PostgreSQL database adapter

**Configuration**:
- Environment variables: `SESSION_SECRET`, `DATABASE_URL`, `OPENAI_API_KEY`

### External Services

**OpenAI API**: 
- Used for conversational interactions only (greetings, chitchat, out-of-scope responses)
- Model: GPT-4o-mini
- Intent detection with JSON structured output
- Factual Q&A still uses local FAISS (no external API for factual answers)

**PostgreSQL Database**:
- Stores conversation history with feedback for training
- Graceful degradation: chat works even if database is unavailable

### Content Source

- Target website: Configurable (default: https://aihub.org.za/)
- Respects robots.txt conventions via user-agent header
- Single-domain crawling (no cross-site following)