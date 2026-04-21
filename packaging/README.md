# Packaging assets

Artifacts consumed by the Flatpak build:

- `gg.tibiavision.Linux.desktop` - freedesktop.org desktop entry.
- `gg.tibiavision.Linux.metainfo.xml` - AppStream metadata (required by Flathub).
- `icon-256.png`, `icon-128.png` - app icons referenced by the desktop file.

The icon files are not versioned in this branch yet; drop a 256x256 and 128x128 PNG here
before building. A placeholder SVG can be generated with:

```bash
inkscape icon.svg --export-width=256 --export-filename=packaging/icon-256.png
inkscape icon.svg --export-width=128 --export-filename=packaging/icon-128.png
```

The CI workflow (`.github/workflows/flatpak.yml`) asserts that both files are present
before running `flatpak-builder`.
