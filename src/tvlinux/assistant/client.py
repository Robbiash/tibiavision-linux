"""Thin xAI chat-completions client used by the desktop assistant."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

__all__ = ["XAIClient", "XAIError"]


class XAIError(RuntimeError):
    """Raised for configuration, transport, or API-level failures."""


def _normalize_model_name(raw: str) -> str:
    """Normalize user-facing aliases to concrete xAI model ids."""
    cleaned = raw.strip()
    if not cleaned:
        raise XAIError("Model cannot be empty.")
    key = cleaned.lower().replace("_", " ")
    grok4_model = (
        os.environ.get("XAI_GROK4_MODEL", "grok-4.20-0309-non-reasoning").strip()
        or "grok-4.20-0309-non-reasoning"
    )
    unhinged_model = os.environ.get("XAI_UNHINGED_MODEL", grok4_model).strip() or grok4_model
    alias_map = {
        "grok4": grok4_model,
        "grok 4": grok4_model,
        "grok-4": grok4_model,
        "grok unhinged": unhinged_model,
        "grok-unhinged": unhinged_model,
        "unhinged": unhinged_model,
    }
    return alias_map.get(key, cleaned)


@dataclass(slots=True)
class XAIClient:
    api_key: str | None = None
    model: str = "grok-4.20-0309-non-reasoning"
    base_url: str = "https://api.x.ai/v1"
    timeout_s: float = 60.0

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.environ.get("XAI_API_KEY")
        env_model = os.environ.get("XAI_MODEL")
        if env_model:
            self.set_model(env_model)
        env_base_url = os.environ.get("XAI_BASE_URL")
        if env_base_url:
            self.base_url = env_base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def set_api_key(self, api_key: str) -> None:
        self.api_key = api_key.strip()

    def set_model(self, model: str) -> None:
        self.model = _normalize_model_name(model)

    def chat(
        self,
        *,
        system_prompt: str,
        messages: Sequence[dict[str, str]],
        temperature: float = 0.9,
        top_p: float = 1.0,
    ) -> str:
        if not self.configured:
            raise XAIError(
                "Missing xAI API key. Set XAI_API_KEY or enter it in the app connection panel."
            )
        payload_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for item in messages:
            role = str(item.get("role", "")).strip()
            content = str(item.get("content", "")).strip()
            if role not in {"system", "user", "assistant"} or not content:
                continue
            payload_messages.append({"role": role, "content": content})

        if len(payload_messages) <= 1:
            raise XAIError("No valid conversation messages were supplied.")

        payload = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": max(0.0, min(1.5, float(temperature))),
            "top_p": max(0.05, min(1.0, float(top_p))),
        }
        raw = self._post_json("/chat/completions", payload)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise XAIError(f"xAI returned invalid JSON: {exc}") from exc

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise XAIError(f"xAI response did not contain choices: {data!r}")
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first, dict) else None
        if not isinstance(message, dict):
            raise XAIError(f"xAI response did not contain a message object: {data!r}")
        return _extract_content(message.get("content"))

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> str:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            url=f"{self.base_url.rstrip('/')}{endpoint}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise XAIError(f"xAI API error ({exc.code}): {detail or exc.reason}") from exc
        except URLError as exc:
            raise XAIError(f"Network error while calling xAI: {exc.reason}") from exc
        return raw


def _extract_content(content: Any) -> str:
    if isinstance(content, str):
        cleaned = content.strip()
        if cleaned:
            return cleaned
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                piece = item.strip()
                if piece:
                    parts.append(piece)
                continue
            if not isinstance(item, dict):
                continue
            text_piece = item.get("text")
            if isinstance(text_piece, str) and text_piece.strip():
                parts.append(text_piece.strip())
        if parts:
            return "\n".join(parts)
    raise XAIError("xAI returned an empty assistant message.")
