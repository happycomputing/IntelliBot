# AI Hub Chatbot - Website Grounded Q&A System

## Overview

This is a Flask-based web application that provides question-answering capabilities grounded in website content. The system crawls a specified website (default: aihub.org.za), extracts and indexes the content, and allows users to ask questions that are answered using relevant information retrieved from the indexed content. The application uses semantic search with FAISS vector indexing and sentence transformers for embeddings to find the most relevant content chunks for answering user queries.

## User Preferences

Preferred communication style: Simple, everyday language.

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
2. **Crawl & Index**: Start crawl and build index buttons with real-time progress indicators
3. **Chat Interface**: Real-time Q&A with source citations and confidence scores
4. **Statistics Dashboard**: Displays raw documents, chunks, indexed status, and configured URL
5. **Danger Zone**: Clear Bot button to wipe all data and reset to defaults
6. **Conversation History**: View past conversations and add feedback for training/improvement
7. **About Section**: System capabilities and technology stack

**Pros**: Simple to maintain, no build process required, real-time feedback, conversation tracking
**Cons**: Less structured than modern frameworks, limited state management

### Backend Architecture

**Problem**: Need to orchestrate web crawling, content indexing, and question-answering with real-time updates.

**Solution**: Flask web framework with Socket.IO for bidirectional communication, modular tool architecture for crawling and indexing.

**Core Components**:

1. **Web Crawling** (`tools/crawl_site.py`)
   - Uses Trafilatura for robust article extraction from HTML
   - BFS-based crawler with URL normalization and deduplication
   - Respects same-origin policy (only crawls specified domain)
   - Stores raw extracted text as JSON documents with URL metadata
   - Configurable max pages and timeout settings

2. **Content Indexing** (`tools/index_kb.py`)
   - Chunks documents with configurable size and overlap for context preservation
   - Uses sentence-transformers (all-MiniLM-L6-v2 model) for semantic embeddings
   - FAISS IndexFlatIP for cosine similarity search (normalized embeddings)
   - Stores embeddings, metadata, and FAISS index for fast retrieval

3. **Retrieval Engine** (`retrieval_engine.py`)
   - Lazy-loading pattern for FAISS index and embedding model
   - Configurable similarity threshold and top-k results
   - Formats answers with source citations
   - Deduplicates results from the same URL

**Threading Model**: Background threads for long-running crawl and index operations to prevent blocking the main Flask thread, with Socket.IO for progress updates.

**Pros**: Modular design, local-first (no external API dependencies for embeddings), configurable
**Cons**: Single-server architecture, no distributed crawling, in-memory index limits

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

**Web Crawling**:
- `requests`: HTTP client for fetching web pages
- `beautifulsoup4`: HTML parsing fallback
- `trafilatura`: Primary content extraction (article-focused)

**Machine Learning & Search**:
- `sentence-transformers`: Semantic embedding generation
- `faiss-cpu`: Vector similarity search and indexing
- `numpy`: Numerical operations and array handling
- `scikit-learn`: Potential future ML utilities (currently unused)

**Configuration**:
- Environment variables for Flask secret key (`SESSION_SECRET`)

### External Services

**None**: The application is designed to run entirely locally without external API dependencies. All ML models run on the server, and no cloud services are required for operation.

### Content Source

- Target website: Configurable (default: https://aihub.org.za/)
- Respects robots.txt conventions via user-agent header
- Single-domain crawling (no cross-site following)