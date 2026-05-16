"""Quest knowledge oracle — bridges tibia-reborn data into Svetlana's context.

Reads from three content sources in priority order:
  1. content/quest-guides/*.json  — 50 rich structured quest guides
  2. content/notebooklm-boss-insights/*.md — deep boss mechanics
  3. content/quests/*.md          — 360 fallback guides (basic)

Configure paths via env vars; defaults point to a sibling tibia-reborn/ directory.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

__all__ = ["QuestOracle", "QuestMatch"]

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

# Assume tibia-reborn/ lives next to tibiavision-linux/ by default.
_SIBLING_REBORN = Path(__file__).resolve().parents[4] / "tibia-reborn"


def _resolve_path(env_key: str, *subpath: str) -> Path:
    raw = os.environ.get(env_key, "").strip()
    if raw:
        return Path(raw).expanduser()
    return _SIBLING_REBORN.joinpath(*subpath)


# ---------------------------------------------------------------------------
# Token matching (same approach as memory.py)
# ---------------------------------------------------------------------------

_NOISE_RE = re.compile(r"[^a-z0-9\s]")
_QUEST_STOPWORDS = {
    "a",
    "an",
    "the",
    "of",
    "in",
    "on",
    "at",
    "to",
    "for",
    "and",
    "or",
    "quest",
    "how",
    "do",
    "i",
    "we",
    "me",
    "my",
    "our",
    "now",
    "want",
    "need",
    "help",
    "with",
    "doing",
    "start",
    "begin",
}


def _tokenize(text: str) -> frozenset[str]:
    clean = _NOISE_RE.sub(" ", text.lower())
    return frozenset(t for t in clean.split() if t and t not in _QUEST_STOPWORDS)


def _prefix_overlap(query_tokens: frozenset[str], title_tokens: frozenset[str]) -> float:
    """Partial credit for prefix matches — handles plurals and apostrophe splits (pirates/pirate)."""
    count = 0.0
    for qt in query_tokens:
        if len(qt) < 4 or qt in query_tokens & title_tokens:
            continue  # skip short tokens and exact matches already counted
        for tt in title_tokens:
            if len(tt) < 4 and tt.startswith(qt) or qt.startswith(tt):
                count += 0.8
                break
    return count


def _match_score(query_tokens: frozenset[str], title_tokens: frozenset[str]) -> float:
    if not query_tokens or not title_tokens:
        return 0.0
    exact = len(query_tokens & title_tokens)
    fuzzy = _prefix_overlap(query_tokens, title_tokens)
    overlap = exact + fuzzy
    if overlap == 0:
        return 0.0
    precision = overlap / len(query_tokens)
    recall = overlap / len(title_tokens)
    return precision * 0.65 + recall * 0.35


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class QuestMatch:
    name: str
    slug: str
    location: str
    level_required: int
    level_recommended: int
    summary: str
    quick_facts: dict
    prerequisites: list[str]
    fast_path: list[str]
    key_items: list[str]
    key_npcs: list[str]
    warnings: list[str]
    boss_insights: str
    source: str  # 'guide_json' | 'fallback_md' | 'db_only'


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------


class QuestOracle:
    """Load and search Tibia quest data from tibia-reborn content files."""

    _MIN_SCORE = 0.35

    def __init__(
        self,
        db_path: Path | None = None,
        guides_dir: Path | None = None,
        boss_insights_dir: Path | None = None,
        fallbacks_dir: Path | None = None,
    ) -> None:
        self._db_path = db_path or _resolve_path("TIBIAWIKI_DB", "tibiawiki.db")
        self._guides_dir = guides_dir or _resolve_path(
            "TIBIA_QUEST_GUIDES", "content", "quest-guides"
        )
        self._boss_dir = boss_insights_dir or _resolve_path(
            "TIBIA_BOSS_INSIGHTS", "content", "notebooklm-boss-insights"
        )
        self._fallbacks_dir = fallbacks_dir or _resolve_path(
            "TIBIA_QUEST_FALLBACKS", "content", "quests"
        )

        # In-memory index: list of (token_set, slug, title) for the 50 priority guides
        self._guide_index: list[tuple[frozenset[str], str, str]] = []
        self._load_guide_index()

    def _load_guide_index(self) -> None:
        if not self._guides_dir.exists():
            return
        for path in self._guides_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                title = data.get("title") or path.stem.replace("-", " ")
                # Always use path.stem as slug — it's guaranteed to match the filename on disk.
                self._guide_index.append((_tokenize(title), path.stem, title))
            except Exception:
                pass

    # -- Public API -----------------------------------------------------------

    def search(self, query: str) -> QuestMatch | None:
        """Return the best-matching quest for a natural-language query, or None."""
        query_tokens = _tokenize(query)
        if not query_tokens:
            return None

        best_score = 0.0
        best_slug = ""
        best_title = ""

        for title_tokens, slug, title in self._guide_index:
            score = _match_score(query_tokens, title_tokens)
            if score > best_score:
                best_score = score
                best_slug = slug
                best_title = title

        if best_score >= self._MIN_SCORE and best_slug:
            return self._load_guide(best_slug, best_title)

        # Fall back to SQLite fuzzy search for quests not in the priority 50
        return self._search_db(query_tokens)

    def format_context(self, match: QuestMatch) -> str:
        """Render a quest match as a compact context block for Svetlana's system prompt."""
        lines: list[str] = []

        lines.append(f"QUEST: {match.name}")

        # Quick facts line
        facts: list[str] = []
        qf = match.quick_facts
        lvl = qf.get("levelRequired") or match.level_required
        lvl_rec = qf.get("levelRecommended") or match.level_recommended
        if lvl:
            lvl_str = f"Level {lvl}"
            if lvl_rec and lvl_rec != lvl:
                lvl_str += f" (recommended {lvl_rec})"
            facts.append(lvl_str)
        if qf.get("teamSize"):
            facts.append(f"Team: {qf['teamSize']}")
        loc = qf.get("location") or match.location
        if loc:
            facts.append(f"Location: {loc}")
        if qf.get("premium"):
            facts.append("Premium only")
        if qf.get("repeatable") and qf["repeatable"] != "No":
            facts.append(f"Repeatable: {qf['repeatable']}")
        if qf.get("estimatedTime"):
            facts.append(f"~{qf['estimatedTime']}")
        if facts:
            lines.append(" | ".join(facts))

        if qf.get("reward") or qf.get("rewards"):
            reward = qf.get("reward") or ", ".join(qf.get("rewards", []))
            lines.append(f"Reward: {reward}")

        if match.summary:
            lines.append("")
            lines.append(match.summary)

        if match.prerequisites:
            lines.append("")
            lines.append("Prerequisites: " + "; ".join(match.prerequisites[:3]))

        if match.fast_path:
            lines.append("")
            lines.append("Steps:")
            for i, step in enumerate(match.fast_path[:10], 1):
                lines.append(f"  {i}. {step}")

        if match.warnings:
            lines.append("")
            for w in match.warnings[:4]:
                lines.append(f"  ⚠️  {w}")

        if match.key_items:
            lines.append("")
            lines.append("Key items: " + ", ".join(match.key_items[:6]))

        if match.key_npcs:
            lines.append("Key NPCs/enemies: " + ", ".join(match.key_npcs[:6]))

        if match.boss_insights:
            lines.append("")
            lines.append("BOSS MECHANICS:")
            lines.append(match.boss_insights)

        return "\n".join(lines)

    # -- Loaders --------------------------------------------------------------

    def _load_guide(self, slug: str, title: str) -> QuestMatch | None:
        guide_path = self._guides_dir / f"{slug}.json"
        if not guide_path.exists():
            return None
        try:
            data = json.loads(guide_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        qf = data.get("quickFacts", {})

        fast_path: list[str] = data.get("fastPath", [])
        if not fast_path:
            # Build fast_path from stages
            for stage in data.get("stages", []):
                for step in stage.get("steps", []):
                    text = step.get("text", "").strip()
                    if text:
                        fast_path.append(text)

        warnings: list[str] = []
        for stage in data.get("stages", []):
            for w in stage.get("warnings", []):
                text = w.get("text", "").strip()
                if text:
                    warnings.append(text)

        key_items = [e["name"] for e in data.get("keyItems", []) if e.get("name")]
        key_npcs = [e["name"] for e in data.get("keyNpcs", []) if e.get("name")]

        prereqs: list[str] = []
        for p in data.get("structuredPrerequisites", data.get("prerequisites", [])):
            if isinstance(p, str):
                prereqs.append(p)
            elif isinstance(p, dict):
                prereqs.append(p.get("text", ""))

        boss_insights = self._load_boss_insights(slug)

        return QuestMatch(
            name=data.get("title", title),
            slug=slug,
            location=qf.get("location", ""),
            level_required=int(qf.get("levelRequired") or 0),
            level_recommended=int(qf.get("levelRecommended") or qf.get("levelRequired") or 0),
            summary=data.get("summary", ""),
            quick_facts=qf,
            prerequisites=[p for p in prereqs if p],
            fast_path=fast_path,
            key_items=key_items,
            key_npcs=key_npcs,
            warnings=warnings,
            boss_insights=boss_insights,
            source="guide_json",
        )

    def _load_boss_insights(self, slug: str) -> str:
        """Load notebooklm-style boss mechanics markdown if it exists for this slug."""
        if not self._boss_dir.exists():
            return ""
        # Try exact slug match first, then stem-only (e.g. "Bakragore" from "Bakragore-Quest")
        candidates = [
            self._boss_dir / f"{slug}.md",
            self._boss_dir / f"{slug.removesuffix('-Quest')}.md",
        ]
        for path in candidates:
            if path.exists():
                try:
                    text = path.read_text(encoding="utf-8").strip()
                    # Strip YAML frontmatter
                    if text.startswith("---"):
                        end = text.find("---", 3)
                        if end != -1:
                            text = text[end + 3 :].strip()
                    return text
                except Exception:
                    pass
        return ""

    def _load_fallback(self, name: str) -> QuestMatch | None:
        """Try to load a fallback markdown guide by quest name."""
        slug = name.replace(" ", "-")
        path = self._fallbacks_dir / f"{slug}.md"
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8").strip()
            # Strip YAML frontmatter
            if text.startswith("---"):
                end = text.find("---", 3)
                if end != -1:
                    text = text[end + 3 :].strip()
        except Exception:
            return None

        return QuestMatch(
            name=name,
            slug=slug,
            location="",
            level_required=0,
            level_recommended=0,
            summary=text[:800],  # First 800 chars from the fallback
            quick_facts={},
            prerequisites=[],
            fast_path=[],
            key_items=[],
            key_npcs=[],
            warnings=[],
            boss_insights=self._load_boss_insights(slug),
            source="fallback_md",
        )

    def _search_db(self, query_tokens: frozenset[str]) -> QuestMatch | None:
        """Fuzzy search the SQLite quest table for quests not in the priority 50."""
        if not self._db_path.exists():
            return None
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT name, location, level_required, level_recommended, legend FROM quest"
                " WHERE status = 'active' ORDER BY name"
            ).fetchall()
            conn.close()
        except Exception:
            return None

        best_score = 0.0
        best_row = None
        for row in rows:
            row_name = row["name"] or ""
            score = _match_score(query_tokens, _tokenize(row_name))
            if score > best_score:
                best_score = score
                best_row = row

        if best_score < self._MIN_SCORE or best_row is None:
            return None

        name = best_row["name"] or ""
        # Try fallback markdown for this quest
        fallback = self._load_fallback(name)
        if fallback:
            fallback.location = best_row["location"] or ""
            fallback.level_required = int(best_row["level_required"] or 0)
            fallback.level_recommended = int(best_row["level_recommended"] or 0)
            if best_row["legend"]:
                fallback.summary = best_row["legend"] + "\n\n" + fallback.summary
            return fallback

        return QuestMatch(
            name=name,
            slug=name.replace(" ", "-"),
            location=best_row["location"] or "",
            level_required=int(best_row["level_required"] or 0),
            level_recommended=int(best_row["level_recommended"] or 0),
            summary=best_row["legend"] or "",
            quick_facts={},
            prerequisites=[],
            fast_path=[],
            key_items=[],
            key_npcs=[],
            warnings=[],
            boss_insights=self._load_boss_insights(name.replace(" ", "-")),
            source="db_only",
        )
