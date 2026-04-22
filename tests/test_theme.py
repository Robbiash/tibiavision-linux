"""Smoke tests for the token-driven stylesheet builder.

These guard against a common regression: someone adds a field to
:class:`tvlinux.theme.Palette` but forgets to reference it anywhere in
:func:`tvlinux.theme.build_qss`, leaving a dead token nobody notices.
"""

from __future__ import annotations

import dataclasses

from tvlinux.theme import TOKENS, Palette, Tokens, build_qss


def test_every_palette_color_is_referenced_in_qss():
    qss = build_qss(TOKENS)
    for field in dataclasses.fields(Palette):
        value = getattr(TOKENS.palette, field.name)
        assert value in qss, f"palette token {field.name!r} ({value}) not referenced in QSS"


def test_build_qss_is_pure():
    # Two consecutive calls with the same tokens produce identical output.
    assert build_qss(TOKENS) == build_qss(TOKENS)


def test_build_qss_respects_custom_tokens():
    custom = Tokens(palette=Palette(accent="#ff00ff"))
    qss = build_qss(custom)
    assert "#ff00ff" in qss
    # And the default accent is *not* in the output anymore.
    assert TOKENS.palette.accent not in qss
