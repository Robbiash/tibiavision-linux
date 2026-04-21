"""Global hotkeys via ``org.freedesktop.portal.GlobalShortcuts``.

This is the only portable Wayland mechanism for application-defined global shortcuts.
On older portals/backends that do not implement it, we log a warning and run without
hotkeys - no fallback to insecure grabs is attempted.

Reference:
  https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html
"""

from __future__ import annotations

import asyncio
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, Signal

from .logging_config import get_logger

log = get_logger(__name__)

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_IFACE = "org.freedesktop.portal.Request"


@dataclass
class ShortcutSpec:
    id: str
    description: str
    default_trigger: str = ""  # e.g. "CTRL+SHIFT+p" (portal-defined, backend-interpreted)


def _token() -> str:
    return "tv_sc_" + secrets.token_hex(6)


class GlobalShortcutManager(QObject):
    activated = Signal(str)  # emits shortcut id

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: str | None = None
        self._handlers: dict[str, Callable[[], None]] = {}

    # -- Lifecycle --------------------------------------------------------------------

    def start(self, shortcuts: list[ShortcutSpec]) -> None:
        self._start_loop()
        assert self._loop is not None
        asyncio.run_coroutine_threadsafe(self._register(shortcuts), self._loop)

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
            self._loop = None

    def register_handler(self, shortcut_id: str, handler: Callable[[], None]) -> None:
        self._handlers[shortcut_id] = handler

    def _start_loop(self) -> None:
        if self._thread is not None:
            return
        ready = threading.Event()

        def runner() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            ready.set()
            try:
                loop.run_forever()
            finally:
                loop.close()

        self._thread = threading.Thread(target=runner, name="tvlinux-shortcuts", daemon=True)
        self._thread.start()
        ready.wait()

    # -- Portal interaction -----------------------------------------------------------

    async def _register(self, shortcuts: list[ShortcutSpec]) -> None:
        try:
            from dbus_next import BusType, Variant  # type: ignore[import-not-found]
            from dbus_next.aio import MessageBus  # type: ignore[import-not-found]
        except ImportError:
            log.warning("shortcuts.no_dbus")
            return

        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        try:
            intro = await bus.introspect(BUS, PATH)
            proxy = bus.get_proxy_object(BUS, PATH, intro)
            try:
                iface = proxy.get_interface(IFACE)
            except Exception:
                log.info("shortcuts.portal_unavailable")
                return
        except Exception as e:
            log.warning("shortcuts.introspect_failed", error=str(e))
            return

        sender = (bus.unique_name or "").replace(".", "_").lstrip(":")

        async def call_request(
            method: str, args: list[Any], options: dict[str, Variant]
        ) -> dict[str, Any]:
            token = _token()
            options["handle_token"] = Variant("s", token)
            expected = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[dict[str, Any]] = loop.create_future()

            req_intro = await bus.introspect(BUS, expected)
            req_proxy = bus.get_proxy_object(BUS, expected, req_intro)
            req_iface = req_proxy.get_interface(REQUEST_IFACE)

            def on_response(response: int, results: dict[str, Variant]) -> None:
                if fut.done():
                    return
                if response != 0:
                    fut.set_exception(RuntimeError(f"{method} failed: {response}"))
                else:
                    fut.set_result({k: v.value for k, v in results.items()})

            req_iface.on_response(on_response)
            actual = await getattr(iface, f"call_{method}")(*args, options)
            if actual != expected:
                act_intro = await bus.introspect(BUS, actual)
                act_proxy = bus.get_proxy_object(BUS, actual, act_intro)
                act_proxy.get_interface(REQUEST_IFACE).on_response(on_response)
            return await fut

        # 1. CreateSession
        session_opts: dict[str, Variant] = {
            "session_handle_token": Variant("s", _token()),
        }
        res = await call_request("create_session", [], session_opts)
        self._session = res["session_handle"]
        log.info("shortcuts.session.created", session=self._session)

        # 2. BindShortcuts
        shortcut_arr = []
        for s in shortcuts:
            entry: dict[str, Variant] = {
                "description": Variant("s", s.description),
            }
            if s.default_trigger:
                entry["preferred_trigger"] = Variant("s", s.default_trigger)
            shortcut_arr.append((s.id, entry))
        try:
            await call_request(
                "bind_shortcuts",
                [self._session, shortcut_arr, ""],
                {},
            )
        except Exception as e:
            log.warning("shortcuts.bind_failed", error=str(e))
            return

        # 3. Connect to the Activated signal
        def on_activated(
            session_handle: str,
            shortcut_id: str,
            _timestamp: int,
            _options: dict[str, Variant],
        ) -> None:
            if session_handle != self._session:
                return
            log.info("shortcuts.activated", id=shortcut_id)
            handler = self._handlers.get(shortcut_id)
            if handler is not None:
                handler()
            self.activated.emit(shortcut_id)

        try:
            iface.on_activated(on_activated)  # type: ignore[attr-defined]
        except Exception as e:
            log.warning("shortcuts.signal_attach_failed", error=str(e))
