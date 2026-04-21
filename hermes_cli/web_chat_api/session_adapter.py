"""
Session persistence adapter for web_server.py

This module provides a unified interface for session management that uses
the persistent storage system (models.py) instead of the in-memory system
(chat_stream.py).

This ensures sessions survive server restarts.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_cli.web_chat_api import models
from hermes_cli.web_chat_api.config import CHAT_STATE_DIR

logger = logging.getLogger(__name__)


def _to_api_format(session: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert internal session format to API response format.

    Args:
        session: Internal session dict from models.py

    Returns:
        Session dict in API response format
    """
    return {
        "session_id": session["session_id"],
        "title": session.get("title", ""),
        "workspace": session.get("workspace", ""),
        "model": session.get("model", ""),
        "messages": session.get("messages", []),
        "created_at": session.get("created_at", 0),
        "last_active": session.get("updated_at", 0),  # Map updated_at to last_active
    }


def _to_list_format(session: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert internal session format to list item format.

    Args:
        session: Internal session dict from models.py

    Returns:
        Session dict in list format (without full messages)
    """
    return {
        "session_id": session["session_id"],
        "title": session.get("title", ""),
        "workspace": session.get("workspace", ""),
        "model": session.get("model", ""),
        "message_count": len(session.get("messages", [])),
        "created_at": session.get("created_at", 0),
        "last_active": session.get("updated_at", 0),
    }


def get_session_endpoint_handler(
    session_id: str,
    state_dir: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """
    Get session details for API endpoint.

    Uses persistent storage (models.py) instead of in-memory storage.

    Args:
        session_id: Session ID to retrieve
        state_dir: State directory (defaults to CHAT_STATE_DIR)

    Returns:
        Session dict or None if not found
    """
    state_dir = state_dir or CHAT_STATE_DIR
    session = models.get_session(session_id, state_dir=state_dir)

    if session is None:
        return None

    return _to_api_format(session)


def list_sessions_handler(
    state_dir: Optional[Path] = None,
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """
    List all persistent sessions.

    Args:
        state_dir: State directory (defaults to CHAT_STATE_DIR)
        limit: Maximum number of sessions to return
        offset: Number of sessions to skip

    Returns:
        List of session dicts
    """
    state_dir = state_dir or CHAT_STATE_DIR
    sessions_dir = state_dir / "sessions"

    if not sessions_dir.exists():
        return []

    sessions = []
    session_files = sorted(
        sessions_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True  # Most recent first
    )

    # Apply offset and limit
    for session_file in session_files[offset:offset + limit]:
        try:
            session_id = session_file.stem
            session = models.get_session(session_id, state_dir=state_dir)
            if session:
                sessions.append(_to_list_format(session))
        except Exception as e:
            logger.warning(f"Failed to load session {session_file}: {e}")
            continue

    return sessions


def create_session_handler(
    title: str = "New Chat",
    workspace: str = "",
    model: str = "",
    state_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Create a new persistent session.

    Args:
        title: Session title
        workspace: Workspace directory
        model: Model string
        state_dir: State directory (defaults to CHAT_STATE_DIR)

    Returns:
        Created session dict
    """
    state_dir = state_dir or CHAT_STATE_DIR
    session = models.create_session(
        title=title,
        workspace=workspace,
        model=model,
        state_dir=state_dir
    )

    return _to_api_format(session)


def update_session_handler(
    session_id: str,
    state_dir: Optional[Path] = None,
    **kwargs
) -> Optional[Dict[str, Any]]:
    """
    Update a persistent session.

    Args:
        session_id: Session ID to update
        state_dir: State directory (defaults to CHAT_STATE_DIR)
        **kwargs: Fields to update

    Returns:
        Updated session dict or None if not found
    """
    state_dir = state_dir or CHAT_STATE_DIR
    session = models.update_session(session_id, state_dir=state_dir, **kwargs)

    if session is None:
        return None

    return _to_api_format(session)


def delete_session_handler(
    session_id: str,
    state_dir: Optional[Path] = None
) -> bool:
    """
    Delete a persistent session.

    Args:
        session_id: Session ID to delete
        state_dir: State directory (defaults to CHAT_STATE_DIR)

    Returns:
        True if deleted, False if not found
    """
    state_dir = state_dir or CHAT_STATE_DIR
    return models.delete_session(session_id, state_dir=state_dir)


def save_session_after_chat(
    session_id: str,
    messages: List[Dict[str, Any]],
    state_dir: Optional[Path] = None,
    title: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Save session after chat completion.

    This function should be called by chat_stream.py after a chat completes
    to persist the messages to disk.

    Args:
        session_id: Session ID
        messages: List of messages to save
        state_dir: State directory (defaults to CHAT_STATE_DIR)
        title: Optional title to update

    Returns:
        Updated session dict or None if session doesn't exist
    """
    state_dir = state_dir or CHAT_STATE_DIR

    # Get existing session or create new one
    session = models.get_session(session_id, state_dir=state_dir)

    if session is None:
        # Create new session if it doesn't exist
        session = models.create_session(
            title=title or "New Chat",
            workspace="",
            model="",
            state_dir=state_dir
        )
        # Update session_id to match the provided one
        # (This handles cases where session was created in memory first)
        old_id = session["session_id"]
        if old_id != session_id:
            # Delete old file and create with correct ID
            models.delete_session(old_id, state_dir=state_dir)
            session["session_id"] = session_id
            session_file = models.get_session_file(session_id, state_dir)
            import json
            session_file.write_text(json.dumps(session, indent=2))

    # Update with new messages and title
    update_kwargs = {"messages": messages}
    if title:
        update_kwargs["title"] = title

    return models.update_session(session_id, state_dir=state_dir, **update_kwargs)
