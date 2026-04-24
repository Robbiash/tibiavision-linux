# Safety, BattlEye, and the Tibia Rules

## Short version

TibiaVision-Linux is a **pure, read-only screen-mirroring application**. From the point of view
of the Tibia client and BattlEye, it is functionally identical to OBS Studio, Discord screen share,
or the GNOME/KDE screenshot tool.

**Hunt stats refresh only when *you* press Tibia's built-in "Copy to clipboard" menu entry.**
The app watches the OS clipboard, not Tibia. We never synthesize clicks, never synthesize keystrokes,
never observe keyboard input, and never call `uinput` or any input-injection API. There is no
"Hunt Mode auto-click," no passive key listener, and no calibration of in-game click targets --
those code paths were removed so the binary cannot do those things even in principle.

We do not, at any point:

- Read or write Tibia's process memory (`ptrace`, `/proc/<pid>/mem`, `process_vm_readv`, ...).
- Inject code, DLLs, `.so`s, or any shared library into the Tibia process.
- Hook, detour, or monkey-patch any Tibia, BattlEye, Qt, or Wayland API.
- Write, modify, or delete **any** file belonging to the Tibia client.
- Open sockets to Tibia's game servers or inspect Tibia's network traffic.
- Emulate or inject keyboard/mouse input into the Tibia window.

The main data source between Tibia and TibiaVision-Linux is **pixels, handed to us by the
Wayland compositor via the standard XDG Desktop Portal**, exactly the same pipeline every
legitimate screen-recording application uses.

### Full disclosure: read-only reads we do perform

So the safety story matches the code exactly, here is every non-pixel touchpoint the app has:

- **`~/.local/share/CipSoft GmbH/Tibia/packages/Tibia/conf/clientoptions.json`** - opened
  **read-only** (never written) to discover your current hotkey preset name and the keys you
  bound to actions/items/spells. Tibia itself rewrites this file whenever you change hotkeys or
  log in, so we simply read what Tibia has already saved for you. This powers the hotbar HUD
  panel and the preset-aware profile switch trigger. The override env var is
  `TIBIAVISION_TIBIA_DATA` for non-standard installs. See
  [`src/tvlinux/tibia_data.py`](../src/tvlinux/tibia_data.py) and
  [`src/tvlinux/analyzers/preset_watcher.py`](../src/tvlinux/analyzers/preset_watcher.py).
- **The OS clipboard** - we subscribe to `QClipboard.dataChanged` and parse the text **only**
  when it starts to look like a Tibia Hunt Analyser or Party Hunt payload, which the user
  produced by pressing Tibia's built-in "Copy to clipboard" menu themselves. We do not write
  the clipboard unless you click "Copy" on a result we produced.
- **Global keyboard shortcuts** are registered via
  `org.freedesktop.portal.GlobalShortcuts`. The compositor decides whether our app receives
  each chosen combo; we never hook or intercept input that is not dispatched to us.

## How Linux screen capture actually works

On modern Linux desktops, applications cannot read other applications' windows directly. Instead,
the compositor (KWin on KDE Plasma Wayland, Mutter on GNOME, sway/Hyprland on wlroots) exposes a
sandbox-friendly D-Bus API called the **XDG Desktop Portal**:

1. TibiaVision-Linux asks `org.freedesktop.portal.ScreenCast` for permission to capture a window.
2. The **compositor itself** shows a native picker ("Which window do you want to share?").
3. The user selects the Tibia window.
4. The compositor sets up a **PipeWire** video stream and hands us a file descriptor.
5. Each frame we receive is a pixel buffer the compositor has already rendered to your display.

At no point does our process get to choose, inspect, or even know which window it is capturing
until after the user explicitly grants consent. Everything happens through public APIs.

## Why BattlEye cannot object

BattlEye client-side defenses typically look for:

| BattlEye check           | What it flags                                | Does TibiaVision-Linux trip it? |
|--------------------------|----------------------------------------------|----------------------------------|
| Memory scanning          | Unauthorized reads/writes to game memory     | No - we never touch game memory |
| Process / DLL injection  | Foreign modules loaded into Tibia            | No - separate process, no injection |
| API hooking              | IAT/inline hooks on Windows API / syscalls   | No - we use only public portal APIs |
| File system monitoring   | Unauthorized writes or tampering with game files | No - we never write game files; `clientoptions.json` is opened read-only |
| Network traffic analysis | Packet injection / MITM                      | No - no sockets to game servers |
| Input emulation          | Automated keyboard/mouse sending             | No - we never call `uinput` or Wayland input |

By construction, **there is nothing for BattlEye to detect**. Our process runs with the same
syscalls and file descriptors as `obs`, `spectacle`, `gnome-screenshot`, or `discord`. If any of
those were bannable, half the Tibia player base streaming on Twitch and YouTube would already be
banned.

## CipSoft's official position

When asked about comparable external tools (virtual desktop software, screen-mirroring helpers),
CipSoft Customer Support has stated:

> These programs are not illegal but are also not supported. This means you won't be punished for
> using them, but you are using the programs at your own risk. Note that we strongly recommend
> not to use such programs as it poses a high risk for your account. Also, please understand that
> you lose all rights to support if you experience problems with the Tibia client due to using an
> external program.

TibiaVision-Linux inherits this policy. We are not affiliated with CipSoft; you assume all risk
associated with using external software alongside the Tibia client. If your client behaves oddly,
**close TibiaVision-Linux first** before opening a support ticket.

## What we would never do

Even if it were technically possible, TibiaVision-Linux will never ship any of the following:

- Bot / macro / automation features (input injection, auto-login, auto-loot, auto-heal, ...).
- Memory-reading "helper" features (reading HP/mana numbers out of game memory).
- Packet-level features (reading/altering protocol traffic).
- In-game overlay injected into Tibia's own window (that would require hooking Tibia).

We restrict ourselves to **pixels on your screen** plus the read-only, user-triggered surfaces
disclosed above (the Tibia `clientoptions.json` file and the OS clipboard). Anything derived
from those sources (future v2 OCR, cooldown computer-vision, hotbar cheat-sheet) happens in
our process, on data you were already looking at or explicitly copied.

## Overlay windows vs. the Smart HUD

Two kinds of "on top of the game" windows live in this app, and they behave very differently:

- **Smart HUD** (`src/tvlinux/smart_hud.py`) - full-screen, transparent, **strictly
  click-through** (`Qt.WindowTransparentForInput` + `WA_TransparentForMouseEvents`).
  Every mouse click and keypress passes right through to Tibia. You cannot accidentally block
  the game with it.
- **Mirror windows** (`src/tvlinux/mirror_window.py`) - standalone frameless windows that
  display a captured region. They are **normal windows**: when unlocked you drag and resize
  them, and when locked the window still exists and will still intercept clicks that land on
  it. Lock is a UX hint ("don't move this by accident"), not click-through.
- **Floating region picker** (`src/tvlinux/region_picker.py::FloatingRegionPicker`) - a
  borderless, stay-on-top window that shows the live portal capture at 1:1 so you can draw
  rectangles on what looks like a second copy of your Tibia window. Like the mirrors it is
  a **normal window**: it sits above other windows because we set
  `Qt.WindowStaysOnTopHint`, but it only renders frames we already receive from the XDG
  ScreenCast portal and receives only its own mouse input. It does not read Tibia's window
  geometry, it does not forward input to Tibia, and it grants no new capabilities beyond
  the capture stream the user already approved.

If a mirror or the picker covers part of the Tibia window, it can block the clicks underneath
it. That is expected desktop behaviour (same as any OBS preview or VLC window), not an
"interference" with Tibia. Move or resize the window so it sits beside the game, not on top
of it, if you want the clicks to reach Tibia directly.
