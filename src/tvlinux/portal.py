"""XDG Desktop Portal ScreenCast client.

This module speaks the async D-Bus dance required to obtain a PipeWire stream for a
user-selected window:

1. ``CreateSession``  -> get a session handle
2. ``SelectSources``  -> tell the compositor "I want a single window, with embedded cursor"
3. ``Start``          -> compositor shows its picker, user chooses a window
4. ``OpenPipeWireRemote`` -> get a file descriptor we hand to GStreamer ``pipewiresrc``

Each portal call uses a random ``handle_token`` and listens for the matching
``Response`` signal on the returned ``Request`` object.

Reference:
  https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Any

from dbus_next import BusType, Variant  # type: ignore[import-not-found]
from dbus_next.aio import MessageBus  # type: ignore[import-not-found]

from .logging_config import get_logger

log = get_logger(__name__)

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"

# SelectSources: types bitfield
SOURCE_TYPE_MONITOR = 1
SOURCE_TYPE_WINDOW = 2
SOURCE_TYPE_VIRTUAL = 4

# cursor_mode bitfield
CURSOR_MODE_HIDDEN = 1
CURSOR_MODE_EMBEDDED = 2
CURSOR_MODE_METADATA = 4


class PortalError(RuntimeError):
    """Raised when the portal rejects a request or no backend is available."""


@dataclass
class PipeWireStream:
    """A single stream handed to us by the portal."""

    node_id: int
    size: tuple[int, int] | None
    source_type: int | None
    position: tuple[int, int] | None
    mapping_id: str | None


@dataclass
class ScreenCastSession:
    session_handle: str
    streams: list[PipeWireStream]
    pipewire_fd: int


def _token() -> str:
    """Random token used both for request/session handles (alphanum required)."""
    return "tv_" + secrets.token_hex(8)


class ScreenCastPortal:
    """Async ScreenCast portal client wrapped in a small, typed facade."""

    def __init__(self) -> None:
        self._bus: MessageBus | None = None
        self._iface: Any | None = None
        self._sender: str | None = None

    async def connect(self) -> None:
        self._bus = await MessageBus(bus_type=BusType.SESSION).connect()
        introspection = await self._bus.introspect(BUS, PATH)
        proxy = self._bus.get_proxy_object(BUS, PATH, introspection)
        self._iface = proxy.get_interface(SCREENCAST_IFACE)
        # dbus-next exposes the bus' unique name (":1.xx") we need to build Request paths.
        unique = self._bus.unique_name or ""
        self._sender = unique.replace(".", "_").lstrip(":")
        log.debug("portal.connected", sender=self._sender)

    async def close(self) -> None:
        if self._bus is not None:
            self._bus.disconnect()
            self._bus = None
            self._iface = None

    def _request_path(self, token: str) -> str:
        return f"/org/freedesktop/portal/desktop/request/{self._sender}/{token}"

    async def _call_request(
        self,
        method_name: str,
        args: list[Any],
        options: dict[str, Variant],
    ) -> dict[str, Any]:
        """Invoke a portal method returning a Request object; await its Response signal."""
        assert self._bus is not None and self._iface is not None

        handle_token = _token()
        options["handle_token"] = Variant("s", handle_token)
        expected_path = self._request_path(handle_token)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        # Subscribe to the Request before issuing the call to avoid a race.
        req_introspection = await self._bus.introspect(BUS, expected_path)
        req_proxy = self._bus.get_proxy_object(BUS, expected_path, req_introspection)
        req_iface = req_proxy.get_interface(REQUEST_IFACE)

        def on_response(response: int, results: dict[str, Variant]) -> None:
            if future.done():
                return
            if response != 0:
                future.set_exception(
                    PortalError(f"{method_name} failed: response={response}, results={results}")
                )
            else:
                future.set_result({k: v.value for k, v in results.items()})

        req_iface.on_response(on_response)

        call = getattr(self._iface, f"call_{method_name}")
        actual_handle = await call(*args, options)
        if actual_handle != expected_path:
            log.debug(
                "portal.handle_mismatch",
                expected=expected_path,
                got=actual_handle,
            )
            # Some backends hand back a different request path; subscribe to that one too.
            act_introspection = await self._bus.introspect(BUS, actual_handle)
            act_proxy = self._bus.get_proxy_object(BUS, actual_handle, act_introspection)
            act_proxy.get_interface(REQUEST_IFACE).on_response(on_response)

        try:
            return await asyncio.wait_for(future, timeout=120.0)
        except asyncio.TimeoutError as e:
            raise PortalError(f"{method_name} timed out after 120s") from e

    async def _get_version(self) -> int:
        assert self._bus is not None
        introspection = await self._bus.introspect(BUS, PATH)
        proxy = self._bus.get_proxy_object(BUS, PATH, introspection)
        props = proxy.get_interface("org.freedesktop.DBus.Properties")
        ver = await props.call_get(SCREENCAST_IFACE, "version")  # type: ignore[attr-defined]
        return int(ver.value)

    async def start_session(
        self,
        *,
        source_types: int = SOURCE_TYPE_WINDOW | SOURCE_TYPE_MONITOR,
        cursor_mode: int = CURSOR_MODE_EMBEDDED,
        multiple: bool = False,
        persist_mode: int = 2,  # persist until explicitly revoked, ignored on older portals
        restore_token: str | None = None,
    ) -> ScreenCastSession:
        """Run the full portal handshake and return an active session + PipeWire fd."""
        if self._iface is None:
            await self.connect()
        assert self._iface is not None

        try:
            version = await self._get_version()
        except Exception:  # pragma: no cover - optional introspection
            version = 1
        log.info("portal.version", version=version)

        # 1. CreateSession
        session_token = _token()
        create_opts: dict[str, Variant] = {
            "session_handle_token": Variant("s", session_token),
        }
        create_res = await self._call_request("create_session", [], create_opts)
        session_handle = create_res["session_handle"]
        log.info("portal.session.created", session=session_handle)

        # 2. SelectSources
        select_opts: dict[str, Variant] = {
            "types": Variant("u", source_types),
            "multiple": Variant("b", multiple),
            "cursor_mode": Variant("u", cursor_mode),
        }
        if version >= 4:
            select_opts["persist_mode"] = Variant("u", persist_mode)
            if restore_token:
                select_opts["restore_token"] = Variant("s", restore_token)
        await self._call_request("select_sources", [session_handle], select_opts)
        log.info("portal.sources.selected", types=source_types, cursor_mode=cursor_mode)

        # 3. Start (triggers user-facing compositor picker)
        start_res = await self._call_request(
            "start",
            [session_handle, ""],  # parent_window
            {},
        )
        streams_raw = start_res.get("streams") or []
        streams: list[PipeWireStream] = []
        for node_id, props in streams_raw:
            streams.append(
                PipeWireStream(
                    node_id=int(node_id),
                    size=(
                        tuple(props["size"].value) if "size" in props else None  # type: ignore[arg-type]
                    ),
                    source_type=(int(props["source_type"].value) if "source_type" in props else None),
                    position=(
                        tuple(props["position"].value) if "position" in props else None  # type: ignore[arg-type]
                    ),
                    mapping_id=(
                        str(props["mapping_id"].value) if "mapping_id" in props else None
                    ),
                )
            )
        if not streams:
            raise PortalError("portal returned no streams")
        log.info("portal.started", streams=len(streams))

        # 4. OpenPipeWireRemote (returns a unix_fd)
        fd = await self._iface.call_open_pipe_wire_remote(session_handle, {})  # type: ignore[attr-defined]
        fd_int = int(fd)
        log.info("portal.pipewire_fd.opened", fd=fd_int)

        return ScreenCastSession(
            session_handle=session_handle,
            streams=streams,
            pipewire_fd=fd_int,
        )
