# AI Hub Chatbot - Hybrid Website-Grounded Q&A System

## Overview

This Flask-based web application provides intelligent question-answering using a hybrid approach. Factual questions are answered by retrieving information from a website (default: aihub.org.za), while conversational interactions are handled by GPT-4o-mini for natural and helpful responses. The system crawls a specified website, extracts and indexes its content, and routes user messages based on intent to deliver the most appropriate response type. The project aims to provide a robust, hallucination-free AI chatbot grounded in specific knowledge bases, offering market potential for businesses needing reliable, domain-specific AI assistance.

## User Preferences

Preferred communication style: Simple, everyday language.
- Avoid technical jargon like "crawling", "indexing", "database" - use "learning", "knowledge base", "knowledge available to me" instead
- Provide focused, specific answers rather than general information dumps
- Support document upload (markdown/PDF) alongside website crawling

## System Architecture

### Frontend Architecture

The frontend is a single-page application built with vanilla JavaScript, Bootstrap 5, and Socket.IO for real-time communication. Key components include a configuration panel, knowledge base management (combining crawling, document processing, and indexing), a real-time chat interface with optional source citations, a statistics dashboard, a "Danger Zone" for data reset, and a conversation history with feedback capabilities. This design prioritizes real-time updates and ease of maintenance.

### Backend Architecture

The backend is a Flask application utilizing Socket.IO for bidirectional communication. It orchestrates web crawling, content indexing, and hybrid Q&A.

**Core Components:**
-   **Web Crawling**: Uses Trafilatura for robust article extraction and a BFS-based crawler for content acquisition, storing raw text as JSON.
-   **Document Processing**: Extracts text from markdown and PDF files, saving them with `uploaded://` URL prefixes.
-   **Content Indexing**: Chunks documents and uses OpenAI embeddings (text-embedding-3-small) with numpy-based cosine similarity for semantic search.
-   **Retrieval Engine**: Provides fast, configurable semantic search over indexed content, formatting answers with source citations.
-   **OpenAI Service**: Employs GPT-4o-mini for intent detection (greeting, factual_question, chitchat, out_of_scope) and generating conversational responses, ensuring factual queries are handled by the retrieval engine.
-   **Hybrid Chat Handler**: Routes messages based on detected intent to the appropriate response mechanism (GPT-4o-mini for conversational, retrieval engine for factual).
-   **Rasa-Style Action System**: Implements `static`, `retrieval`, and `hybrid` action types mapped to intents, allowing for dynamic contextual responses using templates.
-   **Bot Intelligence Panel**: A UI for managing intents with enhanced visualization showing action types (static/retrieval/hybrid), response templates, and example counts. Features inline editing of action types and responses, plus live preview of hybrid templates with real knowledge base data.

Long-running operations like crawling and indexing run in background threads, with progress updates pushed via Socket.IO.

### Data Storage Solutions

A hybrid storage approach is used:
-   **File-based storage**: `kb/raw` for crawled content, `kb/index` for embeddings (numpy arrays) and metadata, and `config.json` for application settings. This leverages simple file I/O for efficient content and index management.
-   **PostgreSQL**: Stores conversation history (question, answer, sources, feedback) via Flask-SQLAlchemy, enabling conversation logging and feedback for improvement.

### Embedding and Search Architecture

The system uses OpenAI's `text-embedding-3-small` model for generating 1536-dimensional semantic embeddings. Search is performed using numpy-based cosine similarity, replacing FAISS for reduced dependencies. Content is chunked (default 900 characters with 150 character overlap) to preserve context. A similarity threshold of 0.40 is used to filter results.

## External Dependencies

### Python Libraries

-   **Web Framework**: `Flask`, `flask-socketio`, `flask-cors`, `flask-sqlalchemy`
-   **Web Crawling**: `requests`, `beautifulsoup4`, `trafilatura`
-   **Machine Learning & Search**: `numpy`, `openai`
-   **Database**: `psycopg2`

### External Services

-   **OpenAI API**: Used for conversational interactions (GPT-4o-mini), intent detection, and generating non-factual responses.
-   **PostgreSQL Database**: Stores conversation history and user feedback.

### Content Source

-   **Target website**: Configurable (default: `https://www.officems.co.za/`).
-   **Documents**: Markdown and PDF file uploads.