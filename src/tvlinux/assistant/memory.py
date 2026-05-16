"""SQLite-backed memory + conversation store for the desktop assistant."""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "ChatMessage",
    "MemoryFact",
    "MemoryStore",
    "extract_explicit_memory",
]

_WORD_RE = re.compile(r"[a-z0-9']+")
_SPACE_RE = re.compile(r"\s+")

_EXPLICIT_MEMORY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*/remember\s+(.+?)\s*$", re.IGNORECASE),
    re.compile(r"^\s*remember that\s+(.+?)\s*$", re.IGNORECASE),
)

_INFERRED_MEMORY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bmy name is\s+([a-z][a-z0-9 _-]{1,60})", re.IGNORECASE),
        "User's name is {value}.",
    ),
    (
        re.compile(r"\bcall me\s+([a-z][a-z0-9 _-]{1,60})", re.IGNORECASE),
        "User likes to be called {value}.",
    ),
    (
        re.compile(r"\bi prefer\s+([^.!?\n]{3,120})", re.IGNORECASE),
        "User prefers {value}.",
    ),
    (
        re.compile(r"\bi like\s+([^.!?\n]{3,120})", re.IGNORECASE),
        "User likes {value}.",
    ),
    (
        re.compile(r"\bi dislike\s+([^.!?\n]{3,120})", re.IGNORECASE),
        "User dislikes {value}.",
    ),
    (
        re.compile(r"\bi don't like\s+([^.!?\n]{3,120})", re.IGNORECASE),
        "User dislikes {value}.",
    ),
    (
        re.compile(r"\bi work as\s+([^.!?\n]{3,120})", re.IGNORECASE),
        "User works as {value}.",
    ),
    (
        re.compile(r"\bmy timezone is\s+([^.!?\n]{2,80})", re.IGNORECASE),
        "User's timezone is {value}.",
    ),
    (
        re.compile(r"\bi live in\s+([^.!?\n]{2,80})", re.IGNORECASE),
        "User lives in {value}.",
    ),
)

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "can",
    "do",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "we",
    "with",
    "you",
    "your",
}


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class MemoryFact:
    id: int
    text: str
    source: str
    score: float
    created_at: str


def extract_explicit_memory(text: str) -> str | None:
    """Return memory content from explicit remember commands."""
    for pattern in _EXPLICIT_MEMORY_PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        value = _clean_value(match.group(1))
        if value:
            return value
    return None


def _tokenize(text: str) -> set[str]:
    tokens = {tok for tok in _WORD_RE.findall(text.lower()) if len(tok) > 1}
    return {tok for tok in tokens if tok not in _STOPWORDS}


def _clean_value(value: str) -> str:
    value = value.strip(" \t\r\n.?!,:;\"'")
    value = _SPACE_RE.sub(" ", value)
    return value.strip()


class MemoryStore:
    """Small durable store for chat history and long-term memory facts."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    source TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0.5,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversation_created ON conversation(created_at)"
            )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(UTC).isoformat()

    def remember(self, text: str, *, source: str = "inferred", score: float = 0.65) -> int:
        """Persist a memory fact and return its row id."""
        cleaned = _clean_value(text)
        if not cleaned:
            raise ValueError("Memory text cannot be empty.")
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                """
                SELECT id
                FROM memories
                WHERE lower(text) = lower(?)
                ORDER BY id DESC
                LIMIT 1
                """,
                (cleaned,),
            ).fetchone()
            if existing is not None:
                existing_id = int(existing["id"])
                conn.execute(
                    "UPDATE memories SET score = MAX(score, ?) WHERE id = ?",
                    (float(score), existing_id),
                )
                return existing_id
            cur = conn.execute(
                "INSERT INTO memories(text, source, score, created_at) VALUES (?, ?, ?, ?)",
                (cleaned, source, float(score), self._utc_now()),
            )
            return int(cur.lastrowid)  # type: ignore[arg-type]

    def forget(self, memory_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    def clear_memories(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM memories")

    def list_memories(self, *, limit: int = 200) -> list[MemoryFact]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, text, source, score, created_at
                FROM memories
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [
            MemoryFact(
                id=int(row["id"]),
                text=str(row["text"]),
                source=str(row["source"]),
                score=float(row["score"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def search(self, query: str, *, limit: int = 8) -> list[MemoryFact]:
        """Simple semantic-ish recall via token overlap + confidence weight."""
        query_tokens = _tokenize(query)
        all_memories = self.list_memories(limit=1000)
        if not all_memories:
            return []
        if not query_tokens:
            return all_memories[: max(1, int(limit))]

        ranked: list[tuple[float, MemoryFact]] = []
        for memory in all_memories:
            memory_tokens = _tokenize(memory.text)
            if not memory_tokens:
                continue
            overlap = len(memory_tokens & query_tokens)
            if overlap == 0:
                continue
            coverage = overlap / max(1, len(query_tokens))
            density = overlap / max(1, len(memory_tokens))
            rank = (coverage * 0.75) + (density * 0.15) + (memory.score * 0.10)
            ranked.append((rank, memory))

        ranked.sort(key=lambda item: (item[0], item[1].id), reverse=True)
        return [memory for _, memory in ranked[: max(1, int(limit))]]

    def append_message(self, role: str, content: str) -> None:
        cleaned = content.strip()
        if not cleaned:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO conversation(role, content, created_at) VALUES (?, ?, ?)",
                (role, cleaned, self._utc_now()),
            )
            # Keep the local transcript bounded.
            conn.execute(
                """
                DELETE FROM conversation
                WHERE id NOT IN (
                    SELECT id
                    FROM conversation
                    ORDER BY id DESC
                    LIMIT 500
                )
                """
            )

    def recent_messages(self, *, limit: int = 20) -> list[ChatMessage]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content
                FROM conversation
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        rows = list(reversed(rows))
        return [ChatMessage(role=str(row["role"]), content=str(row["content"])) for row in rows]

    def clear_conversation(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM conversation")

    def ingest_user_text(self, text: str) -> list[str]:
        """Store explicit and inferred facts from a user utterance."""
        added: list[str] = []
        explicit = extract_explicit_memory(text)
        if explicit is not None:
            self.remember(explicit, source="explicit", score=1.0)
            added.append(explicit)

        normalized = text.strip()
        if not normalized:
            return added

        for pattern, template in _INFERRED_MEMORY_PATTERNS:
            for match in pattern.finditer(normalized):
                value = _clean_value(match.group(1))
                if not value:
                    continue
                fact = template.format(value=value)
                if fact in added:
                    continue
                self.remember(fact, source="inferred", score=0.65)
                added.append(fact)
        return added
