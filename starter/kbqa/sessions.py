"""对话历史。

按 session_id 隔离：不同会话之间不能串线（API 契约 §5）。
没有 session_id 的对话无法界定归属，不保存也不读取。
"""

from __future__ import annotations

import threading
from typing import Optional

MAX_TURNS = 6
MAX_SESSIONS = 500


class SessionStore:
    """每个会话各自保留最近几轮对话，够解追问就行。"""

    def __init__(self, max_sessions: int = MAX_SESSIONS, max_turns: int = MAX_TURNS) -> None:
        self._sessions: dict[str, list[dict]] = {}
        self._lock = threading.Lock()
        self.max_sessions = max_sessions
        self.max_turns = max_turns

    def history(self, session_id: Optional[str]) -> list[dict]:
        if not session_id:
            return []
        with self._lock:
            turns = self._sessions.get(str(session_id))
            return list(turns) if turns else []

    def append(self, session_id: Optional[str], turn: dict) -> None:
        if not session_id:
            return
        with self._lock:
            turns = self._sessions.setdefault(str(session_id), [])
            turns.append(turn)
            del turns[: max(0, len(turns) - self.max_turns)]
            overflow = len(self._sessions) - self.max_sessions
            if overflow > 0:
                for key in list(self._sessions)[:overflow]:
                    del self._sessions[key]

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
