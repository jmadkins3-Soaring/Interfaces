# VoIP-SDR Radio Gateway

Bridges any standards-compliant VoIP PBX to Software Defined Radio hardware.
Callers dial an extension number and broadcast live audio on a mapped RF frequency,
including CTCSS (PL tone) or DCS (digital squelch) encoding.

**The PBX is unmodified.** The gateway registers as ordinary SIP phones — one account
per frequency channel. Asterisk (or FreeSWITCH, FusionPBX, 3CX, etc.) routes calls
to the gateway the same way it routes calls to any desk phone.

---

## Architecture

```
SIP phones → PBX → [Gateway registers as SIP phone] → GNU Radio → SDR hardware → RF
```

1. **PBX** — any SIP-compatible system. Needs a one-line dial rule for 2XX extensions.
2. **Gateway** (`gateway/radio_gateway.py`) — Python app using pjsua2. Registers one
   SIP account per channel, receives RTP audio, encodes CTCSS/DCS, pushes to GNU Radio.
3. **GNU Radio** (`gnuradio/sdr_modulator.py`) — modulates audio (NFM/WFM/AM) and drives
   the SDR hardware via gr-osmosdr / SoapySDR.
4. **Config** (`config/channels.yaml`) — the only file you edit to add/change channels.

---

## Hardware Requirements

| Role | Options |
|------|---------|
| TX SDR | HackRF One, LimeSDR, USRP B210, ADALM-PlutoSDR |
| RX-only (monitor) | RTL-SDR (RTL2832U-based dongles) |
| Antenna | Appropriate for your frequency band |
| PBX host | Any Linux box, Raspberry Pi 4+, VM |

---

## Installation

### 1. System packages (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y \
    gnuradio \
    gr-osmosdr \
    python3-pjsua2 \
    python3-yaml \
    python3-scipy \
    python3-numpy \
    python3-zmq \
    hackrf            # or limesdr-tools / uhd-host for other hardware
```

### 2. Python dependencies

```bash
pip install pyyaml numpy scipy pyzmq
# pjsua2 is usually installed via the system package above
# If not: pip install pjsua2  (requires libpjproject-dev)
```

### 3. SDR hardware

For HackRF:
```bash
sudo apt install hackrf
hackrf_info          # verify hardware is detected
```

For LimeSDR:
```bash
sudo apt install limesuite
LimeUtil --find
```

For USRP:
```bash
sudo apt install uhd-host
uhd_find_devices
```

### 4. Clone project repos (informational — this IS the project)

```
PBX   : github.com/asterisk/asterisk          (Asterisk PBX)
PBX   : github.com/signalwire/freeswitch       (FreeSWITCH alternative)
SIP   : https://www.pjsip.org/                 (pjsua2 - SIP stack)
SDR   : github.com/gnuradio/gnuradio           (GNU Radio)
SDR   : github.com/osmocom/gr-osmosdr          (gr-osmosdr hardware abstraction)
SDR   : github.com/pothosware/SoapySDR         (SoapySDR device layer)
```

---

## Configuration

Edit `config/channels.yaml`.

**Adding a channel:**
```yaml
- sip_user: "radio-repeater-out"
  sip_pass: "mypassword"
  extension: "208"
  display_name: "Repeater Output 147.105"
  freq_mhz: 147.105
  mode: NFM
  deviation_hz: 5000
  ctcss_tx_hz: 123.0     # PL tone to access repeater
  ctcss_rx_hz: 123.0
  dcs_code: 0
  tx_gain_db: 40
  license_note: "Ham - requires General or higher for repeater"
```

**Modes:**
| Mode | Use |
|------|-----|
| `NFM` | VHF/UHF ham, GMRS, MURS, public safety |
| `WFM` | Wideband FM (broadcast-style) |
| `AM` | HF/CB radio, aviation |
| `USB` | HF upper sideband (planned) |
| `LSB` | HF lower sideband (planned) |

**CTCSS vs DCS:**
- Set `ctcss_tx_hz` to a standard PL frequency (67.0–254.1 Hz) to encode a tone
- Set `dcs_code` to a 3-digit octal DCS code (e.g., 156) — takes priority over CTCSS
- Set both to 0 for carrier squelch / no subaudible encoding

---

## Running

### Start Asterisk (or your PBX) first

```bash
sudo systemctl start asterisk
```

Apply the minimal dialplan:
```bash
sudo cp asterisk_config/extensions.conf /etc/asterisk/extensions.conf
sudo asterisk -rx "dialplan reload"
```

### Start the gateway

```bash
cd voip-sdr-gateway
python3 gateway/radio_gateway.py --config config/channels.yaml
```

You should see each channel register:
```
[radio-vhf-call] Registration: 200 OK (code 200)
[radio-gmrs-1]   Registration: 200 OK (code 200)
...
GNU Radio flowgraph running (PID 12345)
Gateway running — 7 channels registered. Ctrl-C to stop.
```

### Make a call

From any SIP phone registered to the PBX:
```
Dial 201 → connects to gateway → transmits on 146.520 MHz with CTCSS 100.0 Hz
Dial 202 → connects to gateway → transmits on 462.5625 MHz with CTCSS 141.3 Hz
```

---

## Legal Notice

Transmitting on radio frequencies is regulated. Ensure you hold the appropriate
license for the frequencies you configure:

| Frequency Band | License |
|---------------|---------|
| Ham (146, 446 MHz, etc.) | FCC Amateur Radio (Technician minimum) |
| GMRS (462–467 MHz) | FCC GMRS License (Part 95E) |
| MURS (151–154 MHz) | No license required (USA), power/equipment limits apply |
| CB (27 MHz) | No license required (USA), 4W AM / 12W SSB max |
| Other | Verify with local authority |

Never transmit on emergency, aviation, military, or public safety frequencies.

---

## Project Structure

```
voip-sdr-gateway/
├── config/
│   └── channels.yaml          ← Edit this to add/change frequencies
├── gateway/
│   └── radio_gateway.py       ← SIP UA + audio pipeline
├── gnuradio/
│   └── sdr_modulator.py       ← GNU Radio modulator + SDR hardware
├── asterisk_config/
│   └── extensions.conf        ← Minimal PBX dialplan (6 lines)
└── docs/
    └── README.md
```
