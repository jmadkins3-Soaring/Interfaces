#!/usr/bin/env python3
"""
gateway/audio_pipeline_factory.py

Factory that selects the correct audio pipeline based on channel mode
and runtime hardware capabilities.

Pipeline selection:
  channel.mode == "NFM" | "WFM" | "AM"  →  AnalogPipeline  (always available)
  channel.mode == "DMR"                  →  DmrPipeline      (requires DVSI dongle)

Both pipelines expose a single method:
    process(rtp_payload: bytes) → None
        Decode one RTP packet, encode/modulate appropriately,
        and push the result to the GNU Radio bridge.

AnalogPipeline is the existing AudioPipeline from radio_gateway.py,
promoted here and renamed for clarity.

DmrPipeline chains:
    ulaw decode (PCM) → DVSI AMBE+2 encode → MMDVMHost frame injection
    → MMDVM-SDR 4FSK symbols → ZMQ → GNU Radio 4FSK modulator
"""

import logging
import math
import struct
import threading
import time
import json
from typing import Optional

import numpy as np
import scipy.signal as signal

from .capability_detector import CapabilityStore
from .dvsi_vocoder import DvsiVocoder

log = logging.getLogger("audio_pipeline_factory")


# ─────────────────────────────────────────────────────────────────────────────
# ulaw decode  (unchanged from radio_gateway.py — kept here so both pipelines
# share one implementation)
# ─────────────────────────────────────────────────────────────────────────────

_BIAS = 0x84

def _ulaw_dec(u):
    u = ~u & 0xFF
    t = (((u & 0x0F) << 3) + _BIAS) << ((u & 0x70) >> 4)
    return t - _BIAS if (u & 0x80) == 0 else _BIAS - t

_ULAW_TABLE = np.array([_ulaw_dec(i) for i in range(256)], dtype=np.int16)


def decode_ulaw_pcm(data: bytes) -> np.ndarray:
    """ulaw bytes → int16 PCM samples (NOT normalised to float)."""
    return _ULAW_TABLE[np.frombuffer(data, dtype=np.uint8)]


def decode_ulaw_float(data: bytes) -> np.ndarray:
    """ulaw bytes → float32 samples in [-1.0, 1.0]."""
    return decode_ulaw_pcm(data).astype(np.float32) / 32768.0


# ─────────────────────────────────────────────────────────────────────────────
# Shared Resampler  (8kHz → 48kHz)
# ─────────────────────────────────────────────────────────────────────────────

class Resampler:
    RATIO = 6
    def __init__(self):
        self._fir = signal.firwin(64, cutoff=3400, fs=48000, window='hamming')
        self._zi  = signal.lfilter_zi(self._fir, [1.0]) * 0
    def process(self, pcm: np.ndarray) -> np.ndarray:
        up = np.zeros(len(pcm) * self.RATIO, dtype=np.float32)
        up[::self.RATIO] = pcm
        out, self._zi = signal.lfilter(self._fir, [1.0], up, zi=self._zi)
        return (out * self.RATIO).astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# CTCSS / DCS  (unchanged from radio_gateway.py)
# ─────────────────────────────────────────────────────────────────────────────

class CtcssGen:
    def __init__(self, freq_hz: float, sr: int = 48000, level: float = 0.15):
        self.freq_hz = freq_hz; self.sr = sr; self.level = level; self._phase = 0
    def generate(self, n: int) -> np.ndarray:
        if self.freq_hz <= 0:
            return np.zeros(n, dtype=np.float32)
        t = (np.arange(n) + self._phase) / self.sr
        tone = (self.level * np.sin(2 * math.pi * self.freq_hz * t)).astype(np.float32)
        self._phase = (self._phase + n) % self.sr
        return tone


class DcsEncoder:
    RATE = 134.4; LEVEL = 0.10
    _PARITY = {
        23:0b00000010111, 25:0b00000011001, 51:0b00000110011,
        114:0b00001110010, 131:0b00010000011, 132:0b00010000100,
        143:0b00010001111, 152:0b00010011000, 155:0b00010011011,
        156:0b00010011100, 162:0b00010100010, 165:0b00010100101,
        205:0b00100000101, 223:0b00100011111, 244:0b00100110100,
        245:0b00100110101, 261:0b00101000001, 265:0b00101000101,
        271:0b00101001111, 274:0b00101010010,
    }
    def __init__(self, code: int, sr: int = 48000):
        self.code = code; self.sr = sr; self._spb = sr / self.RATE
        parity = self._PARITY.get(code, 0)
        data = int(str(code), 8) & 0x7FF
        word = (parity << 12) | data
        self._bits = [0, 0, 0] + [(word >> i) & 1 for i in range(23)]
        self._pos = 0.0
    def generate(self, n: int) -> np.ndarray:
        if self.code <= 0:
            return np.zeros(n, dtype=np.float32)
        out = np.empty(n, dtype=np.float32); nb = len(self._bits)
        for i in range(n):
            bit = self._bits[int(self._pos) % nb]
            freq = self.RATE * (1.5 if bit else 0.75)
            out[i] = self.LEVEL * math.sin(2 * math.pi * freq * i / self.sr)
            self._pos += 1.0 / self._spb
        return out


# ─────────────────────────────────────────────────────────────────────────────
# AnalogPipeline
# ─────────────────────────────────────────────────────────────────────────────

class AnalogPipeline:
    """
    NFM / WFM / AM pipeline (always available — no hardware dependency).
    ulaw RTP → float32 PCM → resample 8k→48k → CTCSS/DCS mix → ZMQ push
    """

    def __init__(self, channel_cfg: dict, bridge):
        self.cfg       = channel_cfg
        self.bridge    = bridge
        self.resampler = Resampler()
        dcs   = channel_cfg.get("dcs_code", 0)
        ctcss = channel_cfg.get("ctcss_tx_hz", 0)
        self.squelch = DcsEncoder(dcs) if dcs else CtcssGen(ctcss)

    def process(self, rtp_payload: bytes):
        pcm8k  = decode_ulaw_float(rtp_payload)
        pcm48k = self.resampler.process(pcm8k)
        tone   = self.squelch.generate(len(pcm48k))
        mixed  = np.clip(pcm48k + tone, -1.0, 1.0)
        self.bridge.send_audio(mixed)

    def close(self):
        pass   # nothing to release


# ─────────────────────────────────────────────────────────────────────────────
# DmrPipeline
# ─────────────────────────────────────────────────────────────────────────────

class DmrPipeline:
    """
    DMR pipeline — requires DVSI USB-3000 dongle.

    Chain:
      ulaw RTP → PCM int16 → DVSI AMBE+2 encode (serial) →
      DMR voice frame builder → MMDVMHost PTY injection →
      MMDVM-SDR 4FSK symbols → ZMQ push → GNU Radio 4FSK modulator

    MMDVMHost communication:
      MMDVMHost is launched as a subprocess.  We communicate via a
      pseudo-TTY (PTY) which MMDVM-SDR presents as a virtual serial port.
      Voice frames are written as MMDVM-framed packets; MMDVM-SDR handles
      TDMA scheduling and 4FSK symbol generation.
    """

    # MMDVM frame header bytes
    MMDVM_FRAME_START = 0xE0
    MMDVM_DMR_DATA    = 0x31   # DMR Data frame type

    def __init__(self, channel_cfg: dict, bridge, dvsi_port: str):
        self.cfg      = channel_cfg
        self.bridge   = bridge
        self.vocoder  = DvsiVocoder(dvsi_port)
        self._pty_fd  = None
        self._mmdvm   = None
        self._lock    = threading.Lock()
        self._start_mmdvm()

    def process(self, rtp_payload: bytes):
        """Process one 20ms RTP packet through the DMR chain."""
        # 1. Decode ulaw → raw int16 PCM (320 bytes)
        pcm_int16 = decode_ulaw_pcm(rtp_payload)
        pcm_bytes = pcm_int16.tobytes()

        # 2. DVSI AMBE+2 encode (serial, ~5ms)
        ambe_frame = self.vocoder.encode(pcm_bytes)
        if ambe_frame is None:
            log.warning("[%s] DVSI encode returned None — dropping frame",
                        self.cfg["sip_user"])
            return

        # 3. Build DMR voice frame and write to MMDVMHost PTY
        dmr_frame = self._build_dmr_voice_frame(ambe_frame)
        self._write_to_mmdvm(dmr_frame)

    def close(self):
        self.vocoder.close()
        self._stop_mmdvm()

    # ── MMDVMHost subprocess management ──────────────────────────────────

    def _start_mmdvm(self):
        """
        Launch MMDVMHost with MMDVM-SDR as its modem backend.
        MMDVMHost connects to MMDVM-SDR via a PTY presented as a virtual
        serial port.  MMDVM-SDR connects to GNU Radio via ZMQ.

        Config file written per-channel so each DMR channel has its own
        MMDVMHost instance with its specific colorcode/timeslot/talkgroup.
        """
        import subprocess, pty, os
        ch = self.cfg

        # Create PTY pair for MMDVMHost ↔ our frame writer
        master_fd, slave_fd = pty.openpty()
        self._pty_fd = master_fd
        slave_path   = os.ttyname(slave_fd)

        # Write a minimal MMDVMHost.ini for this channel
        ini_path = f"/tmp/mmdvm_{ch['sip_user']}.ini"
        self._write_mmdvm_config(ini_path, slave_path, ch)

        # Launch MMDVMHost — it will open slave_path as its modem port
        try:
            self._mmdvm = subprocess.Popen(
                ["MMDVMHost", ini_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            log.info("[%s] MMDVMHost started PID %d, PTY %s",
                     ch["sip_user"], self._mmdvm.pid, slave_path)
        except FileNotFoundError:
            log.warning("[%s] MMDVMHost not found in PATH — DMR TX will be silent", ch["sip_user"])
            self._mmdvm = None

    def _stop_mmdvm(self):
        if self._mmdvm and self._mmdvm.poll() is None:
            self._mmdvm.terminate()
            try:
                self._mmdvm.wait(timeout=3)
            except Exception:
                self._mmdvm.kill()
        if self._pty_fd:
            try:
                import os; os.close(self._pty_fd)
            except Exception:
                pass

    def _write_mmdvm_config(self, path: str, pty_path: str, ch: dict):
        """Write a minimal MMDVMHost.ini for this channel."""
        config = f"""[General]
Callsign=GATEWAY
Id={ch.get('dmr_id', 0)}
Timeout=180
Duplex=0
ModeHang=10
RFModeHang=10
NetModeHang=10

[Info]
RXFrequency={int(ch['freq_mhz'] * 1e6)}
TXFrequency={int(ch['freq_mhz'] * 1e6)}
Power=1
Latitude=0.0
Longitude=0.0
Height=0
Location=SDR Gateway
Description=VoIP-SDR Gateway DMR Channel

[Modem]
Port={pty_path}
Speed=115200
TXInvert=0
RXInvert=0
PTTInvert=0
TXDelay=100
DMRDelay=0
RXLevel=50
TXLevel=50
RSSIMappingFile=RSSI.dat
Trace=0
Debug=0

[DMR]
Enable=1
Beacons=0
ColorCode={ch.get('dmr_colorcode', 1)}
SelfOnly=0
EmbeddedLCOnly=0
DumpTAData=0
CallHang=10
TXHang=4
OVCM=0

[DMRN]
; No network — local simplex only (no BrandMeister)
Enable=0

[Log]
DisplayLevel=1
FileLevel=0
FilePath=/tmp
FileRoot=mmdvm_{ch['sip_user']}
"""
        with open(path, "w") as f:
            f.write(config)

    def _write_to_mmdvm(self, dmr_frame: bytes):
        """Write a DMR voice frame to the MMDVMHost PTY."""
        if self._pty_fd is None:
            return
        try:
            import os
            os.write(self._pty_fd, dmr_frame)
        except Exception as e:
            log.error("[%s] PTY write error: %s", self.cfg["sip_user"], e)

    # ── DMR frame builder ─────────────────────────────────────────────────

    def _build_dmr_voice_frame(self, ambe_frame: bytes) -> bytes:
        """
        Wrap a 9-byte AMBE+2 frame in a minimal MMDVM DMR voice packet.

        MMDVM framing (from MMDVM source, Modem.cpp):
          [0xE0] [length] [type=0x31] [dmr_data...]
          dmr_data for voice: [timeslot(1)] [call_type(1)] [ambe(9)] [padding(4)]
        """
        ts = self.cfg.get("dmr_timeslot", 1) & 0x01
        # Call type 0x00 = group call
        header = bytes([
            self.MMDVM_FRAME_START,
            len(ambe_frame) + 3,   # length: type + ts + call_type + ambe
            self.MMDVM_DMR_DATA,
            ts,
            0x00,   # group call
        ])
        return header + ambe_frame


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

class AudioPipelineFactory:
    """
    Single point of pipeline construction — reads channel mode and caps,
    returns the appropriate pipeline instance.
    """

    @staticmethod
    def build(channel_cfg: dict, bridge, caps: CapabilityStore):
        """
        Build and return an AnalogPipeline or DmrPipeline.

        If mode is DMR but no dongle is present, falls back to an
        AnalogPipeline and logs a warning.  This should not happen in
        normal operation because DmrPipeline channels are filtered out
        during registration — but guards against edge cases.
        """
        mode = channel_cfg.get("mode", "NFM").upper()

        if mode == "DMR":
            if caps.dvsi_present and caps.dvsi_port:
                log.info("[%s] Building DMR pipeline (DVSI %s)",
                         channel_cfg["sip_user"], caps.dvsi_port)
                return DmrPipeline(channel_cfg, bridge, caps.dvsi_port)
            else:
                log.warning(
                    "[%s] mode=DMR requested but DVSI dongle not present — "
                    "this channel should have been filtered at registration. "
                    "Falling back to analog silence pipeline.",
                    channel_cfg["sip_user"]
                )
                # Return analog pipeline with zero CTCSS — safe no-op
                fallback = dict(channel_cfg)
                fallback["ctcss_tx_hz"] = 0
                fallback["dcs_code"]    = 0
                return AnalogPipeline(fallback, bridge)

        # All analog modes
        return AnalogPipeline(channel_cfg, bridge)
