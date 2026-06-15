# Interfaces

Hardware and protocol interface projects — Soaring Heights

## Projects

### [voip-sdr-gateway](./voip-sdr-gateway/)

Bridges any standards-compliant VoIP PBX to Software Defined Radio hardware.
The gateway registers as ordinary SIP phones — one account per frequency channel.
Callers dial an extension and broadcast live audio on a mapped RF frequency with
CTCSS (PL tone) or DCS digital squelch encoding.

**Stack:** Python · pyVoIP · GNU Radio · gr-osmosdr · SoapySDR  
**Hardware:** HackRF · LimeSDR · USRP · PlutoSDR  
**PBX:** Asterisk · FreeSWITCH · FusionPBX · any SIP-compatible system

### [meshcore-hardware-expansion](./meshcore-hardware-expansion/)

Gap analysis and development plan for porting MeshCore firmware to the LoRa
hardware that Meshtastic supports but MeshCore doesn't (89 boards). Includes a
reproducible gap dataset, a reusable variant-porting template, and a worked
Tier-1 reference port.

**Stack:** PlatformIO · RadioLib · Arduino (ESP32 / nRF52 / RP2040 / STM32WL)  
**Targets:** MeshCore & Meshtastic open-source LoRa mesh firmware

---

*Soaring Heights — jmadkins3-Soaring*
