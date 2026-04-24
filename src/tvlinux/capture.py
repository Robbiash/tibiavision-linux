"""Capture core.

Owns the lifecycle of the XDG ScreenCast portal session and the GStreamer pipeline
that consumes the resulting PipeWire stream and turns it into ``QImage`` frames.

Design:
- Portal work runs in a dedicated asyncio event loop on a worker thread, so the Qt main
  thread never blocks on D-Bus round trips.
- GStreamer is driven by its own GLib main loop on *another* worker thread, so GStreamer
  message handling and appsink callbacks never stall the UI either.
- Frames are emitted via ``frame_ready(QImage)``. Mirror windows subscribe to this and
  crop their own sub-rect in their own ``paintEvent``. There is intentionally no per-mirror
  capture session; one portal session fans out to N mirrors.
- A ``frame_buffer_ready(np.ndarray)`` signal is also emitted for v2 analyzers (OCR / CV).
  It is lazy: if no slot is connected, we skip the numpy conversion entirely.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import os
import threading
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QSize, Signal
from PySide6.QtGui import QImage

from .logging_config import get_logger
from .portal import PipeWireStream, PortalError, ScreenCastPortal, ScreenCastSession

log = get_logger(__name__)


def _import_gst() -> tuple[Any, Any, Any]:
    """Import GStreamer through PyGObject, raising a clean error if unavailable."""
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstApp", "1.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib, Gst, GstApp

    if not Gst.is_initialized():
        Gst.init(None)
    return Gst, GstApp, GLib


class CaptureCore(QObject):
    """Owns portal + GStreamer pipeline; emits frames and lifecycle signals."""

    started = Signal()
    stopped = Signal()
    errored = Signal(str)
    size_changed = Signal(QSize)
    # Full source frame as QImage (zero-copy view into GStreamer-owned memory; consumers
    # must ``copy()`` if they want to keep it).
    frame_ready = Signal(QImage)
    # Same frame as an (H, W, 4) uint8 numpy ndarray. Materialized per frame only when
    # ``buffer_output_enabled`` is True, so the numpy conversion stays off by default.
    frame_buffer_ready = Signal(object)

    def __init__(self, *, use_portal: bool = True, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._use_portal = use_portal
        self._portal: ScreenCastPortal | None = None
        self._session: ScreenCastSession | None = None
        self._pipeline: Any | None = None
        self._appsink: Any | None = None
        self._gst_loop: Any | None = None
        self._gst_thread: threading.Thread | None = None
        self._async_loop: asyncio.AbstractEventLoop | None = None
        self._async_thread: threading.Thread | None = None
        self._source_size = QSize(0, 0)
        self._running = False
        # When False we skip the (expensive) QImage -> numpy conversion. Flipped on by
        # components that actually need buffers (e.g. AnalyzerHub when any analyzer is
        # enabled).
        self.buffer_output_enabled: bool = False

    # -- Lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        """Kick off the portal handshake. Non-blocking; listen to ``started`` / ``errored``."""
        if self._running:
            log.warning("capture.start.already_running")
            return
        self._running = True
        if not self._use_portal:
            self.errored.emit(
                "Screen capture is disabled because --no-portal is set. "
                "Quit the app and relaunch it without --no-portal to get live frames."
            )
            return
        self._start_async_thread()
        assert self._async_loop is not None
        asyncio.run_coroutine_threadsafe(self._run_portal(), self._async_loop)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._teardown_pipeline()
        if self._async_loop and self._async_loop.is_running():
            self._async_loop.call_soon_threadsafe(self._async_loop.stop)
        if self._async_thread:
            self._async_thread.join(timeout=2.0)
            self._async_thread = None
        if self._gst_thread:
            if self._gst_loop:
                self._gst_loop.quit()
            self._gst_thread.join(timeout=2.0)
            self._gst_thread = None
        self._portal = None
        self._session = None
        self.stopped.emit()

    # -- Public read-only --------------------------------------------------------------

    @property
    def source_size(self) -> QSize:
        return QSize(self._source_size)

    @property
    def active_stream(self) -> PipeWireStream | None:
        if self._session and self._session.streams:
            return self._session.streams[0]
        return None

    # -- Internals --------------------------------------------------------------------

    def _start_async_thread(self) -> None:
        if self._async_thread is not None:
            return
        ready = threading.Event()

        def runner() -> None:
            loop = asyncio.new_event_loop()
            self._async_loop = loop
            asyncio.set_event_loop(loop)
            ready.set()
            try:
                loop.run_forever()
            finally:
                loop.close()

        t = threading.Thread(target=runner, name="tvlinux-portal", daemon=True)
        t.start()
        ready.wait()
        self._async_thread = t

    async def _run_portal(self) -> None:
        try:
            self._portal = ScreenCastPortal()
            await self._portal.connect()
            self._session = await self._portal.start_session()
        except PortalError as e:
            log.error("capture.portal_failed", error=str(e))
            self.errored.emit(f"Portal error: {e}")
            return
        except Exception as e:  # pragma: no cover - defensive
            log.exception("capture.portal_unexpected")
            self.errored.emit(f"Portal error: {e}")
            return

        stream = self._session.streams[0]
        if stream.size:
            self._source_size = QSize(*stream.size)
            self.size_changed.emit(self._source_size)

        self._build_pipeline(self._session.pipewire_fd, stream.node_id)

    def _build_pipeline(self, fd: int, node_id: int) -> None:
        try:
            Gst, _GstApp, GLib = _import_gst()
        except Exception as e:
            self.errored.emit(
                "Couldn't start screen capture because GStreamer or the "
                "PipeWire plugin is missing. On Fedora/Bazzite, install it "
                "with: sudo dnf install gstreamer1-plugin-pipewire "
                f"(details: {e})"
            )
            return

        # BGRA is Qt's Format_ARGB32 on little-endian (our only target).
        pipeline_desc = (
            f"pipewiresrc fd={fd} path={node_id} do-timestamp=true "
            "! videoconvert "
            "! video/x-raw,format=BGRA "
            "! appsink name=sink emit-signals=true max-buffers=2 drop=true sync=false"
        )
        log.info("capture.pipeline", desc=pipeline_desc)

        try:
            self._pipeline = Gst.parse_launch(pipeline_desc)
        except GLib.Error as e:
            self.errored.emit(f"Failed to build GStreamer pipeline: {e}")
            return

        self._appsink = self._pipeline.get_by_name("sink")
        self._appsink.connect("new-sample", self._on_new_sample)

        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

        # Start the GLib loop on its own thread.
        glib_ready = threading.Event()

        def glib_runner() -> None:
            self._gst_loop = GLib.MainLoop()
            glib_ready.set()
            self._gst_loop.run()

        self._gst_thread = threading.Thread(target=glib_runner, name="tvlinux-gst", daemon=True)
        self._gst_thread.start()
        glib_ready.wait()

        self._pipeline.set_state(Gst.State.PLAYING)
        log.info("capture.pipeline.playing", fd=fd, node_id=node_id)
        self.started.emit()

    def _on_bus_message(self, _bus: Any, message: Any) -> None:  # pragma: no cover
        from gi.repository import Gst  # type: ignore[import-not-found]

        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            log.error("capture.gst_error", error=str(err), debug=debug)
            self.errored.emit(str(err))
        elif t == Gst.MessageType.EOS:
            log.info("capture.gst_eos")
            self.stopped.emit()
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self._pipeline:
                old, new, _ = message.parse_state_changed()
                log.debug("capture.gst_state", from_=old.value_nick, to=new.value_nick)

    def _on_new_sample(self, sink: Any) -> Any:
        from gi.repository import Gst  # type: ignore[import-not-found]

        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        caps = sample.get_caps()
        s = caps.get_structure(0)
        width = s.get_value("width")
        height = s.get_value("height")

        new_size = QSize(width, height)
        if new_size != self._source_size:
            self._source_size = new_size
            self.size_changed.emit(new_size)

        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            # Build a QImage that references the mapped memory. QImage with a raw buffer
            # does NOT copy; we copy() below to decouple the frame lifetime from the
            # GStreamer mapinfo we're about to unmap.
            image = QImage(
                bytes(mapinfo.data),
                width,
                height,
                width * 4,
                QImage.Format.Format_ARGB32_Premultiplied,
            )
            image = image.copy()  # detach from the mapinfo memory
        finally:
            buf.unmap(mapinfo)

        self.frame_ready.emit(image)

        if self.buffer_output_enabled:
            arr = _qimage_to_ndarray(image)
            self.frame_buffer_ready.emit(arr)

        return Gst.FlowReturn.OK

    def _teardown_pipeline(self) -> None:
        if self._pipeline is not None:
            try:
                from gi.repository import Gst  # type: ignore[import-not-found]

                self._pipeline.set_state(Gst.State.NULL)
            except Exception:  # pragma: no cover
                log.exception("capture.teardown_failed")
            self._pipeline = None
            self._appsink = None
        if self._session and self._session.pipewire_fd >= 0:
            with contextlib.suppress(OSError):
                os.close(self._session.pipewire_fd)


def _qimage_to_ndarray(image: QImage) -> np.ndarray:
    """Zero-copy-ish view of a QImage's pixel buffer as an (H, W, 4) uint8 ndarray."""
    if image.format() != QImage.Format.Format_ARGB32_Premultiplied:
        image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    ptr = image.constBits()
    # PySide6: ``constBits`` returns a ``memoryview``-like object we can hand to numpy.
    arr = np.frombuffer(ptr, dtype=np.uint8, count=image.sizeInBytes())
    arr = arr.reshape((image.height(), image.width(), 4))
    # The caller is responsible for copying if it wants to outlive the QImage.
    return arr


# Silence an unused-import warning from static type checkers that don't like ctypes here.
_ = ctypes
