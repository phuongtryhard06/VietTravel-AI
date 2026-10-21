"""
VietTravel AI - Session Manager (Phase 2 & Phase 3)
Provides thread-safe in-memory session caching and optional PostgreSQL persistence
for multi-turn agent conversations and shared working memory.
"""

import uuid
import threading
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Any

from src.services.ai.state import TravelSessionState

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages in-memory TravelSessionState instances across requests with thread-safety.
    """
    def __init__(self, max_cached_sessions: int = 1000):
        self._lock = threading.Lock()
        self._sessions: Dict[str, TravelSessionState] = {}
        self._conversation_lookup: Dict[int, str] = {}
        self._max_cached_sessions = max_cached_sessions

    def get_or_create_session(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[int] = None,
        conversation_id: Optional[int] = None,
        language: str = "vietnamese"
    ) -> TravelSessionState:
        with self._lock:
            # 1. Lookup by session_id
            if session_id and session_id in self._sessions:
                session = self._sessions[session_id]
                if user_id is not None and session.user_id is None:
                    session.user_id = user_id
                if conversation_id is not None and session.conversation_id is None:
                    session.conversation_id = conversation_id
                    self._conversation_lookup[conversation_id] = session_id
                return session

            # 2. Lookup by conversation_id
            if conversation_id and conversation_id in self._conversation_lookup:
                sid = self._conversation_lookup[conversation_id]
                if sid in self._sessions:
                    session = self._sessions[sid]
                    if user_id is not None:
                        session.user_id = user_id
                    return session

            # 3. Create fresh session
            actual_sid = session_id or f"session_{uuid.uuid4().hex[:12]}"
            new_session = TravelSessionState(
                session_id=actual_sid,
                user_id=user_id,
                conversation_id=conversation_id,
                language=language
            )

            # Evict oldest if capacity exceeded
            if len(self._sessions) >= self._max_cached_sessions:
                oldest_key = next(iter(self._sessions))
                del self._sessions[oldest_key]

            self._sessions[actual_sid] = new_session
            if conversation_id is not None:
                self._conversation_lookup[conversation_id] = actual_sid

            return new_session

    def save_session(self, session_state: TravelSessionState) -> None:
        with self._lock:
            session_state.updated_at = datetime.now(timezone.utc).isoformat()
            self._sessions[session_state.session_id] = session_state
            if session_state.conversation_id is not None:
                self._conversation_lookup[session_state.conversation_id] = session_state.session_id

    def persist_chat_turn(
        self,
        session_state: TravelSessionState,
        user_message: str,
        bot_response: str
    ) -> None:
        """
        Optionally persists messages into the database if conversation_id is valid.
        Operates defensively without crashing if database is not active.
        """
        if not session_state.conversation_id:
            return

        try:
            from src.models.base import db
            from src.models.message import Message
            from src.models.conversation import Conversation

            # Check if conversation exists
            conv = db.session.get(Conversation, session_state.conversation_id) if hasattr(db.session, 'get') else Conversation.query.get(session_state.conversation_id)
            if not conv:
                return

            # Save user message
            user_msg = Message(
                conversation_id=session_state.conversation_id,
                sender='user',
                message_text=user_message,
                message_type='text'
            )
            # Save bot message
            bot_msg = Message(
                conversation_id=session_state.conversation_id,
                sender='bot',
                message_text=bot_response,
                message_type='text'
            )
            db.session.add(user_msg)
            db.session.add(bot_msg)
            db.session.commit()
        except Exception as e:
            logger.debug(f"Optional chat message persistence skipped: {e}")

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._conversation_lookup.clear()


# Global shared session manager
default_session_manager = SessionManager()
