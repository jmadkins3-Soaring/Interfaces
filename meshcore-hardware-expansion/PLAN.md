# Plan: bring MeshCore to the hardware Meshtastic supports but MeshCore doesn't

## 1. Goal

Meshtastic and MeshCore are both open-source LoRa mesh firmwares, but Meshtastic
ships board definitions for far more hardware. This plan:

1. Identifies **every board Meshtastic has a firmware variant for that MeshCore
   does not** (the "gap").
2. Classifies each gap board by the actual engineering effort to add it to
   MeshCore.
3. Lays out a concrete, sequenced program of work — plus a reusable porting
   template and a fully worked reference port — to close the gap.

The machine-readable gap is in [`data/gap_analysis.csv`](data/gap_analysis.csv)
/ [`.json`](data/gap_analysis.json); the full per-board table is
[`data/gap_analysis.md`](data/gap_analysis.md). All of it is regenerated from
upstream by `scripts/`.

## 2. How the gap was derived

| Step | Source | Output |
|------|--------|--------|
| Enumerate Meshtastic boards | `meshtastic/firmware` → `variants/<mcu>/<board>/` (blobless clone) | 161 real board variants |
| Enumerate MeshCore boards | `ripplebiz/MeshCore` → `variants/<board>/` | 79 variants |
| Map MeshCore→Meshtastic names | hand-curated equivalence (naming differs, e.g. MeshCore `thinknode_m1` = Meshtastic `ELECROW-ThinkNode-M1`) | 72 Meshtastic boards already covered |
| Anti-join + classify | spot-checked MCU/radio from each Meshtastic `variant.h` / `platformio.ini` | **89 gap boards** |

Reproduce with `scripts/fetch_upstream_boards.sh` then
`scripts/build_gap_dataset.py`.

> Counts reflect the upstream snapshot taken for this analysis. Both projects
> add boards frequently — re-run the scripts to refresh. A board counts as
> "covered" if MeshCore has the same hardware under a different name, even if a
> minor sub-variant differs.

## 3. What MeshCore can already do (this is what sets the effort tiers)

The effort to port a board is governed almost entirely by whether MeshCore
already supports its **MCU** and **radio**. From the MeshCore tree:

- **MCU families supported:** ESP32 / ESP32-S3 / ESP32-C3 / ESP32-C6
  (`ESP32Board`, BLE + WiFi + ESP-NOW), **nRF52840** (`NRF52Board`, BLE),
  **RP2040** (USB/serial companion), **STM32WL** (`arch/stm32`).
- **Radios supported** (RadioLib wrappers in `src/helpers/radiolib/`):
  **SX1262, SX1268, SX1276, LLCC68, LR11x0, STM32WLx**.
- **Firmware roles** (the `examples/`): companion radio (BLE/USB/WiFi),
  simple repeater, room server, sensor, KISS modem.
- **Design constraints** (from `CONTRIBUTING.md`): embedded C++, *no dynamic
  allocation outside setup*, minimal layering. MeshCore is deliberately
  lighter-weight than Meshtastic and has **no rich GUI stack** — its UI is an
  optional SSD1306/OLED status display plus one button.

**Consequence:** for the large majority of gap boards the silicon is already
supported, so the port is a **pin map + a thin board class** — no new drivers.
The hard cases are exactly the ones that need *new silicon support* or a
*UI/peripheral subsystem MeshCore doesn't have yet*.

## 4. The gap at a glance

**89 boards**, by effort tier:

| Tier | Meaning | Boards | Typical effort each |
|------|---------|:------:|---------------------|
| **1 — variant-only** | MCU + radio already supported; just pins, board class, `[env:]` roles | **41** | 0.5–1 day |
| **2 — variant + peripheral** | Adds a display/keyboard/Ethernet/PMIC/no-BLE constraint that needs new (but bounded) code | **44** | 2–5 days |
| **3 — new platform/radio** | New MCU family or radio, or a whole new host platform | **4** | 1–6 weeks |

By MCU family: ESP32-S3 ×27, ESP32 ×22, nRF52840 ×19, RP2040 ×7, ESP32-C3 ×5,
STM32WL ×3, RP2350 ×2, Linux ×2, ESP32-S2 ×1, nRF54L15 ×1.

The full board-by-board table is [`data/gap_analysis.md`](data/gap_analysis.md).

## 5. Per-tier strategy

### Tier 1 — variant-only ports (41 boards)

Pure pin-map work on already-supported silicon. The mechanical recipe is the
same every time and is captured in
[`templates/meshcore_variant/`](templates/meshcore_variant/), with a complete
real instance in [`examples/heltec_v1/`](examples/heltec_v1/).

Recipe per board:

1. `cp -r templates/meshcore_variant variants/<board>` (in a MeshCore checkout).
2. Translate pins from Meshtastic's `variants/<mcu>/<board>/variant.h`
   (`LORA_*`, `I2C_*`, `BUTTON_PIN`, `BATTERY_PIN`, `ADC_MULTIPLIER`) into the
   `P_LORA_*` / `PIN_*` defines.
3. Select `USE_<radio>` + `RADIO_CLASS`/`WRAPPER_CLASS` and the matching
   `Module(...)` constructor (SX126x uses `BUSY`; SX127x/RFM95 uses `DIO0`).
4. Rename the board class, set battery pins + manufacturer name.
5. Add a `boards/<board>.json` only if the MCU/flash layout isn't a stock PIO board.
6. Build `repeater` + one `companion_radio_*` env; flash; verify advert + range.

Sequence Tier 1 by clustering identical MCU+radio+role so each port reuses the
previous one's board class:

- **Batch 1a — nRF52840 + SX1262 (11):** `canaryone`, `MS24SF1`,
  `MakePython_nRF52840_oled`, `TWC_mesh_v4`, `dls_Minimesh_Lite`, `icarus`,
  `meshlink`, `monteops_hw1`, `rak4631_nomadstar_meteor_pro`,
  `gat562_mesh_trial_tracker`, `ELECROW-ThinkNode-M4`. (ThinkNode/GAT562/RAK4631
  are sub-variants of boards MeshCore already has — start here, lowest risk.)
- **Batch 1b — ESP32 classic + SX127x/SX1262 (12):** `heltec_v1` (done — see
  example), `heltec_wsl_v2.1`, `heltec_wireless_bridge`, `nano-g1`,
  `nano-g1-explorer`, `station-g1`, `tlora_v1`, `tlora_v1_3`, `tlora_v2`,
  `tlora_v3_3_0_tcxo`, `rak11200`, `hackerboxes_esp32_io`.
- **Batch 1c — ESP32-C3/S3 (10):** `ai-c3`, `heltec_esp32c3`,
  `hackerboxes_esp32c3_oled`, `m5stack-stamp-c3`, `EBYTE_ESP32-S3`,
  `bpi_picow_esp32_s3`, `esp32-s3-pico`, `link32_s3_v1`, `nugget_s3_lora`,
  `rak3312`.
- **Batch 1d — RP2040 + STM32WL (8):** `challenger_2040_lora`,
  `feather_rp2040_rfm95`, `senselora_rp2040`, `ec_catsniffer`, `rpipico`,
  `rpipico-slowclock` (USB/serial companion — no BLE); `CDEBYTE_E77-MBL`,
  `russell` (STM32WL, sub-variants of `wio-e5`/`rak3172`).

### Tier 2 — variant + bounded peripheral work (44 boards)

Each needs **one** reusable subsystem that MeshCore doesn't ship yet. Build the
subsystem **once**, then the boards that need it collapse to Tier-1 effort.
Prioritise by how many boards each unlock:

| Build this once | Unlocks (examples) | Approx boards |
|-----------------|--------------------|:-------------:|
| **E-paper UI driver** (GxEPD2-style, behind `DISPLAY_CLASS`) | `MakePython_nRF52840_eink`, `crowpanel-esp32s3-5-epaper`, `mini-epaper-s3`, `t5s3_epaper`, `tlora_t3s3_epaper`, `t-echo-plus`, `m5stack_coreink` | ~8 |
| **Color TFT + (optional) touch UI** | `t-watch-s3`, `m5stack_cores3`, `rak_wismeshtap(+v2)`, `mesh-tab`, `elecrow_panel`, `dreamcatcher` | ~8 |
| **Keyboard/QWERTY input + list UI** | `tlora-pager`, `t-deck-pro(+v1_1)`, `nibble_esp32`, `nibble_rp2040`, `picomputer-s3`, `m5stack_cardputer_adv`, `tracksenger`, `chatter2`, `hackaday-communicator` | ~11 |
| **W5500 Ethernet transport** | `rak4631_eth_gw`, `ELECROW-ThinkNode-G3` | ~2 |
| **No-BLE companion profile** (USB/WiFi only) | `nugget_s2_lora` (ESP32-S2), the RP2040/RP2350 cluster | ~4 |
| **Sensor/PMIC + solar power mgmt** | `heltec_capsule_sensor_v3`, `heltec_sensor_hub`, `heltec_hru_3601`, `milesight_gs301`, `rak2560` | ~5 |
| **RP2350 toolchain validation** | `rpipico2`, `rpipico2w` | 2 |

Notes:
- Many Tier-2 boards can ship a **headless** repeater/room-server build at Tier-1
  effort *first* (skip the display), then gain the UI when the subsystem lands.
  Recommend doing exactly that to deliver value early.
- The rich-UI boards are where MeshCore's minimalist philosophy matters most —
  coordinate scope with maintainers (their `CONTRIBUTING.md` asks for an issue
  before architecturally significant work) before building a large UI layer.

### Tier 3 — new platform / radio bring-up (4 boards)

| Board | What's actually needed | Est. |
|-------|------------------------|------|
| `betafpv_2400_tx_micro` | **SX1280 (2.4 GHz) driver** — RadioLib has `SX128x`; add `CustomSX1280` + wrapper mirroring the existing `CustomSX1262`. Then 2.4 GHz unlocks beyond this one board. | 1–2 wk |
| `portduino` | **Linux/Raspberry Pi host port** — SPI/GPIO LoRa via RadioLib's Linux layer, a serial/BLE companion interface, file-backed identity store. High value: turns any Pi into a MeshCore gateway. | 3–6 wk |
| `portduino-buildroot` | Buildroot packaging on top of the Linux port. | +1 wk |
| `nrf54l15dk` | **nRF54L15 MCU bring-up** — new SoftDevice/SDK; no Arduino-nRF52 core support yet. Largest unknown; recommend **defer** until upstream Arduino/RadioLib support matures. | 4–6 wk+ |

## 6. Recommended sequence

1. **Phase 0 — tooling (done here):** gap dataset + porting template + one
   worked Tier-1 port (`heltec_v1`). This repo is that phase.
2. **Phase 1 — Tier 1 batches 1a→1d (41 boards):** highest coverage per unit
   effort; each batch reuses the prior board class. ~6–8 weeks of focused work.
3. **Phase 2 — Tier 2 by subsystem:** build e-paper → keyboard → TFT/touch →
   Ethernet → no-BLE profile → sensor/PMIC, shipping headless builds first.
4. **Phase 3 — Tier 3:** SX1280 driver, then the Linux/portduino host; defer
   nRF54L15.

Throughout: open each variant PR against MeshCore's **`dev`** branch, one board
(or one tight cluster) per PR, with a photo of the device showing a successful
advert/connection as the acceptance check.

## 7. Risks & open questions

- **Naming drift / double-counting:** the gap is computed from a hand-curated
  name map; a few "gap" boards are minor sub-variants of covered ones and may be
  even cheaper than Tier 1. Re-run the scripts before committing to a board.
- **Pin-map accuracy:** Meshtastic `variant.h` is the source of truth, but some
  pins (SPI bus) come from the PlatformIO board default — confirm against the
  schematic before flashing hardware you can't easily recover.
- **MeshCore scope fit:** rich-UI / phone-class boards (`wiphone`, `unphone`,
  `mesh-tab`) may be a poor fit for MeshCore's minimalist design. Confirm intent
  with maintainers before investing in a UI subsystem.
- **Hardware access:** every port needs the physical board to validate RF.
  Prioritise boards the team actually owns.
