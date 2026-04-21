"""Stub: 'Ask Opus' copilot.

v2 will implement a single-shot "Ask Opus 4.7" button that:

  - Grabs a snapshot from the nominated mirror region(s),
  - Serializes to PNG,
  - Calls the Anthropic Messages API with a vision-enabled Claude Opus 4.7 request:

        POST https://api.anthropic.com/v1/messages
        {
          "model": "claude-opus-4-7",
          "max_tokens": 1024,
          "messages": [{
            "role": "user",
            "content": [
              {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                           "data": "<...>"}},
              {"type": "text", "text": "I'm playing Tibia. Given this screenshot of my
                                        spell bar / hunt area, what should I consider?"}
            ]
          }]
        }

The API key lives in libsecret (via ``QtKeychain`` in v2); nothing leaves the machine
unless the user explicitly clicks the button. **Off by default**; we document this
prominently in the v2 settings UI and About dialog update.
"""

from __future__ import annotations

from .base import Analyzer, AnalyzerFrame, Event


class CopilotAnalyzer(Analyzer):
    id = "copilot"

    def __init__(self) -> None:
        super().__init__()
        self.enabled = False  # opt-in + requires explicit API key entry

    def analyze(self, frame: AnalyzerFrame) -> list[Event]:
        return []
