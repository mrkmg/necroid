#!/usr/bin/env bash
# Regenerate derived brand assets from assets/necroid.png.
# Requires ImageMagick (`magick` on PATH). Run from anywhere.
# Commit the outputs -- end users and dist builds do NOT need ImageMagick.
set -euo pipefail
cd "$(dirname "$0")"

SRC=necroid.png

# Source layout (measured by row-scan of bone pixels on 1024x1024 master):
#   skull region:    y = 160..480
#   wordmark region: y = 540..640
#   tagline:         y > 640

# Skull only: key out charcoal bg via -fuzz -transparent (range-match on the
# mid-gradient bg color), then -trim auto-finds the exact skull bbox, then
# extent square with padding on TRANSPARENT. Transparent bg lets the GUI's
# char_900 show through uniformly -- no gradient banding, no manual crop
# coords to drift when the source logo is re-exported.
#
# Pre-crop away the wordmark region (y>=540) and far edges so -trim locks
# onto the skull+chip-pins only, not the faint rounded-rect border.
magick "$SRC" \
    -crop 800x430+112+100 +repage \
    -fuzz 40% -transparent "#2b2b2e" \
    -trim +repage \
    -background none -gravity center -extent 720x720 \
    -resize 256x256 \
    necroid-mark-256.png

# Full brand mark at 256 (skull + wordmark + tagline + rounded-rect bg).
# Used for large-size contexts: Explorer jumbo thumbnails, README, .exe
# file icon at 128/256, GUI taskbar in large-icon mode.
magick "$SRC" \
    -resize 256x256 \
    necroid-icon-256.png

# Skull-only 128 for GUI small-icon slot (taskbar, title bar, Alt-Tab).
# Transparent bg, same style as necroid-mark-256.png (just smaller).
magick necroid-mark-256.png -resize 128x128 necroid-icon-skull-128.png

# Render each .ico frame from its best source, per-size for crisp downscales
# (instead of one lossy auto-resize from 256).
# Small sizes (16/32/48): skull-only transparent -- wordmark illegible there.
# Large sizes (64/128/256): full brand mark on its rounded-rect tile.
for s in 16 32 48; do
    magick necroid-mark-256.png -resize ${s}x${s} /tmp/ico-${s}.png
done
for s in 64 128 256; do
    magick necroid-icon-256.png -resize ${s}x${s} /tmp/ico-${s}.png
done

# Assemble multi-resolution .ico: skull-only for 16/32/48, full mark for 64+.
magick /tmp/ico-16.png /tmp/ico-32.png /tmp/ico-48.png \
       /tmp/ico-64.png /tmp/ico-128.png /tmp/ico-256.png \
       necroid-icon.ico

# Clean up scratch
rm -f /tmp/ico-*.png preview-ico-*.png

# .icns (macOS) is NOT generated here -- ImageMagick's ICNS coder is unavailable
# on Windows/Linux builds of magick, so we can't reliably author a multi-frame
# .icns cross-platform. Instead, packaging/build_dist.py generates assets/
# necroid-icon.icns on the fly when building on macOS, using Apple's iconutil
# (which ships with the macOS runner / Xcode CLT). The .icns is ephemeral and
# gitignored -- regenerated per build.

echo "Done. Outputs:"
ls -la necroid-mark-256.png necroid-icon-256.png necroid-icon.ico
