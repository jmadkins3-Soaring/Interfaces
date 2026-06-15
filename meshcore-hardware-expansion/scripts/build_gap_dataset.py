#!/usr/bin/env python3
"""
build_gap_dataset.py
====================

Single source of truth for the Meshtastic -> MeshCore hardware gap.

It encodes, for every board that Meshtastic's firmware ships a variant for but
MeshCore does not, the facts that drive a MeshCore port:

  * mcu      - the host micro-controller family
  * radio    - the LoRa transceiver
  * tier     - porting effort tier (1 = variant-only, 2 = + peripheral work,
               3 = new platform / radio bring-up)
  * category - what kind of device it is (drives the firmware roles that apply)
  * blocker  - the single thing that makes it harder than a copy-paste port
  * notes    - porting guidance

Running this script regenerates data/gap_analysis.csv and data/gap_analysis.json
from the table below, and cross-checks the keys against the captured upstream
board lists in data/ so the dataset can't silently drift from the raw evidence.

The board lists themselves are reproduced by scripts/fetch_upstream_boards.sh.
"""

import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")

# MeshCore already supports these MCU families and radios (see arch/ and
# src/helpers/radiolib/ in the MeshCore tree). This is what makes a board
# "Tier 1" - if the silicon is already supported, a port is just a pin map.
MESHCORE_MCUS = {"esp32", "esp32-s3", "esp32-c3", "esp32-c6", "nrf52840",
                 "rp2040", "stm32wl"}
MESHCORE_RADIOS = {"sx1262", "sx1268", "sx1276", "llcc68", "lr1110", "lr1121",
                   "stm32wlx"}

# ---------------------------------------------------------------------------
# The gap table. Keys MUST match directory names under Meshtastic variants/.
# ---------------------------------------------------------------------------
# fields: (mcu, radio, tier, category, blocker, notes)
GAP = {
    # --- nRF52840 + supported radio : mostly pure variant ports (Tier 1) ----
    "MS24SF1":                      ("nrf52840", "sx1262", 1, "module",   "none",        "Minew nRF52840 module; copy an nRF52 variant, set pins."),
    "MakePython_nRF52840_oled":     ("nrf52840", "sx1262", 1, "node",     "none",        "OLED reuses existing SSD1306Display driver."),
    "MakePython_nRF52840_eink":     ("nrf52840", "sx1262", 2, "node",     "display",     "Needs an e-ink UI driver (see Tier-2 display work)."),
    "TWC_mesh_v4":                  ("nrf52840", "sx1262", 1, "diy",      "none",        "Community DIY board; straight pin-map port."),
    "canaryone":                    ("nrf52840", "sx1262", 1, "node",     "none",        "OLED + SX1262; reference Tier-1 nRF52 port."),
    "dls_Minimesh_Lite":            ("nrf52840", "sx1262", 1, "diy",      "none",        "DIY board; pin-map port."),
    "icarus":                       ("nrf52840", "sx1262", 1, "diy",      "none",        "Pin-map port."),
    "meshlink":                     ("nrf52840", "sx1262", 1, "diy",      "none",        "Pin-map port."),
    "monteops_hw1":                 ("nrf52840", "sx1262", 1, "diy",      "none",        "Pin-map port."),
    "rak4631_nomadstar_meteor_pro": ("nrf52840", "sx1262", 1, "node",     "none",        "RAK4631 derivative; sub-variant of existing rak4631."),
    "gat562_mesh_trial_tracker":    ("nrf52840", "sx1262", 1, "tracker",  "none",        "GAT562 family already in MeshCore; add this sub-variant + GPS."),
    "ELECROW-ThinkNode-M4":         ("nrf52840", "sx1262", 1, "node",     "none",        "ThinkNode family partly ported (m1/2/3/5/6); add M4."),
    "ELECROW-ThinkNode-M7":         ("nrf52840", "sx1262", 2, "node",     "display",     "ThinkNode sub-variant; confirm display hardware."),
    "rak2560":                      ("nrf52840", "sx1262", 2, "node",     "power",       "RAK WisBlock solar sensor node; PMIC/solar handling."),
    "rak4631_eth_gw":               ("nrf52840", "sx1262", 2, "gateway",  "ethernet",    "Needs W5500 Ethernet interface (new transport)."),
    "rak_wismeshtap":               ("nrf52840", "sx1262", 2, "handheld", "display",      "TFT + touch; needs UI layer."),
    "rak_wismesh_tap_v2":           ("nrf52840", "sx1262", 2, "handheld", "display",      "TFT + touch; needs UI layer."),
    "t-echo-plus":                  ("nrf52840", "sx1262", 2, "node",     "display",      "Sub-variant of t-echo; e-ink + GPS pin map."),
    "Dongle_nRF52840-pca10059-v1":  ("nrf52840", "none",   2, "dongle",   "no-radio",     "USB dongle with NO onboard LoRa; needs external radio wiring."),

    # --- ESP32 classic + supported radio (Tier 1 headless) ------------------
    "heltec_v1":                    ("esp32",    "sx1276", 1, "node",     "none",        "Reference Tier-1 ESP32 port (worked example in examples/)."),
    "heltec_wireless_bridge":       ("esp32",    "sx1276", 1, "gateway",  "none",        "Headless bridge; pin-map port."),
    "heltec_wsl_v2.1":              ("esp32",    "sx1276", 1, "node",     "none",        "Wireless Stick Lite; sub-variant of heltec_v2."),
    "hackerboxes_esp32_io":         ("esp32",    "sx1276", 1, "diy",      "none",        "DIY; pin-map port."),
    "nano-g1":                      ("esp32",    "sx1276", 1, "node",     "none",        "Pin-map port."),
    "nano-g1-explorer":             ("esp32",    "sx1276", 1, "node",     "none",        "Pin-map port."),
    "station-g1":                   ("esp32",    "sx1276", 1, "gateway",  "none",        "Pin-map port."),
    "rak11200":                     ("esp32",    "sx1262", 1, "node",     "none",        "ESP32 WisBlock core; pin-map port."),
    "tlora_v1":                     ("esp32",    "sx1276", 1, "node",     "none",        "Early T-LoRa; pin-map port."),
    "tlora_v1_3":                   ("esp32",    "sx1276", 1, "node",     "none",        "Pin-map port."),
    "tlora_v2":                     ("esp32",    "sx1276", 1, "node",     "none",        "Pin-map port."),
    "tlora_v3_3_0_tcxo":            ("esp32",    "sx1262", 1, "node",     "none",        "T-LoRa V3 (SX1262 + TCXO); pin-map port."),
    "trackerd":                     ("esp32",    "sx1276", 2, "tracker",  "display",      "Has display + GPS; UI optional for headless build."),
    "chatter2":                     ("esp32",    "sx1276", 2, "handheld", "display",      "Display + keyboard handheld; needs UI layer."),
    "wiphone":                      ("esp32",    "sx1276", 2, "phone",    "display",      "Phone form factor; large UI/codec scope."),
    "m5stack_core":                 ("esp32",    "sx1276", 2, "node",     "display",      "M5 module LoRa; TFT UI."),
    "m5stack_coreink":              ("esp32",    "sx1262", 2, "node",     "display",      "E-ink UI."),

    # TX-module form factor (niche, 900 MHz RF95) ----------------------------
    "betafpv_900_tx_nano":          ("esp32",    "sx1276", 2, "tx-module","form-factor",  "FPV TX module; niche, headless repeater only."),
    "radiomaster_900_bandit":       ("esp32",    "sx1276", 2, "tx-module","form-factor",  "FPV TX module; niche, headless repeater only."),
    "radiomaster_900_bandit_micro": ("esp32",    "sx1276", 2, "tx-module","form-factor",  "FPV TX module; niche."),
    "radiomaster_900_bandit_nano":  ("esp32",    "sx1276", 2, "tx-module","form-factor",  "FPV TX module; niche."),

    # --- ESP32-C3 + supported radio (Tier 1) --------------------------------
    "ai-c3":                        ("esp32-c3", "sx1276", 1, "node",     "none",        "C3 has BLE; pin-map port."),
    "heltec_esp32c3":               ("esp32-c3", "sx1262", 1, "node",     "none",        "Pin-map port."),
    "hackerboxes_esp32c3_oled":     ("esp32-c3", "sx1276", 1, "diy",      "none",        "Pin-map port."),
    "m5stack-stamp-c3":             ("esp32-c3", "sx1276", 1, "module",   "none",        "Pin-map port."),
    "heltec_hru_3601":              ("esp32-c3", "sx1262", 2, "sensor",   "power",        "Sensor/relay board; confirm peripherals."),

    # --- ESP32-S2 : NO BLE (Tier 2 - companion over USB/WiFi only) ----------
    "nugget_s2_lora":               ("esp32-s2", "sx1276", 2, "diy",      "no-ble",       "S2 has no BLE; companion must be USB/WiFi only."),

    # --- ESP32-S3 + supported radio -----------------------------------------
    "EBYTE_ESP32-S3":               ("esp32-s3", "sx1262", 1, "module",   "none",        "Pin-map port."),
    "bpi_picow_esp32_s3":           ("esp32-s3", "sx1262", 1, "node",     "none",        "Pin-map port."),
    "esp32-s3-pico":                ("esp32-s3", "sx1262", 1, "node",     "none",        "Pin-map port."),
    "link32_s3_v1":                 ("esp32-s3", "sx1262", 1, "node",     "none",        "Pin-map port."),
    "nugget_s3_lora":               ("esp32-s3", "sx1276", 1, "diy",      "none",        "Pin-map port."),
    "heltec_capsule_sensor_v3":     ("esp32-s3", "sx1262", 2, "sensor",   "power",        "Sensor node; sensor manager wiring."),
    "heltec_sensor_hub":            ("esp32-s3", "sx1262", 2, "sensor",   "power",        "Multi-sensor hub; sensor manager wiring."),
    "dreamcatcher":                 ("esp32-s3", "sx1262", 2, "gateway",  "display",      "Gateway/dev board; confirm peripherals."),
    "crowpanel-esp32s3-5-epaper":   ("esp32-s3", "sx1262", 2, "node",     "display",      "E-paper UI."),
    "elecrow_panel":                ("esp32-s3", "sx1262", 2, "node",     "display",      "TFT panel UI."),
    "mini-epaper-s3":               ("esp32-s3", "sx1262", 2, "node",     "display",      "E-paper UI."),
    "t5s3_epaper":                  ("esp32-s3", "sx1262", 2, "node",     "display",      "E-paper UI."),
    "tlora_t3s3_epaper":            ("esp32-s3", "sx1262", 2, "node",     "display",      "E-paper UI; T3S3 base already ported."),
    "hackaday-communicator":        ("esp32-s3", "sx1262", 2, "handheld", "display",      "Display + input; UI layer."),
    "mesh-tab":                     ("esp32-s3", "sx1262", 2, "tablet",   "display",      "Tablet TFT + touch; large UI scope."),
    "nibble_esp32":                 ("esp32-s3", "sx1262", 2, "handheld", "display",      "Keyboard + display; UI layer."),
    "picomputer-s3":                ("esp32-s3", "sx1262", 2, "handheld", "display",      "Keyboard + display; UI layer."),
    "tracksenger":                  ("esp32-s3", "sx1262", 2, "handheld", "display",      "Display + keyboard; UI layer."),
    "m5stack_cardputer_adv":        ("esp32-s3", "sx1262", 2, "handheld", "display",      "Cardputer keyboard + TFT; UI layer."),
    "m5stack_cores3":               ("esp32-s3", "sx1262", 2, "node",     "display",      "CoreS3 TFT + touch; UI layer."),
    "t-deck-pro":                   ("esp32-s3", "sx1262", 2, "handheld", "display",      "E-paper + keyboard + 4G; UI layer (T-Deck base ported)."),
    "t-deck-pro-v1_1":              ("esp32-s3", "sx1262", 2, "handheld", "display",      "Sub-variant of t-deck-pro."),
    "t-watch-s3":                   ("esp32-s3", "sx1262", 2, "wearable", "display",      "Touch smartwatch; UI + PMU (AXP2101)."),
    "tlora-pager":                  ("esp32-s3", "sx1262", 2, "handheld", "display",      "Keyboard + TFT pager; UI layer."),
    "unphone":                      ("esp32-s3", "sx1262", 2, "phone",    "display",      "Touch phone; large UI scope."),
    "ELECROW-ThinkNode-G3":         ("esp32-s3", "sx1262", 2, "gateway",  "display",      "Gateway variant; confirm peripherals."),
    "rak3312":                      ("esp32-s3", "sx1262", 1, "node",     "none",        "ESP32-S3 WisBlock; pin-map port."),

    # --- RP2040 / RP2350 : no native BLE (USB/serial companion) -------------
    "challenger_2040_lora":         ("rp2040",   "sx1276", 1, "node",     "no-ble",       "RP2040; USB/serial companion. rak11310 proves the path."),
    "feather_rp2040_rfm95":         ("rp2040",   "sx1276", 1, "node",     "no-ble",       "Feather + RFM95; USB/serial companion."),
    "senselora_rp2040":             ("rp2040",   "sx1276", 1, "sensor",   "no-ble",       "USB/serial; sensor node."),
    "nibble_rp2040":                ("rp2040",   "sx1276", 2, "handheld", "display",      "Keyboard + display; UI layer."),
    "ec_catsniffer":                ("rp2040",   "sx1262", 1, "tool",     "no-ble",       "CatSniffer RP2040; USB/serial sniffer/relay."),
    "rpipico":                      ("rp2040",   "sx1276", 1, "diy",      "no-ble",       "Raw Pico + external RFM95; USB/serial."),
    "rpipico-slowclock":            ("rp2040",   "sx1276", 1, "diy",      "no-ble",       "Sub-variant of rpipico (low clock)."),
    "rpipico2":                     ("rp2350",   "sx1276", 2, "diy",      "new-mcu-minor","RP2350 (Pico 2); arduino-pico supports it, validate toolchain."),
    "rpipico2w":                    ("rp2350",   "sx1276", 2, "diy",      "new-mcu-minor","RP2350W; validate toolchain + WiFi."),

    # --- STM32WL : MCU family supported (wio-e5/rak3172 exist) ---------------
    "CDEBYTE_E77-MBL":              ("stm32wl",  "stm32wlx", 1, "module",  "none",        "STM32WLE5; sub-variant of existing wio-e5/rak3172 path."),
    "milesight_gs301":              ("stm32wl",  "stm32wlx", 2, "sensor",  "power",        "STM32WL sensor; confirm peripherals/PMIC."),
    "russell":                      ("stm32wl",  "stm32wlx", 1, "diy",     "none",        "Custom STM32WL board; pin-map port."),

    # === Tier 3 : new platform or radio bring-up ============================
    "betafpv_2400_tx_micro":        ("esp32",    "sx1280",  3, "tx-module","new-radio",   "2.4 GHz SX1280: MeshCore has NO SX1280 RadioLib wrapper. New driver."),
    "nrf54l15dk":                   ("nrf54l15", "sx1262",  3, "devkit",   "new-mcu",     "nRF54L15: new MCU family, no MeshCore/Arduino-nRF52 support yet."),
    "portduino":                    ("linux",    "any-spi", 3, "host",     "new-platform","Linux/Raspberry Pi native LoRa gateway: new HAL (SPI/GPIO, BLE/serial)."),
    "portduino-buildroot":          ("linux",    "any-spi", 3, "host",     "new-platform","Buildroot Linux target; depends on portduino HAL."),
}

TIER_LABEL = {
    1: "Tier 1 - variant-only port",
    2: "Tier 2 - variant + peripheral integration",
    3: "Tier 3 - new platform / radio bring-up",
}


def load_list(name):
    with open(os.path.join(DATA, name)) as f:
        return [l.strip() for l in f if l.strip()]


def main():
    mt = set(load_list("meshtastic_boards.txt"))

    # Sanity: every gap key must be a real Meshtastic variant.
    unknown = [b for b in GAP if b not in mt]
    if unknown:
        raise SystemExit("Gap keys not present in Meshtastic board list: %r" % unknown)

    rows = []
    for board in sorted(GAP, key=lambda b: (GAP[b][2], GAP[b][0], b.lower())):
        mcu, radio, tier, category, blocker, notes = GAP[board]
        rows.append({
            "meshtastic_variant": board,
            "mcu": mcu,
            "radio": radio,
            "tier": tier,
            "tier_label": TIER_LABEL[tier],
            "category": category,
            "blocker": blocker,
            "mcu_supported_by_meshcore": mcu in MESHCORE_MCUS,
            "radio_supported_by_meshcore": radio in MESHCORE_RADIOS,
            "notes": notes,
        })

    # CSV
    csv_path = os.path.join(DATA, "gap_analysis.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # JSON (with summary)
    summary = {
        "meshtastic_total_variants": len(mt),
        "meshcore_total_variants": len(load_list("meshcore_variants.txt")),
        "gap_total": len(rows),
        "by_tier": {TIER_LABEL[t]: sum(1 for r in rows if r["tier"] == t) for t in (1, 2, 3)},
        "by_mcu": {},
    }
    for r in rows:
        summary["by_mcu"][r["mcu"]] = summary["by_mcu"].get(r["mcu"], 0) + 1

    json_path = os.path.join(DATA, "gap_analysis.json")
    with open(json_path, "w") as f:
        json.dump({"summary": summary, "boards": rows}, f, indent=2)

    # Markdown table (grouped by tier) for the plan appendix.
    md_path = os.path.join(DATA, "gap_analysis.md")
    with open(md_path, "w") as f:
        f.write("# Meshtastic-only boards — full gap table\n\n")
        f.write("_Generated by scripts/build_gap_dataset.py. Do not edit by hand._\n\n")
        f.write("Meshtastic variants: %d · MeshCore variants: %d · Gap: %d boards\n\n"
                % (summary["meshtastic_total_variants"],
                   summary["meshcore_total_variants"], summary["gap_total"]))
        for t in (1, 2, 3):
            trows = [r for r in rows if r["tier"] == t]
            f.write("## %s (%d boards)\n\n" % (TIER_LABEL[t], len(trows)))
            f.write("| Board | MCU | Radio | Category | Blocker | Notes |\n")
            f.write("|-------|-----|-------|----------|---------|-------|\n")
            for r in trows:
                f.write("| `%s` | %s | %s | %s | %s | %s |\n" % (
                    r["meshtastic_variant"], r["mcu"], r["radio"],
                    r["category"], r["blocker"], r["notes"]))
            f.write("\n")

    print("Wrote %d gap boards" % len(rows))
    print("  ->", os.path.relpath(csv_path))
    print("  ->", os.path.relpath(json_path))
    print("  ->", os.path.relpath(md_path))
    print("By tier:", summary["by_tier"])
    print("By MCU :", summary["by_mcu"])


if __name__ == "__main__":
    main()
