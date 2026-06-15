#!/usr/bin/env bash
#
# fetch_upstream_boards.sh
# ------------------------
# Reproduce data/meshtastic_boards.txt and data/meshcore_variants.txt from the
# upstream firmware repositories. Uses blobless shallow clones so it is fast and
# does not pull file contents.
#
# Re-run this whenever you want to refresh the gap against upstream, then run
# scripts/build_gap_dataset.py to regenerate the analysis.
#
set -euo pipefail

DATA_DIR="$(cd "$(dirname "$0")/../data" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

MESHTASTIC_REPO="https://github.com/meshtastic/firmware.git"
MESHCORE_REPO="https://github.com/ripplebiz/MeshCore.git"

echo "Cloning Meshtastic firmware (blobless)..."
git clone --depth 1 --filter=blob:none "$MESHTASTIC_REPO" "$WORK/mtfw" >/dev/null 2>&1

echo "Cloning MeshCore (blobless)..."
git clone --depth 1 --filter=blob:none "$MESHCORE_REPO" "$WORK/mcfw" >/dev/null 2>&1

# Meshtastic variants are nested one level under the MCU family:
#   variants/<mcu>/<board>/
# Drop non-board scaffolding directories.
find "$WORK/mtfw/variants" -mindepth 2 -maxdepth 2 -type d -printf '%f\n' \
  | grep -viE '^(diy|cpp_overrides|feather_diy|station-common)$' \
  | sort -u > "$DATA_DIR/meshtastic_boards.txt"

# MeshCore variants are flat under variants/.
ls "$WORK/mcfw/variants" | sort -u > "$DATA_DIR/meshcore_variants.txt"

echo "Meshtastic board variants: $(wc -l < "$DATA_DIR/meshtastic_boards.txt")"
echo "MeshCore variants:         $(wc -l < "$DATA_DIR/meshcore_variants.txt")"
echo "Wrote board lists to $DATA_DIR"
