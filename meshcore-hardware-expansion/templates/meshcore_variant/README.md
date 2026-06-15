# MeshCore variant template

Drop-in scaffold for adding a new board to MeshCore. Copy the four files into
`variants/<board>/` in a MeshCore checkout, rename `BoardNameBoard.h` to
`<BoardName>Board.h`, and resolve every `TODO`.

| File | Rename to | Fill in |
|------|-----------|---------|
| `platformio.ini` | (keep) | base section, `board`, `P_LORA_*` pins, `USE_<RADIO>`, `[env:]` roles |
| `target.h` | (keep) | `<BoardName>`, `<RADIO>` |
| `target.cpp` | (keep) | `<BoardName>`, correct `Module(...)` constructor for the radio |
| `BoardNameBoard.h` | `<BoardName>Board.h` | battery pins, manufacturer name, base class |

If the board's MCU/flash layout is not a stock PlatformIO board, also add a
`boards/<board>.json` (copy one from MeshCore's `boards/` and edit `mcu`,
`variant`, flash size, `hwids`).

## Radio constructor cheat-sheet

| Radio (`USE_…`) | Wrapper | `Module(...)` args |
|-----------------|---------|--------------------|
| `SX1262` / `SX1268` / `LLCC68` / `LR1110` | `Custom<R>Wrapper` | `NSS, DIO1, RESET, BUSY` |
| `SX1276` (RFM95) | `CustomSX1276Wrapper` | `NSS, DIO0, RESET, DIO1` |
| `STM32WLx` | `CustomSTM32WLxWrapper` | internal (no SPI pins) |

See `../../examples/heltec_v1/` for a fully worked, real-pin instance of this
template (ESP32 + SX1276).

> MeshCore accepts variant PRs against its `dev` branch. Build at least the
> `repeater` and one `companion_radio_*` env before submitting.
