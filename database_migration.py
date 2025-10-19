"""Utilities for migrating legacy PostgreSQL data into SQLite."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker


def migrate_postgres_to_sqlite(db, storage, legacy_url: Optional[str]) -> None:
    """Migrate conversation and intent history from PostgreSQL to SQLite."""

    if not legacy_url or not legacy_url.startswith("postgres"):
        return

    print("Detected legacy PostgreSQL database. Preparing migration to SQLite...")

    try:
        legacy_engine = create_engine(legacy_url)
    except ModuleNotFoundError as exc:
        print(
            "Skipping migration: PostgreSQL driver is not available. "
            f"({exc})"
        )
        return
    except SQLAlchemyError as exc:
        print(f"Skipping migration: unable to create legacy engine ({exc}).")
        return

    inspector = inspect(legacy_engine)
    existing_tables = set(inspector.get_table_names())
    required_tables = {"conversations", "intents"}

    if not existing_tables.intersection(required_tables):
        print("Legacy database does not contain recognised tables; skipping migration.")
        legacy_engine.dispose()
        return

    from models import Conversation, Intent

    SqliteSession = sessionmaker(bind=db.engine)
    legacy_session_factory = sessionmaker(bind=legacy_engine)

    with SqliteSession() as sqlite_session:
        if sqlite_session.query(Conversation.id).first() or sqlite_session.query(Intent.id).first():
            print("SQLite database already contains data; skipping legacy migration.")
            legacy_engine.dispose()
            return

    try:
        with legacy_session_factory() as legacy_session:
            conversations = (
                legacy_session.query(Conversation).order_by(Conversation.id).all()
                if "conversations" in existing_tables
                else []
            )
            intents = (
                legacy_session.query(Intent).order_by(Intent.id).all()
                if "intents" in existing_tables
                else []
            )
    except SQLAlchemyError as exc:
        print(f"Failed to read legacy data; skipping migration ({exc}).")
        legacy_engine.dispose()
        return

    conversation_payloads = [
        {
            "id": conv.id,
            "question": conv.question,
            "answer": conv.answer,
            "sources": conv.sources,
            "similarity_scores": conv.similarity_scores,
            "feedback": conv.feedback,
            "timestamp": conv.timestamp,
        }
        for conv in conversations
    ]

    intent_payloads = [
        {
            "id": intent.id,
            "name": intent.name,
            "description": intent.description,
            "patterns": intent.patterns,
            "examples": intent.examples,
            "auto_detected": intent.auto_detected,
            "enabled": intent.enabled,
            "action_type": intent.action_type,
            "responses": intent.responses,
            "created_at": intent.created_at,
            "updated_at": intent.updated_at,
        }
        for intent in intents
    ]

    backup_path: Optional[Path] = None
    sqlite_path = storage.sqlite_path
    if sqlite_path.exists():
        backup_path = sqlite_path.with_suffix(".bak")
        try:
            shutil.copy2(sqlite_path, backup_path)
            print(f"SQLite backup created at {backup_path}.")
        except OSError as exc:
            print(f"Warning: Failed to create SQLite backup: {exc}")
            backup_path = None

    SqliteSession = sessionmaker(bind=db.engine)
    try:
        with SqliteSession() as sqlite_session:
            for payload in conversation_payloads:
                if sqlite_session.get(Conversation, payload["id"]):
                    continue
                sqlite_session.add(Conversation(**payload))

            for payload in intent_payloads:
                if sqlite_session.get(Intent, payload["id"]):
                    continue
                sqlite_session.add(Intent(**payload))

            sqlite_session.commit()
    except SQLAlchemyError as exc:
        if backup_path and backup_path.exists():
            try:
                shutil.copy2(backup_path, sqlite_path)
                print(
                    "Migration failed; restored SQLite database from backup "
                    f"at {backup_path}."
                )
            except OSError as restore_exc:
                print(
                    "Error: Migration failed and backup restoration was unsuccessful: "
                    f"{restore_exc}"
                )
        print(f"Legacy migration aborted due to error: {exc}")
    else:
        if conversation_payloads or intent_payloads:
            print(
                f"Successfully migrated {len(conversation_payloads)} conversations "
                f"and {len(intent_payloads)} intents into SQLite."
            )
        else:
            print("Legacy database was empty; no records migrated.")

    legacy_engine.dispose()
