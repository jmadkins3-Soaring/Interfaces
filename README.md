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

---

*Soaring Heights — jmadkins3-Soaring*
