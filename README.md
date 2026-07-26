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

### [darkweb-threat-intel](./darkweb-threat-intel/)

Standalone CLI for third-party / supply-chain dark web due diligence. Give it a
company (domain, email domain, brand name) and it searches Intelligence X across
the dark web, breach dumps, stealer logs, and paste sites, scores every hit, and
produces ranked console / JSON / HTML reports. Supports a YAML watchlist to scan
many vendors in one run.

**Stack:** Python · requests · Intelligence X (intelx.io) API

---

*Soaring Heights — jmadkins3-Soaring*
