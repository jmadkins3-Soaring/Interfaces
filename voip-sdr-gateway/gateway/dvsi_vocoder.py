#!/usr/bin/env python3
"""
gateway/dvsi_vocoder.py

Serial wrapper for the DVSI USB-3000 (AMBE-3000) vocoder dongle.

The DVSI USB-3000 accepts raw 8kHz signed 16-bit PCM frames and returns
compressed AMBE+2 voice frames suitable for injection into DMR framing.

Protocol (DVSI USB-3000 Reference Manual Rev 1.1):
  All packets:  [0x61] [channel] [len_hi] [len_lo] [type] [data...]

  Encode request  (PKT_CHANNEL_FMT + PKT_SPEECH):
    → Send PCM frame as 16-bit signed samples at 8000 Hz
    ← Receive 9-byte AMBE+2 compressed frame

  Decode request (PKT_CHANNEL_FMT + PKT_CHAND):
    → Send 9-byte AMBE+2 frame
    ← Receive PCM frame (160 samples × 2 bytes)

Timing:
  - Each frame = 20ms of audio = 160 samples at 8kHz = 320 bytes raw PCM
  - Dongle latency is typically <5ms at 460800 baud
  - One thread owns the serial port exclusively — this class is NOT thread-safe
    by design; the DMR pipeline calls it from a single drain thread

Thread model:
  DmrPipeline owns one DvsiVocoder instance per active call.
  The serial port is opened on first encode() call and closed on close().
"""

import logging
import struct
import threading
import time
from typing import Optional

log = logging.getLogger("dvsi_vocoder")

# ── DVSI packet constants ─────────────────────────────────────────────────────
PKT_HEADER       = 0x61
PKT_CHANNEL      = 0x00   # channel 0 (single-channel dongle)

# Packet type bytes
PKT_PRODID       = 0x00   # product ID query
PKT_CHANNEL_FMT  = 0x15   # set channel format (PCM ↔ AMBE)
PKT_SPEECH       = 0x02   # encode: PCM in → AMBE out
PKT_CHAND        = 0x01   # decode: AMBE in → PCM out

# Format constants
FMT_PCM_8K_S16   = 0x00   # 8kHz signed 16-bit linear PCM
FMT_AMBE2PLUS    = 0x33   # AMBE+2 for DMR (9 bytes per frame)

# Frame geometry
PCM_SAMPLES_PER_FRAME  = 160     # 20ms at 8kHz
PCM_BYTES_PER_FRAME    = 320     # 160 × 2 bytes
AMBE_BYTES_PER_FRAME   = 9       # AMBE+2 frame size for DMR

DVSI_BAUD = 460800
PROBE_TIMEOUT = 0.20   # seconds — generous for cold start
ENCODE_TIMEOUT = 0.10  # seconds — tight for real-time operation


def _build_packet(pkt_type: int, data: bytes, channel: int = PKT_CHANNEL) -> bytes:
    """Assemble a DVSI framed packet."""
    length = len(data) + 1    # +1 for the type byte
    return struct.pack(">BBBBB", PKT_HEADER, channel,
                       (length >> 8) & 0xFF, length & 0xFF, pkt_type) + data


def _build_encode_packet(pcm_frame: bytes) -> bytes:
    """
    Build the PCM→AMBE encode request packet.
    pcm_frame must be exactly 320 bytes (160 × int16 samples).
    """
    if len(pcm_frame) != PCM_BYTES_PER_FRAME:
        raise ValueError(f"PCM frame must be {PCM_BYTES_PER_FRAME} bytes, got {len(pcm_frame)}")
    return _build_packet(PKT_SPEECH, pcm_frame)


def _build_decode_packet(ambe_frame: bytes) -> bytes:
    """Build the AMBE→PCM decode request packet."""
    if len(ambe_frame) != AMBE_BYTES_PER_FRAME:
        raise ValueError(f"AMBE frame must be {AMBE_BYTES_PER_FRAME} bytes, got {len(ambe_frame)}")
    return _build_packet(PKT_CHAND, ambe_frame)


class DvsiVocoder:
    """
    Manages one DVSI USB-3000 serial connection for AMBE+2 encoding/decoding.

    Usage:
        vocoder = DvsiVocoder("/dev/ttyUSB0")
        ambe_frame = vocoder.encode(pcm_bytes)   # 320 bytes in → 9 bytes out
        pcm_frame  = vocoder.decode(ambe_frame)  # 9 bytes in → 320 bytes out
        vocoder.close()
    """

    def __init__(self, port: str):
        self._port    = port
        self._ser     = None
        self._lock    = threading.Lock()
        self._open()

    # ── Public API ────────────────────────────────────────────────────────

    def encode(self, pcm_frame: bytes) -> Optional[bytes]:
        """
        Encode one 20ms PCM frame to AMBE+2.
        Returns 9-byte AMBE frame, or None on timeout/error.
        pcm_frame: 320 bytes, signed 16-bit little-endian, 8000 Hz.
        """
        with self._lock:
            try:
                self._ser.reset_input_buffer()
                self._ser.write(_build_encode_packet(pcm_frame))
                self._ser.flush()
                return self._read_ambe_response(ENCODE_TIMEOUT)
            except Exception as e:
                log.error("DVSI encode error: %s", e)
                self._try_reopen()
                return None

    def decode(self, ambe_frame: bytes) -> Optional[bytes]:
        """
        Decode one 9-byte AMBE+2 frame to 20ms PCM.
        Returns 320-byte PCM frame, or None on error.
        """
        with self._lock:
            try:
                self._ser.reset_input_buffer()
                self._ser.write(_build_decode_packet(ambe_frame))
                self._ser.flush()
                return self._read_pcm_response(ENCODE_TIMEOUT)
            except Exception as e:
                log.error("DVSI decode error: %s", e)
                self._try_reopen()
                return None

    def close(self):
        with self._lock:
            if self._ser and self._ser.is_open:
                self._ser.close()
                log.debug("DVSI serial port closed")

    # ── Private ──────────────────────────────────────────────────────────

    def _open(self):
        import serial
        self._ser = serial.Serial(
            port=self._port,
            baudrate=DVSI_BAUD,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=PROBE_TIMEOUT,
            xonxoff=False,
            rtscts=False,
        )
        self._configure_channel()
        log.info("DVSI serial open: %s @ %d baud", self._port, DVSI_BAUD)

    def _configure_channel(self):
        """
        Send PKT_CHANNEL_FMT to configure the dongle for PCM↔AMBE+2 conversion.
        This must be sent once after opening before any encode/decode requests.
        """
        fmt_data = struct.pack("BB", FMT_PCM_8K_S16, FMT_AMBE2PLUS)
        pkt = _build_packet(PKT_CHANNEL_FMT, fmt_data)
        self._ser.write(pkt)
        self._ser.flush()
        # Read and discard ACK
        self._ser.read(8)

    def _read_ambe_response(self, timeout: float) -> Optional[bytes]:
        """
        Read the response to an encode request.
        Response packet: [0x61] [ch] [len_hi] [len_lo] [PKT_SPEECH] [9 AMBE bytes]
        Total: 14 bytes
        """
        raw = self._read_bytes(5 + AMBE_BYTES_PER_FRAME, timeout)
        if raw is None or len(raw) < 5 + AMBE_BYTES_PER_FRAME:
            return None
        # Validate header
        if raw[0] != PKT_HEADER or raw[4] != PKT_SPEECH:
            log.warning("DVSI unexpected encode response header: %s", raw[:5].hex())
            return None
        return raw[5:5 + AMBE_BYTES_PER_FRAME]

    def _read_pcm_response(self, timeout: float) -> Optional[bytes]:
        """
        Read the response to a decode request.
        Response: [0x61] [ch] [len_hi] [len_lo] [PKT_CHAND] [320 PCM bytes]
        Total: 325 bytes
        """
        raw = self._read_bytes(5 + PCM_BYTES_PER_FRAME, timeout)
        if raw is None or len(raw) < 5 + PCM_BYTES_PER_FRAME:
            return None
        if raw[0] != PKT_HEADER or raw[4] != PKT_CHAND:
            log.warning("DVSI unexpected decode response header: %s", raw[:5].hex())
            return None
        return raw[5:5 + PCM_BYTES_PER_FRAME]

    def _read_bytes(self, n: int, timeout: float) -> Optional[bytes]:
        """Read exactly n bytes within timeout seconds."""
        self._ser.timeout = timeout
        buf = b""
        deadline = time.monotonic() + timeout
        while len(buf) < n and time.monotonic() < deadline:
            chunk = self._ser.read(n - len(buf))
            if chunk:
                buf += chunk
        return buf if len(buf) == n else None

    def _try_reopen(self):
        """Attempt to recover from a serial error by reopening the port."""
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
            time.sleep(0.1)
            self._open()
            log.info("DVSI serial port reopened successfully")
        except Exception as e:
            log.error("DVSI serial reopen failed: %s", e)
