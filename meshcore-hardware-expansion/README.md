# meshcore-hardware-expansion

A gap analysis and development plan for bringing **MeshCore** firmware to the
hardware that **Meshtastic** already supports but MeshCore does not. Both are
open-source LoRa mesh firmwares; Meshtastic ships ~2× the board definitions.

## What's here

| Path | What it is |
|------|------------|
| [`PLAN.md`](PLAN.md) | The development plan: methodology, effort tiers, per-tier strategy, sequencing, risks |
| [`data/gap_analysis.md`](data/gap_analysis.md) | Full per-board gap table, grouped by effort tier |
| [`data/gap_analysis.csv`](data/gap_analysis.csv) / [`.json`](data/gap_analysis.json) | Machine-readable gap dataset |
| [`data/meshtastic_boards.txt`](data/meshtastic_boards.txt) / [`meshcore_variants.txt`](data/meshcore_variants.txt) | Raw upstream board lists (evidence) |
| [`templates/meshcore_variant/`](templates/meshcore_variant/) | Reusable scaffold for adding a MeshCore variant |
| [`examples/heltec_v1/`](examples/heltec_v1/) | A complete, real-pin Tier-1 port (ESP32 + SX1276) |
| [`scripts/`](scripts/) | Reproduce the board lists and regenerate the dataset |

## Headline result

From the analyzed upstream snapshot: **161** Meshtastic board variants vs **79**
MeshCore variants → **89 boards** that MeshCore is missing.

| Tier | Effort | Boards |
|------|--------|:------:|
| 1 — variant-only port (MCU + radio already supported) | 0.5–1 day each | **41** |
| 2 — variant + one bounded peripheral subsystem | 2–5 days each | **44** |
| 3 — new MCU / radio / host platform | weeks each | **4** |

The key finding: MeshCore already supports the MCUs (ESP32 family, nRF52840,
RP2040, STM32WL) and radios (SX1262/68/76, LLCC68, LR11x0, STM32WLx) behind most
of the gap, so **~46% of missing boards are pure pin-map ports.** The genuinely
hard cases are few: a 2.4 GHz **SX1280** driver, a **Linux/portduino** host
port, and the new **nRF54L15** MCU.

## Reproduce

```bash
scripts/fetch_upstream_boards.sh      # refresh board lists from upstream (blobless clones)
python3 scripts/build_gap_dataset.py  # regenerate data/gap_analysis.{csv,json,md}
```

## Scope & caveats

This is an analysis + plan + scaffolding, not a fork of MeshCore. The board
counts reflect a point-in-time snapshot of two fast-moving projects; the scripts
exist so the numbers can be refreshed. Pin maps in `examples/` are translated
from Meshtastic's `variant.h` and should be schematic-checked before flashing.
MeshCore asks that variant contributions target its `dev` branch.

---

*Soaring Heights — jmadkins3-Soaring*
