"""
Hermes Chat UI -- Simplified SSE streaming engine for Hermes Agent.
Core chat functionality with SSE streaming support.
"""
import json
import logging
import os
import queue
import threading
import time
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, Callable

logger = logging.getLogger(__name__)

from hermes_cli.web_chat_api.config import (
    STREAMS, STREAMS_LOCK, CANCEL_FLAGS, AGENT_INSTANCES,
    LOCK, CHAT_SESSION_DIR, get_hermes_home,
    resolve_model_provider, get_toolsets_for_chat,
)

# Environment lock for HERMES_HOME isolation
_ENV_LOCK = threading.Lock()

# Session storage (uses Hermes Agent's SessionDB)
_session_db = None

def _get_session_db():
    """Get or create SessionDB instance."""
    global _session_db
    if _session_db is None:
        try:
            from hermes_state import SessionDB
            _session_db = SessionDB()
        except Exception as e:
            logger.warning(f"SessionDB init failed: {e}")
    return _session_db


def _get_ai_agent():
    """Get AIAgent class."""
    try:
        from run_agent import AIAgent
        return AIAgent
    except ImportError as e:
        logger.error(f"AIAgent import failed: {e}")
        return None


class ChatSession:
    """Simple session model for chat UI."""
    def __init__(self, session_id: str, workspace: str = None, model: str = None):
        self.session_id = session_id
        self.workspace = workspace or str(Path.cwd())
        self.model = model or ''
        self.messages = []
        self.title = ''
        self.created_at = time.time()
        self.last_active = time.time()


# In-memory session cache
SESSIONS: Dict[str, ChatSession] = {}
_SESSIONS_LOCK = threading.Lock()


def get_session(session_id: str) -> Optional[ChatSession]:
    """Get or create a session."""
    with _SESSIONS_LOCK:
        if session_id not in SESSIONS:
            SESSIONS[session_id] = ChatSession(session_id)
        return SESSIONS[session_id]


def update_session(session_id: str, **kwargs) -> ChatSession:
    """Update session fields."""
    session = get_session(session_id)
    for key, value in kwargs.items():
        if hasattr(session, key):
            setattr(session, key, value)
    session.last_active = time.time()
    return session


def title_from(user_text: str, assistant_text: str, max_len: int = 60) -> str:
    """Generate a simple title from first exchange."""
    if user_text:
        # Take first line or first N chars
        first_line = user_text.strip().split('\n')[0]
        if len(first_line) > max_len:
            return first_line[:max_len-3] + '...'
        return first_line
    return 'New Chat'


async def run_chat_stream(
    session_id: str,
    user_message: str,
    model: str = None,
    workspace: str = None,
    on_token: Callable = None,
    on_tool: Callable = None,
    on_complete: Callable = None,
    on_error: Callable = None,
):
    """Run a chat session with SSE streaming.

    Args:
        session_id: Unique session identifier
        user_message: User's input message
        model: Model string (e.g., "anthropic/claude-sonnet-4.6")
        workspace: Working directory for the session
        on_token: Callback for each token (delta)
        on_tool: Callback for tool progress
        on_complete: Callback when stream completes
        on_error: Callback for errors
    """
    stream_id = f"{session_id}:{int(time.time()*1000)}"
    cancel_event = threading.Event()

    with STREAMS_LOCK:
        CANCEL_FLAGS[stream_id] = cancel_event

    # Get session
    session = get_session(session_id)
    if workspace:
        session.workspace = workspace
    if model:
        session.model = model

    # Resolve model/provider
    resolved_model, resolved_provider, resolved_base_url = resolve_model_provider(model or session.model)

    # Get API key via runtime provider
    resolved_api_key = None
    api_mode = None
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider
        rt = resolve_runtime_provider(requested=resolved_provider)
        resolved_api_key = rt.get("api_key")
        if not resolved_provider:
            resolved_provider = rt.get("provider")
        if not resolved_base_url:
            resolved_base_url = rt.get("base_url")
        api_mode = rt.get("api_mode")
    except Exception as e:
        logger.warning(f"resolve_runtime_provider failed: {e}")

    # Get toolsets
    toolsets = get_toolsets_for_chat()

    # Set environment for workspace
    env_prev = {}
    with _ENV_LOCK:
        env_prev['TERMINAL_CWD'] = os.environ.get('TERMINAL_CWD')
        os.environ['TERMINAL_CWD'] = session.workspace
        env_prev['HERMES_SESSION_KEY'] = os.environ.get('HERMES_SESSION_KEY')
        os.environ['HERMES_SESSION_KEY'] = session_id

    try:
        AIAgent = _get_ai_agent()
        if AIAgent is None:
            if on_error:
                on_error("AIAgent not available")
            return

        # Build messages history
        messages = session.messages.copy()
        messages.append({"role": "user", "content": user_message})

        # Token collection for final response
        collected_tokens = []

        def _on_token_callback(delta, **kwargs):
            if cancel_event.is_set():
                return
            collected_tokens.append(delta)
            if on_token:
                on_token(delta)

        def _on_tool_callback(name, preview, args, **kwargs):
            if cancel_event.is_set():
                return
            if on_tool:
                on_tool(name, preview, args, kwargs)

        # Create agent
        agent = AIAgent(
            model=resolved_model,
            provider=resolved_provider,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            api_mode=api_mode,
            platform='webui',
            quiet_mode=True,
            enabled_toolsets=toolsets,
            session_id=session_id,
            session_db=_get_session_db(),
            stream_delta_callback=_on_token_callback,
            tool_progress_callback=_on_tool_callback,
        )

        with STREAMS_LOCK:
            AGENT_INSTANCES[stream_id] = agent

        # Check cancel before start
        if cancel_event.is_set():
            agent.interrupt("Cancelled before start")
            if on_error:
                on_error("Cancelled by user")
            return

        # Run conversation (synchronous, runs in thread)
        result_queue = queue.Queue()

        def _run_agent():
            try:
                result = agent.run_conversation(user_message)
                result_queue.put(('success', result))
            except Exception as e:
                result_queue.put(('error', str(e)))

        agent_thread = threading.Thread(target=_run_agent, daemon=True)
        agent_thread.start()

        # Wait for result or cancel
        while agent_thread.is_alive():
            if cancel_event.is_set():
                try:
                    agent.interrupt("Cancelled by user")
                except Exception:
                    pass
                agent_thread.join(timeout=5)
                if on_error:
                    on_error("Cancelled by user")
                return
            agent_thread.join(timeout=0.1)

        # Get result
        try:
            status, result = result_queue.get(timeout=1)
            if status == 'success':
                final_response = result.get('final_response', '')

                # Update session
                session.messages.append({"role": "user", "content": user_message})
                session.messages.append({"role": "assistant", "content": final_response})

                # Generate title if needed
                if not session.title and len(session.messages) >= 2:
                    user_text = session.messages[0].get('content', '')
                    session.title = title_from(user_text, final_response)

                if on_complete:
                    on_complete(final_response, session.messages)
            else:
                if on_error:
                    on_error(result)
        except queue.Empty:
            if on_error:
                on_error("Agent timeout")

    except Exception as e:
        logger.exception(f"Chat stream error: {e}")
        if on_error:
            on_error(str(e))
    finally:
        # Restore environment
        with _ENV_LOCK:
            for key, value in env_prev.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        # Cleanup
        with STREAMS_LOCK:
            CANCEL_FLAGS.pop(stream_id, None)
            AGENT_INSTANCES.pop(stream_id, None)


def cancel_stream(stream_id: str) -> bool:
    """Cancel an active stream."""
    with STREAMS_LOCK:
        if stream_id in CANCEL_FLAGS:
            CANCEL_FLAGS[stream_id].set()
            # Also interrupt agent if running
            if stream_id in AGENT_INSTANCES:
                try:
                    AGENT_INSTANCES[stream_id].interrupt("Cancelled by user")
                except Exception:
                    pass
            return True
    return False


def is_stream_active(stream_id: str) -> bool:
    """Check if a stream is still active."""
    with STREAMS_LOCK:
        return stream_id in CANCEL_FLAGS and not CANCEL_FLAGS[stream_id].is_set()


# SSE helper functions

def sse_format(event: str, data: Any) -> str:
    """Format data as SSE message."""
    data_str = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {data_str}\n\n"


def sse_done() -> str:
    """Format SSE done signal."""
    return "event: done\ndata: {}\n\n"