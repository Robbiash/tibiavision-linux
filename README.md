# TibiaVision-Linux

A Wayland-native, BattlEye-safe screen-mirroring overlay for the **official native Tibia Linux client**.
Targets **Bazzite** (KDE Plasma 6 / Wayland / rpm-ostree) but runs on any modern Linux desktop that
exposes the `org.freedesktop.portal.ScreenCast` XDG portal (KDE Plasma, GNOME, wlroots, etc.).

This is an independent, from-scratch rewrite inspired by the Windows-only
[TibiaVision](https://tibiavision.com/). It is not affiliated with CipSoft, BattlEye, or the
original TibiaVision project.

## Why

TibiaVision solves a very real pain for Tibia players: you can mirror any region of the client
(spell bar, cooldowns, items, battle list, minimap, ...) into floating always-on-top windows you
can arrange across multiple monitors. The Windows build does this using the DWM Thumbnail API.
On Linux, the equivalent primitive is the **XDG ScreenCast portal** producing a **PipeWire** stream,
which is exactly what OBS, Discord, Firefox, and every other "share your screen" app uses.

Because we never touch game memory, files, or network, the BattlEye safety story is the same as
upstream: we are indistinguishable, from the anti-cheat's point of view, from any other legitimate
screen-capturing application. See [docs/safety.md](docs/safety.md) for details.

## Features (v1)

- Multi-region screen mirroring with unlimited regions (no artificial free/premium split).
- Rubber-band region picker drawn over a live preview of the Tibia window.
- Per-region: transparency (20-100%), lock, visibility toggle, animated border glow, grid overlay.
- Profiles: save/load/import/export, cycle with a global hotkey.
- TibiaAudio-equivalent: countdown audio timers with custom sounds and global hotkeys.
- Dark theme, frameless mirror windows, always-on-top, drag/resize when unlocked.
- Shipped as a single **Flatpak** - no rpm-ostree layering required on Bazzite.

## Installation (Bazzite / Fedora Atomic)

Once a Flatpak bundle is published:

```bash
flatpak install --user gg.tibiavision.Linux.flatpak
flatpak run gg.tibiavision.Linux
```

From source (developer flow):

```bash
git clone https://github.com/tibiavision-linux/tibiavision-linux.git
cd tibiavision-linux
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
python -m tvlinux
```

On Bazzite you can also run the dev flow inside a Fedora Distrobox container so you don't need to
layer any packages onto the host.

## Requirements

- Linux kernel 5.15+
- PipeWire 0.3.48+ (pre-installed on Bazzite)
- Qt 6.7+ / PySide6 (pulled in by pip or provided by the `org.kde.Platform` runtime in Flatpak)
- GStreamer 1.24 with the `pipewire` plugin (`gstreamer1.0-pipewire` on Debian, bundled in the KDE
  Platform runtime on Flatpak)
- An XDG Desktop Portal backend (`xdg-desktop-portal-kde`, `...-gnome`, or `...-wlr`)
- **Strongly recommended on Wayland:** `layer-shell-qt` (Fedora:
  `kf6-layer-shell-qt`; bundled in the KDE Flatpak runtime). When present on a compositor that
  implements `wlr-layer-shell-v1` (KDE Plasma 6, Sway, Hyprland), mirror overlays sit above
  fullscreen Tibia windows by protocol. The app requests this integration by setting
  `QT_WAYLAND_SHELL_INTEGRATION=layer-shell` before Qt's Wayland plugin loads; if the Qt Wayland
  shell-integration plugin or the library can't load (e.g. a Qt ABI mismatch in a dev env),
  Qt transparently falls back to `xdg-shell` and our reactive re-raise path takes over. That
  fallback is reliable for borderless-windowed Tibia on compositors that honor
  `WindowStaysOnTopHint`, but cannot beat true fullscreen on GNOME / Mutter -- for that case,
  switch to **Companion view** (Settings -> Mirror placement).

## Safety and Terms of Service

Read [docs/safety.md](docs/safety.md) before using. TL;DR: we only read pixels the compositor
hands us through a standard desktop portal. We never inject input, read memory, read game files,
or touch game network traffic. Same risk profile as OBS or any other screen recorder.

CipSoft's official stance on non-supported external tools is: *"not illegal but not supported ...
at your own risk."* Using TibiaVision-Linux does not grant you any right to support from CipSoft
for issues with the Tibia client.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
