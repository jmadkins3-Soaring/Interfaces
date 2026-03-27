# VoIP-SDR Gateway — UI Design Brief

This document gives a UI designer/developer everything they need to build
a management and monitoring console for the `voip-sdr-gateway` project.
It covers data sources, field definitions, runtime state, events, and
suggested screen layout.

---

## Project overview

The gateway is a Python application that registers as SIP "phones" on a
VoIP PBX. When a caller dials an extension, the gateway answers and
transmits the caller's audio over RF using a Software Defined Radio
(Pluto+ SDR). Channels can be analog (NFM/AM/WFM) or digital DMR — DMR
is only available when a DVSI USB-3000 hardware dongle is plugged in.

The UI needs to let an operator:
- See the current state of every channel at a glance
- Know immediately whether hardware (SDR + dongle) is healthy
- Add, edit, and delete channels without touching YAML files
- Monitor live call activity and system events
- Adjust key RF parameters on active channels

---

## Repository

```
github.com/jmadkins3-Soaring/Interfaces
branch: feature/dmr-pluto-optimizations
path:   voip-sdr-gateway/
```

---

## Data sources the UI will read/write

| Source | Format | How accessed |
|--------|--------|--------------|
| `config/channels.yaml` | YAML | Read/write on disk (or via REST API wrapper) |
| Gateway runtime state | Python objects | ZMQ PUB socket on port 5555 (control channel) |
| Gateway log stream | Python logging | Log file or stdout — parse for events |
| GNU Radio status | ZMQ | Same control socket — add a `status` command |

The simplest API approach: add a lightweight FastAPI or Flask server to
`radio_gateway.py` that exposes a REST + WebSocket interface. The UI
talks to that. The WebSocket pushes live events; REST handles CRUD.

---

## Section 1 — Hardware status panel

Displayed prominently at the top of every screen. Updates on startup and
on hardware change events.

### 1a. SDR hardware

| Field | Source | Type | Notes |
|-------|--------|------|-------|
| `sdr.driver` | channels.yaml | string | `plutosdr` \| `hackrf` \| `limesdr` \| `usrp` |
| `sdr.device_args` | channels.yaml | string | e.g. `ip:192.168.2.1` — Ethernet URI for Pluto+ |
| `sdr.sample_rate` | channels.yaml | integer | Hz — `520833` is Pluto+ minimum valid rate |
| `sdr.audio_sample_rate` | channels.yaml | integer | Hz — always `48000` |
| `sdr.buffer_size` | channels.yaml | integer | samples — `32768` ≈ 63ms latency |
| `sdr_connected` | runtime | bool | True if GNU Radio flowgraph started without error |
| `gnuradio_pid` | runtime | integer | Process ID of GNU Radio subprocess |
| `tx_active` | runtime | bool | True when a call is active and TX is live |

Display suggestion: green/red indicator dot + driver name + URI.
Show "TX LIVE" badge in amber when transmitting.

### 1b. DVSI vocoder dongle

| Field | Source | Type | Notes |
|-------|--------|------|-------|
| `dvsi_present` | runtime (CapabilityStore) | bool | Detected at startup via USB probe |
| `dvsi_port` | runtime (CapabilityStore) | string \| null | e.g. `/dev/ttyUSB0` — null when absent |
| `dvsi_version` | runtime (CapabilityStore) | string \| null | e.g. `AMBE-3000R2` — from chip product ID |
| `dmr_capable` | runtime (CapabilityStore) | bool | `dvsi_present == True` — derived field |

Display suggestion: "DMR CAPABLE" green badge when dongle present;
"ANALOG ONLY" gray badge when absent. Show port and chip version in
small text underneath.

### 1c. PBX connection

| Field | Source | Type | Notes |
|-------|--------|------|-------|
| `pbx.host` | channels.yaml | string | IP or hostname of Asterisk/FreeSWITCH |
| `pbx.port` | channels.yaml | integer | SIP port, default `5060` |
| `pbx.transport` | channels.yaml | string | `udp` \| `tcp` \| `tls` |
| `pbx_registered_count` | runtime | integer | How many SIP accounts successfully registered |
| `pbx_skipped_count` | runtime | integer | Channels skipped (no dongle / out of range) |

---

## Section 2 — Channel list (main table)

One row per channel. This is the central view.

### Channel row fields

| Field | Source | Type | Display label | Notes |
|-------|--------|------|---------------|-------|
| `extension` | channels.yaml | string | Ext | Dialable number e.g. `201` |
| `display_name` | channels.yaml | string | Name | Human-readable e.g. `VHF 146.52` |
| `freq_mhz` | channels.yaml | float | Frequency | Show as `146.520 MHz` |
| `mode` | channels.yaml | string | Mode | `NFM` \| `WFM` \| `AM` \| `DMR` — color-coded |
| `sip_user` | channels.yaml | string | SIP ID | e.g. `radio-vhf-call` |
| `sip_registration_status` | runtime | enum | Status | `registered` \| `failed` \| `skipped` |
| `call_active` | runtime | bool | Active | Live call in progress |
| `call_duration_sec` | runtime | integer | Duration | Seconds since call answered |
| `tx_gain_db` | channels.yaml | integer | TX Gain | 0–89 dB (Pluto+ attenuation inverse) |
| `license_note` | channels.yaml | string | License | Informational — show as tooltip |
| `ctcss_tx_hz` | channels.yaml | float | CTCSS TX | `0` = none; show as `100.0 Hz` or `—` |
| `ctcss_rx_hz` | channels.yaml | float | CTCSS RX | Same |
| `dcs_code` | channels.yaml | integer | DCS | `0` = none; show as `DCS 156` or `—` |
| `deviation_hz` | channels.yaml | integer | Deviation | `5000` Hz = ±5kHz NFM; hidden for DMR |
| `dmr_id` | channels.yaml | integer | DMR ID | DMR channels only |
| `dmr_colorcode` | channels.yaml | integer | Color Code | 1–15; DMR channels only |
| `dmr_timeslot` | channels.yaml | integer | Timeslot | 1 or 2; DMR channels only |
| `dmr_talkgroup` | channels.yaml | integer | Talk Group | DMR channels only |
| `enabled` | channels.yaml | bool | — | Not yet in schema but should be added — lets operator disable a channel without deleting it |

### Status badge values and colors

| Value | Color | Meaning |
|-------|-------|---------|
| `registered` | Green | SIP account live on PBX |
| `active` | Amber | Call in progress — TX live |
| `failed` | Red | SIP registration failed |
| `skipped_no_dongle` | Gray | DMR channel, dongle absent |
| `skipped_out_of_range` | Gray | Frequency outside SDR range |
| `disabled` | Gray | Manually disabled |

### Mode badge colors

| Mode | Color |
|------|-------|
| NFM | Blue |
| WFM | Teal |
| AM | Purple |
| DMR | Orange |

---

## Section 3 — Channel editor (add / edit form)

Form fields for creating or editing a channel. Show/hide DMR-specific
fields based on selected mode.

### Always-visible fields

| Field | Input type | Validation |
|-------|-----------|------------|
| `display_name` | Text | Required, max 40 chars |
| `extension` | Text | Required, numeric string, unique |
| `freq_mhz` | Number | 70.0–6000.0 (Pluto+ hacked range) |
| `mode` | Select | NFM \| WFM \| AM \| DMR |
| `tx_gain_db` | Slider or number | 0–89 integer |
| `sip_user` | Text | Required, unique, alphanumeric + hyphens |
| `sip_pass` | Password | Required |
| `license_note` | Text | Optional, informational |

### Analog-mode fields (hide when mode = DMR)

| Field | Input type | Validation | Notes |
|-------|-----------|------------|-------|
| `deviation_hz` | Select or number | 2500 \| 5000 \| 75000 | 2500 = narrow, 5000 = standard NFM, 75000 = WFM |
| `ctcss_tx_hz` | Select | 0 or standard EIA list | 0 = disabled. Show as `None` when 0 |
| `ctcss_rx_hz` | Select | 0 or standard EIA list | Usually matches ctcss_tx_hz |
| `dcs_code` | Select or number | 0 or valid 3-digit octal | 0 = disabled. Mutually exclusive with CTCSS |

**Standard CTCSS frequencies (EIA):**
67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8,
97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8,
136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3,
173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5,
206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1

### DMR-mode fields (show only when mode = DMR)

| Field | Input type | Validation | Notes |
|-------|-----------|------------|-------|
| `dmr_id` | Number | 1–16776415 | Your DMR radio ID (from radioid.net) |
| `dmr_colorcode` | Select | 1–15 | Must match repeater/hotspot |
| `dmr_timeslot` | Select | 1 or 2 | Simplex = TS1 |
| `dmr_talkgroup` | Number | Any positive integer | e.g. 91 = Worldwide, 9 = Local |

### Validation rules

- `freq_mhz` < 70 or > 6000 → error: "Outside Pluto+ frequency range"
- `freq_mhz` < 325 and > 70 → warning: "Extended range — requires AD9364 firmware hack"
- `mode == DMR` and `dvsi_present == false` → warning: "DMR requires DVSI USB-3000 dongle — channel will be skipped at startup"
- `dcs_code > 0` and `ctcss_tx_hz > 0` → error: "Cannot use both DCS and CTCSS on the same channel"
- `extension` already in use → error: "Extension conflict"
- `sip_user` already in use → error: "SIP user conflict"

---

## Section 4 — System configuration panel

Global settings that apply to all channels.

### PBX settings

| Field | Input type | Notes |
|-------|-----------|-------|
| `pbx.host` | Text | IP or hostname |
| `pbx.port` | Number | Default 5060 |
| `pbx.transport` | Select | `udp` \| `tcp` \| `tls` |

### SDR hardware settings

| Field | Input type | Notes |
|-------|-----------|-------|
| `sdr.driver` | Select | `plutosdr` \| `hackrf` \| `limesdr` \| `usrp` |
| `sdr.device_args` | Text | URI string e.g. `ip:192.168.2.1` |
| `sdr.sample_rate` | Number | Minimum 520833 for Pluto+ |
| `sdr.buffer_size` | Number | Default 32768 |

---

## Section 5 — Live activity feed

Real-time event stream. Shown as a scrolling log panel or as toast
notifications for high-priority events.

### Event types and fields

| Event | Trigger | Key fields to display |
|-------|---------|----------------------|
| `gateway_start` | Gateway process starts | timestamp |
| `hardware_probe` | Startup hardware detection | dvsi_present, dvsi_port, dvsi_version |
| `sip_registered` | SIP account registers | sip_user, extension, freq_mhz, mode |
| `sip_skipped` | Channel skipped at startup | sip_user, extension, reason |
| `call_incoming` | Caller dials an extension | extension, display_name, caller_id |
| `call_answered` | Gateway answers the call | extension, freq_mhz, mode |
| `call_ended` | Call terminates | extension, duration_sec |
| `tx_armed` | RF transmit starts | freq_mhz, mode, tx_gain_db |
| `tx_stopped` | RF transmit stops | — |
| `dvsi_error` | Vocoder encode/decode fails | error message |
| `dvsi_reconnect` | Dongle serial port reopened | dvsi_port |
| `gnuradio_start` | GNU Radio subprocess starts | pid |
| `gnuradio_exit` | GNU Radio exits unexpectedly | exit_code |
| `tune` | Frequency/mode change | freq_mhz, mode, ctcss_hz, dcs_code |

### Severity levels

| Level | Color | Events |
|-------|-------|--------|
| INFO | Gray | registered, answered, ended, tx events |
| WARNING | Amber | skipped, dvsi_reconnect, gnuradio unexpected exit |
| ERROR | Red | dvsi_error, gnuradio crash, SIP registration failure |

---

## Section 6 — Active call detail panel

Shown when a call is in progress on any channel. Can be a sidebar
drawer or modal.

| Field | Type | Notes |
|-------|------|-------|
| Channel display name | string | e.g. `VHF 146.52` |
| Extension | string | e.g. `201` |
| Caller ID | string | From SIP INVITE — may be unavailable |
| Frequency | float | MHz with 3 decimal places |
| Mode | string | `NFM` / `DMR` etc |
| CTCSS TX | float | Hz or `None` |
| DCS code | integer | or `None` |
| TX gain | integer | dB |
| Call duration | timer | Live counter since `call_answered` |
| Audio RMS level | float | Optional — real-time VU meter if audio metering added |

Action buttons on this panel:
- `End call` — hangs up the SIP call (gateway sends BYE)
- `Adjust TX gain` — inline slider, sends tune command to GNU Radio

---

## Section 7 — Channel quick-reference card

Printable / shareable card showing extension → frequency mapping.
Useful for giving to users of the phone system who need to know what
to dial for which radio channel.

| Column | Content |
|--------|---------|
| Dial | Extension number |
| Name | display_name |
| Frequency | freq_mhz in MHz |
| Mode | NFM / DMR etc |
| Squelch | CTCSS Hz, DCS code, or `Open` |
| License | license_note |

---

## Suggested screen layout

```
┌─────────────────────────────────────────────────────────────────┐
│  HARDWARE STATUS                                                │
│  [● SDR: PlutoSDR  ip:192.168.2.1  ✓ Connected]               │
│  [● DVSI: PRESENT  /dev/ttyUSB0  AMBE-3000R2  ✓ DMR CAPABLE] │
│  [● PBX: 192.168.1.10:5060  8/8 channels registered]          │
├─────────────────────────────────────────────────────────────────┤
│  CHANNELS                              [+ Add Channel]          │
│                                                                 │
│  Ext  Name               Freq       Mode  Status   Squelch     │
│  201  VHF 146.52         146.520    NFM   ● ACTIVE  100.0 Hz   │
│  202  GMRS Ch1 462.56    462.5625   NFM   ● reg     141.3 Hz   │
│  203  GMRS Ch6 462.61    462.6125   NFM   ● reg     167.9 Hz   │
│  204  MURS Ch1 151.82    151.820    NFM   ● reg     Open       │
│  206  UHF 446.00         446.000    NFM   ● reg     88.5 Hz    │
│  207  Custom 155.34 DCS  155.340    NFM   ● reg     DCS 156    │
│  301  DMR UHF Simplex    446.500    DMR   ● reg     CC1 TS1    │
│  302  DMR Worldwide TG91 446.075    DMR   ● reg     CC1 TS2    │
├─────────────────────────────────────────────────────────────────┤
│  LIVE ACTIVITY                                                  │
│  14:32:01 [INFO]  201 VHF 146.52 — call answered (ext 101)    │
│  14:32:01 [INFO]  TX armed → 146.520 MHz  NFM  CTCSS 100.0Hz  │
│  14:31:45 [INFO]  Gateway running — 6 analog, 2 DMR channels   │
│  14:31:43 [INFO]  DVSI USB-3000 confirmed: /dev/ttyUSB0        │
│  14:31:42 [INFO]  Hardware probe complete                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## REST API endpoints to implement (backend additions needed)

The gateway process does not currently expose an HTTP API.
These endpoints need to be added to `radio_gateway.py` (FastAPI
recommended — already in the Python ecosystem, async-friendly).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/status` | Full system status — hardware, channels, active calls |
| `GET` | `/api/channels` | All channels from channels.yaml |
| `POST` | `/api/channels` | Add a new channel |
| `PUT` | `/api/channels/{sip_user}` | Edit an existing channel |
| `DELETE` | `/api/channels/{sip_user}` | Remove a channel |
| `POST` | `/api/channels/{sip_user}/enable` | Enable a disabled channel |
| `POST` | `/api/channels/{sip_user}/disable` | Disable without deleting |
| `GET` | `/api/hardware` | SDR + DVSI dongle status |
| `POST` | `/api/gateway/reload` | Re-read channels.yaml and re-register |
| `POST` | `/api/gateway/restart` | Full restart of gateway process |
| `WS` | `/ws/events` | WebSocket — streams events as JSON lines |

### `/api/status` response shape

```json
{
  "gateway_version": "2.0",
  "uptime_sec": 3642,
  "hardware": {
    "sdr_driver": "plutosdr",
    "sdr_uri": "ip:192.168.2.1",
    "sdr_connected": true,
    "gnuradio_pid": 12345,
    "tx_active": true,
    "dvsi_present": true,
    "dvsi_port": "/dev/ttyUSB0",
    "dvsi_version": "AMBE-3000R2",
    "dmr_capable": true
  },
  "pbx": {
    "host": "192.168.1.10",
    "port": 5060,
    "registered_count": 8,
    "skipped_count": 0
  },
  "channels": [
    {
      "sip_user": "radio-vhf-call",
      "extension": "201",
      "display_name": "VHF 146.52",
      "freq_mhz": 146.520,
      "mode": "NFM",
      "status": "active",
      "call_active": true,
      "call_duration_sec": 47,
      "tx_gain_db": 40,
      "ctcss_tx_hz": 100.0,
      "ctcss_rx_hz": 100.0,
      "dcs_code": 0,
      "deviation_hz": 5000,
      "license_note": "Ham — Technician or higher"
    }
  ]
}
```

### WebSocket event shape

```json
{
  "ts": "2026-03-27T14:32:01Z",
  "level": "INFO",
  "event": "call_answered",
  "sip_user": "radio-vhf-call",
  "extension": "201",
  "display_name": "VHF 146.52",
  "freq_mhz": 146.520,
  "mode": "NFM",
  "caller_id": "101"
}
```

---

## Technology suggestions

The UI can be built with any frontend stack. Given the project context
(runs on a home server, single operator, no auth needed initially):

**Lightweight option:** Single HTML file with Alpine.js + Tailwind CDN.
Drop it in the repo, serve it from FastAPI's static files. No build step.

**Full-featured option:** React + Vite. Better for the channel editor
form logic and real-time WebSocket handling.

**Key libraries to consider:**
- WebSocket: native browser WebSocket API is sufficient
- Charts/meters: Chart.js for any signal level visualization
- Forms: React Hook Form handles the complex conditional fields well
- Tables: TanStack Table for sortable/filterable channel list

---

## Files to read before starting

```
voip-sdr-gateway/
├── config/channels.yaml              ← all config fields + types
├── gateway/capability_detector.py    ← CapabilityStore fields
├── gateway/radio_gateway.py          ← GnuRadioBridge, RadioPhone, RadioGateway
├── gateway/audio_pipeline_factory.py ← AnalogPipeline, DmrPipeline
├── gateway/dvsi_vocoder.py           ← DVSI serial protocol
└── docs/README.md                    ← setup and install context
```
