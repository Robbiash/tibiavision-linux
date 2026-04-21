# Safety, BattlEye, and the Tibia Rules

## Short version

TibiaVision-Linux is a **pure, read-only screen-mirroring application**. From the point of view
of the Tibia client and BattlEye, it is functionally identical to OBS Studio, Discord screen share,
or the GNOME/KDE screenshot tool. We do not, at any point:

- Read or write Tibia's process memory (`ptrace`, `/proc/<pid>/mem`, `process_vm_readv`, ...).
- Inject code, DLLs, `.so`s, or any shared library into the Tibia process.
- Hook, detour, or monkey-patch any Tibia, BattlEye, Qt, or Wayland API.
- Read files under the Tibia client installation directory or its config/cache paths.
- Open sockets to Tibia's game servers or inspect Tibia's network traffic.
- Emulate or inject keyboard/mouse input into the Tibia window.

The only data that ever flows between Tibia and TibiaVision-Linux is **pixels, handed to us by the
Wayland compositor via the standard XDG Desktop Portal**, exactly the same pipeline every
legitimate screen-recording application uses.

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
| File system monitoring   | Unauthorized reads of game files             | No - we never touch game files |
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

We restrict ourselves to **pixels on your screen**. Anything derived from those pixels (future v2
OCR, cooldown computer-vision) happens in our process, on pixels you were already looking at.
