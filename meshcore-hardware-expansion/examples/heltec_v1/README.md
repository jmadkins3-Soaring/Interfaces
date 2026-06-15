# Worked example — Heltec WiFi LoRa 32 (V1) MeshCore variant

A complete, ready-to-drop-in MeshCore variant for a board that Meshtastic
supports (`variants/esp32/heltec_v1`) but MeshCore does not. This is the
**Tier 1** reference port the plan refers to: ESP32 + SX1276, both already
supported by MeshCore, so the entire port is a pin map plus a thin board class.

## Why this board is Tier 1

| Factor | Value | Already in MeshCore? |
|--------|-------|----------------------|
| MCU | ESP32 (classic) | ✅ `ESP32Board`, BLE/WiFi/ESP-NOW |
| Radio | Semtech SX1276 | ✅ `CustomSX1276` / `CustomSX1276Wrapper` |
| Display | SSD1306 OLED | ✅ `helpers/ui/SSD1306Display` |
| Input | single button | ✅ `helpers/ui/MomentaryButton` |

Nothing new has to be written — no driver, no transport, no UI primitive.

## Files (mirror MeshCore's `variants/<board>/` layout)

| File | Purpose |
|------|---------|
| `platformio.ini` | Board base section + one `[env:...]` per firmware role |
| `target.h` | Declares `board`, `radio_driver`, `rtc_clock`, `sensors` |
| `target.cpp` | Instantiates them; `radio_init()` + `radio_new_identity()` |
| `HeltecV1Board.h` | `HeltecV1Board : public ESP32Board` — battery read + name |

## Pin map provenance

Pins were translated from Meshtastic
`variants/esp32/heltec_v1/variant.h` plus the standard Heltec WiFi LoRa 32 V1
VSPI LoRa wiring (identical bus to MeshCore's existing `heltec_v2`):

```
NSS=18  RESET=14  DIO0=26  SCLK=5  MISO=19  MOSI=27
OLED/I2C: SDA=4  SCL=15      USER_BTN=0   STATUS_LED=25
VBAT divider=13 (x3.2)       GPS: RX=36 TX=33
```

## Build

Copy this directory into a MeshCore checkout as `variants/heltec_v1/`, then:

```bash
pio run -e Heltec_v1_companion_radio_ble    # phone-app companion over BLE
pio run -e Heltec_v1_repeater               # headless repeater
pio run -e Heltec_v1_room_server            # BBS / room server
pio run -e Heltec_v1_companion_radio_usb    # companion over USB serial
```

> Note: this example is written against MeshCore's variant ABI as captured in
> the plan (board base section `extends = esp32_base`, `P_LORA_*` pin defines,
> `RADIO_CLASS`/`WRAPPER_CLASS` selection). Verify it still matches `dev` before
> opening a PR — MeshCore asks that variant PRs target the `dev` branch.
