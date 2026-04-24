# QA checklist (Bazzite / Plasma 6 Wayland)

Run through this list before tagging a release. The target environment is **Bazzite
stable, KDE Plasma 6 on Wayland**. Where behavior is expected to differ on other desktops
(GNOME, wlroots, Plasma X11), each item notes the expected variation.

## 1. Launch and capture

- [ ] `flatpak run gg.tibiavision.Linux` opens the control panel within 1.5 s.
- [ ] KDE's "Share screen" picker appears and lists the Tibia window by title.
- [ ] After selecting the Tibia window, the status bar shows
      `Capturing source <W>x<H>`.
- [ ] The first mirror window added is populated with frames within ~500 ms.
- [ ] Killing the Tibia client transitions the status bar to `Capture error: ...`
      without crashing the app.
- [ ] Re-launching the Tibia client and re-requesting capture via the portal works
      without a restart.

## 2. Region lifecycle

- [ ] "Add region" opens the picker dialog with a live preview of the captured window.
- [ ] Dragging a rubber-band rectangle produces a mirror window that matches the
      selection's aspect ratio.
- [ ] Unlocked mirror windows can be dragged from any pixel inside them.
- [ ] Each corner + edge hover produces the correct resize cursor.
- [ ] Double-click opens the rename dialog; the new name appears in the list.
- [ ] Right-click context menu: Lock, Hide, Rename, Border glow, Grid overlay, Delete
      all work.
- [ ] Deleting the last region empties the detail group and disables it.

## 3. Per-region visuals

- [ ] Opacity slider reaches 20% minimum and the window becomes near-transparent.
- [ ] Grid overlay respects `grid_spacing` (try 8, 16, 32).
- [ ] Border glow pulses at ~0.55 Hz (a full cycle every ~1.8 s).
- [ ] Always-on-top holds across `Super+Tab` window switches.

## 3b. Always-on-top vs. Tibia (layer-shell regression suite)

The single most important promise of this app. On Plasma 6 Wayland the
layer-shell overlay path kicks in; on GNOME / Mutter and X11 the
reactive-re-raise path (focus-change handler + 1.5 s safety-net timer)
handles the same problem less perfectly.

- [ ] Start the app, add one mirror. Click the Tibia window. Mirror
      stays visibly on top without any further interaction.
- [ ] Toggle Tibia to true fullscreen (F11 or its in-game setting).
      Mirror is still visible (layer-shell overlay layer). On
      non-Plasma compositors, switch Tibia back to borderless-windowed
      or use Companion view.
- [ ] Alt-tab to a browser, then alt-tab back to Tibia. Mirror
      returns to top within ~1.5 s (safety-net timer budget).
- [ ] Add a second mirror while Tibia is focused. Both mirrors end
      up on top; neither disappears behind Tibia.
- [ ] Press `Delete` while the mirror is hovered/focused (unlocked).
      The region is removed. (Regression guard: confirms keyboard
      routing still works when the mirror is explicitly interactive.)
- [ ] Unlock a mirror and drag it across the screen. Drag-to-move
      still works. (Regression guard: `startSystemMove` still
      functions despite the reactive re-raise trying to `raise_()`
      the window.)

## 3c. Click-through when locked (input-transparency regression suite)

The second load-bearing promise: a locked mirror must NOT eat clicks or
keystrokes intended for Tibia. On Wayland this relies on
`WA_TransparentForMouseEvents` (which maps to
`wl_surface.set_input_region(empty)`) plus, on the layer-shell path,
`KeyboardInteractivity::None`.

- [ ] Lock a mirror (right-click while unlocked -> "Lock", or
      `Ctrl+Shift+L` globally). Move the mouse INSIDE the mirror's
      rectangle and click. Tibia receives the click as if the mirror
      weren't there (character walks, spell casts, whatever is
      appropriate).
- [ ] Drag-to-move Tibia's window across the screen STARTING from
      inside a locked mirror. The drag passes through to Tibia's
      title bar area. (On Wayland this is only meaningful if the
      mirror overlaps Tibia's client area; the gesture itself is
      what's being tested.)
- [ ] While a locked mirror is visibly on top of Tibia, press
      hotkeys that Tibia reads directly (function keys, spell
      slots, WASD). Tibia receives every keystroke; the mirror's
      `keyPressEvent` is never invoked.
- [ ] `Ctrl+Shift+L` while all mirrors are locked unlocks all of
      them (status bar confirms). Pressing it again re-locks all.
- [ ] Unlock a mirror, right-click it. Context menu appears.
      Click "Lock". Mirror immediately becomes click-through; the
      next click in its area reaches Tibia. (No show/hide cycle
      required -- the input region updates on the next commit.)

## 4. Profiles

- [ ] "Save as..." creates a new profile and marks it bold in the profiles list.
- [ ] "Load" swaps regions atomically; no flicker.
- [ ] "Export" writes a `.json` file that "Import" can round-trip.
- [ ] Importing a file that clashes with an existing profile name is renamed
      `Name (2)` automatically.
- [ ] `Ctrl+Shift+P` cycles profiles even when the Tibia client is focused
      (requires `GlobalShortcuts` portal; falls back gracefully on older backends).

## 5. Audio timers

- [ ] Creating a timer with a .wav file plays back on expiry.
- [ ] Hotkey slot 0-9 starts the corresponding timer via the Shortcuts portal.
- [ ] Countdown progress bar reflects remaining seconds at 10 Hz.
- [ ] Timers persist across app restarts.

## 6. Multi-monitor

- [ ] Mirror windows placed on a secondary monitor reopen on that monitor after restart.
- [ ] Moving a mirror to a different monitor updates the persisted `geometry`.
- [ ] Disconnecting and reconnecting a monitor keeps the mirror's position relative to
      the capture's source coordinates (the mirror reappears at saved position when
      monitor returns).

## 7. Performance

- [ ] `top` / `systemd-cgtop` shows < 3% total CPU for the app with 5 mirrors at 60 fps
      on integrated graphics.
- [ ] GPU usage is within noise of baseline (KDE's compositor already does the
      expensive compositing work).
- [ ] Heap RSS stays under ~300 MB steady state (PySide6's Python interpreter baseline
      dominates; PipeWire frames are not retained).

## 8. Sandbox / Flatpak

- [ ] `flatpak info --show-permissions gg.tibiavision.Linux` contains only:
      `wayland, fallback-x11, dri, xdg-run/pipewire-0, pulseaudio,
      portal.{Desktop, ScreenCast, GlobalShortcuts, Background, Notifications},
      StatusNotifierWatcher`.
- [ ] The app works with `flatpak override --user --nofilesystem=home` applied
      (i.e. it must not secretly rely on `--filesystem=home`).
- [ ] `flatpak update gg.tibiavision.Linux` succeeds without layering any rpms on the
      Bazzite host.

## 9. Safety assertions (can be automated in later releases)

- [ ] `strace -e trace=ptrace,process_vm_readv -p $(pidof tvlinux)` shows zero calls for
      a 5-minute run.
- [ ] `lsof -p $(pidof tvlinux)` shows no open file descriptors under the Tibia client
      install directory, cache, or config.
- [ ] `ss -p | grep tvlinux` shows no sockets to Tibia's game-server ports.
- [ ] No shared libraries from the Tibia install dir are loaded into the `tvlinux`
      process (`cat /proc/$(pidof tvlinux)/maps`).

## 10. Cross-desktop smoke

- [ ] GNOME 46 Wayland: portal picker appears (Mutter backend); capture works with
      slightly different stream metadata. Mutter does NOT ship
      `wlr-layer-shell-v1`; mirrors fall back to the reactive re-raise
      path. For true-fullscreen Tibia there, switch to Companion view.
- [ ] sway/Hyprland (wlroots): picker appears (wlr portal), capture works.
      `wlr-layer-shell-v1` is supported, so mirrors sit above fullscreen.
- [ ] Plasma X11 session: falls back through portal; may require
      `xdg-desktop-portal-gtk` as a secondary. Document if so. Layer-shell
      does not apply on X11; the reactive re-raise path handles
      always-on-top.
