#!/usr/bin/env python3
"""
voip-sdr-gateway  —  gateway/radio_gateway.py  (pyVoIP edition)
SIP stack: github.com/tayler6000/pyVoIP  (pure Python, Python 3.12 compatible)
"""

import threading, logging, time, math, json
import numpy as np
import scipy.signal as signal
import zmq, yaml

from pyVoIP.VoIP import VoIPPhone, CallState, InvalidStateError
from pyVoIP.VoIP.VoIP import VoIPCall

log = logging.getLogger("radio_gateway")

# ── ulaw lookup table ─────────────────────────────────────────────────────────
_BIAS = 0x84
def _ulaw_dec(u):
    u = ~u & 0xFF
    t = (((u & 0x0F) << 3) + _BIAS) << ((u & 0x70) >> 4)
    return t - _BIAS if (u & 0x80) == 0 else _BIAS - t
_ULAW_TABLE = np.array([_ulaw_dec(i) for i in range(256)], dtype=np.int16)

def decode_ulaw(data: bytes) -> np.ndarray:
    return _ULAW_TABLE[np.frombuffer(data, dtype=np.uint8)].astype(np.float32) / 32768.0

# ── Resampler 8kHz → 48kHz ────────────────────────────────────────────────────
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

# ── CTCSS tone ────────────────────────────────────────────────────────────────
class CtcssGen:
    def __init__(self, freq_hz: float, sr: int = 48000, level: float = 0.15):
        self.freq_hz = freq_hz
        self.sr = sr
        self.level = level
        self._phase = 0
    def generate(self, n: int) -> np.ndarray:
        if self.freq_hz <= 0:
            return np.zeros(n, dtype=np.float32)
        t = (np.arange(n) + self._phase) / self.sr
        tone = (self.level * np.sin(2 * math.pi * self.freq_hz * t)).astype(np.float32)
        self._phase = (self._phase + n) % self.sr
        return tone

# ── DCS encoder ───────────────────────────────────────────────────────────────
class DcsEncoder:
    RATE = 134.4
    LEVEL = 0.10
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
        self.code = code
        self.sr   = sr
        self._spb = sr / self.RATE
        parity = self._PARITY.get(code, 0)
        data   = int(str(code), 8) & 0x7FF
        word   = (parity << 12) | data
        self._bits = [0,0,0] + [(word >> i) & 1 for i in range(23)]
        self._pos  = 0.0
    def generate(self, n: int) -> np.ndarray:
        if self.code <= 0:
            return np.zeros(n, dtype=np.float32)
        out = np.empty(n, dtype=np.float32)
        nb  = len(self._bits)
        for i in range(n):
            bit  = self._bits[int(self._pos) % nb]
            freq = self.RATE * (1.5 if bit else 0.75)
            out[i] = self.LEVEL * math.sin(2 * math.pi * freq * i / self.sr)
            self._pos += 1.0 / self._spb
        return out

# ── Audio pipeline ────────────────────────────────────────────────────────────
class AudioPipeline:
    def __init__(self, channel_cfg: dict, bridge):
        self.cfg  = channel_cfg
        self.bridge = bridge
        self.resampler = Resampler()
        dcs   = channel_cfg.get("dcs_code", 0)
        ctcss = channel_cfg.get("ctcss_tx_hz", 0)
        self.squelch = DcsEncoder(dcs) if dcs else CtcssGen(ctcss)
    def process(self, rtp_payload: bytes):
        pcm8k  = decode_ulaw(rtp_payload)
        pcm48k = self.resampler.process(pcm8k)
        tone   = self.squelch.generate(len(pcm48k))
        mixed  = np.clip(pcm48k + tone, -1.0, 1.0)
        self.bridge.send_audio(mixed)

# ── ZMQ bridge to GNU Radio ───────────────────────────────────────────────────
class GnuRadioBridge:
    def __init__(self, host="127.0.0.1", audio_port=5556, ctrl_port=5555):
        self._ctx   = zmq.Context()
        self._audio = self._ctx.socket(zmq.PUSH)
        self._audio.bind(f"tcp://{host}:{audio_port}")
        self._ctrl  = self._ctx.socket(zmq.PUB)
        self._ctrl.bind(f"tcp://{host}:{ctrl_port}")
        log.info("ZMQ: audio PUSH :%d  ctrl PUB :%d", audio_port, ctrl_port)
    def send_audio(self, samples: np.ndarray):
        try:
            self._audio.send(samples.tobytes(), flags=zmq.NOBLOCK)
        except zmq.Again:
            pass
    def tune(self, ch: dict):
        cmd = {"cmd":"tune","freq_hz":ch["freq_mhz"]*1e6,"mode":ch["mode"],
               "tx_gain":ch.get("tx_gain_db",40),"deviation_hz":ch.get("deviation_hz",5000),
               "ctcss_hz":ch.get("ctcss_tx_hz",0),"dcs_code":ch.get("dcs_code",0)}
        self._ctrl.send(json.dumps(cmd).encode())
        log.info("Tune → %.4f MHz  mode=%s", ch["freq_mhz"], ch["mode"])
    def stop(self):
        self._ctrl.send(json.dumps({"cmd":"stop"}).encode())
    def close(self):
        self._audio.close(); self._ctrl.close(); self._ctx.term()

# ── Per-channel SIP phone ─────────────────────────────────────────────────────
class RadioPhone:
    def __init__(self, channel_cfg: dict, pbx_cfg: dict, bridge: GnuRadioBridge):
        self.ch = channel_cfg
        self.pbx = pbx_cfg
        self.bridge = bridge
        self._phone = None
        self._lock  = threading.Lock()
        self._pipeline = None
    def start(self):
        pbx = self.pbx; ch = self.ch
        self._phone = VoIPPhone(
            server=pbx["host"], port=pbx.get("port",5060),
            username=ch["sip_user"], password=ch["sip_pass"],
            callCallback=self._on_call,
        )
        self._phone.start()
        log.info("[%s] Registered → %s  ext %s  %.4f MHz",
                 ch["sip_user"], pbx["host"], ch["extension"], ch["freq_mhz"])
    def stop(self):
        if self._phone:
            try: self._phone.stop()
            except: pass
    def _on_call(self, call: VoIPCall):
        state = call.state
        if state == CallState.RINGING:
            try: call.answer()
            except InvalidStateError: pass
        elif state == CallState.ANSWERED:
            with self._lock:
                self._pipeline = AudioPipeline(self.ch, self.bridge)
            self.bridge.tune(self.ch)
            threading.Thread(target=self._drain_rtp, args=(call,), daemon=True).start()
        elif state == CallState.ENDED:
            with self._lock: self._pipeline = None
            self.bridge.stop()
    def _drain_rtp(self, call: VoIPCall):
        while call.state == CallState.ANSWERED:
            try:
                payload = call.readAudio()
                if payload:
                    with self._lock:
                        if self._pipeline:
                            self._pipeline.process(payload)
            except Exception as e:
                log.debug("[%s] RTP: %s", self.ch["sip_user"], e); break
            time.sleep(0.002)

# ── Orchestrator ──────────────────────────────────────────────────────────────
class RadioGateway:
    def __init__(self, config_path="config/channels.yaml"):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)
        self.bridge = GnuRadioBridge()
        self.phones = []
    def start(self):
        self._start_gnuradio()
        self._register_phones()
        log.info("Gateway running — %d channel(s). Ctrl-C to stop.", len(self.phones))
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            log.info("Shutting down…")
        finally:
            self.stop()
    def stop(self):
        for p in self.phones: p.stop()
        self.bridge.close()
        if hasattr(self, "_gr_proc"): self._gr_proc.terminate()
    def _start_gnuradio(self):
        import subprocess, sys
        self._gr_proc = subprocess.Popen(
            [sys.executable, "gnuradio/sdr_modulator.py","--config","config/channels.yaml"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        time.sleep(2)
        if self._gr_proc.poll() is not None:
            raise RuntimeError("GNU Radio exited immediately")
        log.info("GNU Radio PID %d", self._gr_proc.pid)
    def _register_phones(self):
        pbx = self.cfg["pbx"]
        for ch in self.cfg["channels"]:
            p = RadioPhone(ch, pbx, self.bridge)
            p.start(); self.phones.append(p); time.sleep(0.3)

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/channels.yaml")
    RadioGateway(ap.parse_args().config).start()
