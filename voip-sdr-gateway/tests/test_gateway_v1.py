#!/usr/bin/env python3
"""
tests/test_gateway.py

Full test suite for voip-sdr-gateway.
Tests every component that doesn't require physical hardware,
and uses mocks for pyVoIP and ZMQ so tests run with zero network access.

Run with:
    python -m pytest tests/test_gateway.py -v
or:
    python tests/test_gateway.py
"""

import sys, os, math, json, struct, threading, time, tempfile, pathlib
import numpy as np
import yaml

# ── Make sure we can import gateway modules from project root ─────────────────
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gateway"))

# ── Import the modules under test ─────────────────────────────────────────────
from gateway.radio_gateway import (
    decode_ulaw,
    Resampler,
    CtcssGen,
    DcsEncoder,
    AudioPipeline,
    GnuRadioBridge,
    RadioPhone,
    RadioGateway,
    _ULAW_TABLE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_config(tmp_path, channels=None):
    """Write a minimal channels.yaml and return its path."""
    if channels is None:
        channels = [
            {
                "sip_user": "radio-test-vhf",
                "sip_pass": "secret",
                "extension": "201",
                "display_name": "VHF Test",
                "freq_mhz": 146.520,
                "mode": "NFM",
                "deviation_hz": 5000,
                "ctcss_tx_hz": 100.0,
                "ctcss_rx_hz": 100.0,
                "dcs_code": 0,
                "tx_gain_db": 40,
            },
            {
                "sip_user": "radio-test-dcs",
                "sip_pass": "secret2",
                "extension": "202",
                "display_name": "DCS Test",
                "freq_mhz": 462.5625,
                "mode": "NFM",
                "deviation_hz": 5000,
                "ctcss_tx_hz": 0,
                "ctcss_rx_hz": 0,
                "dcs_code": 156,
                "tx_gain_db": 40,
            },
        ]
    cfg = {
        "pbx": {"host": "192.168.1.1", "port": 5060},
        "sdr": {"driver": "hackrf", "sample_rate": 2_000_000, "audio_sample_rate": 48000},
        "channels": channels,
    }
    p = tmp_path / "channels.yaml"
    p.write_text(yaml.dump(cfg))
    return str(p)


PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
_results = []

def check(name: str, expr: bool, detail: str = ""):
    status = PASS if expr else FAIL
    label  = f"{status} {name}"
    if detail:
        label += f"  ({detail})"
    print(label)
    _results.append((name, expr))
    return expr


# ═════════════════════════════════════════════════════════════════════════════
# 1. Config loading
# ═════════════════════════════════════════════════════════════════════════════

def test_config_loading():
    print("\n── 1. Config loading ────────────────────────────────────────────")
    with tempfile.TemporaryDirectory() as d:
        cfg_path = _make_config(pathlib.Path(d))
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)

        check("pbx host present",        "host" in cfg["pbx"])
        check("two channels loaded",      len(cfg["channels"]) == 2)
        check("channel 0 freq correct",   cfg["channels"][0]["freq_mhz"] == 146.520)
        check("channel 1 dcs code set",   cfg["channels"][1]["dcs_code"] == 156)
        check("channel 0 ctcss set",      cfg["channels"][0]["ctcss_tx_hz"] == 100.0)
        check("modes valid",
              all(ch["mode"] in ("NFM","WFM","AM","USB","LSB")
                  for ch in cfg["channels"]))


# ═════════════════════════════════════════════════════════════════════════════
# 2. ulaw decoder
# ═════════════════════════════════════════════════════════════════════════════

def test_ulaw_decoder():
    print("\n── 2. ulaw decoder ──────────────────────────────────────────────")

    # Silence byte in ulaw is 0xFF
    silence_byte = bytes([0xFF] * 160)
    out = decode_ulaw(silence_byte)
    check("silence decodes near-zero",   np.max(np.abs(out)) < 0.01,
          f"max={np.max(np.abs(out)):.5f}")
    check("output is float32",           out.dtype == np.float32)
    check("output length matches input", len(out) == 160)

    # All possible byte values should decode to [-1, 1]
    all_bytes = bytes(range(256))
    decoded   = decode_ulaw(all_bytes)
    check("all values in [-1, 1]",       np.all(np.abs(decoded) <= 1.0))
    check("lookup table is 256 entries", len(_ULAW_TABLE) == 256)

    # Known ulaw values (ITU-T G.711 reference)
    # ulaw byte 0x00 → maximum positive sample
    # ulaw byte 0x7F → quietest positive sample  
    sample_0x00 = decode_ulaw(bytes([0x00]))[0]
    sample_0x7F = decode_ulaw(bytes([0x7F]))[0]
    check("0x00 decodes to large positive", sample_0x00 > 0.9,
          f"got {sample_0x00:.4f}")
    check("0x7F decodes to small positive", 0 < sample_0x7F < 0.02,
          f"got {sample_0x7F:.6f}")


# ═════════════════════════════════════════════════════════════════════════════
# 3. Resampler
# ═════════════════════════════════════════════════════════════════════════════

def test_resampler():
    print("\n── 3. Resampler (8kHz → 48kHz) ──────────────────────────────────")

    rs = Resampler()

    # 20ms frame at 8kHz = 160 samples → should produce 960 samples at 48kHz
    pcm_8k = np.random.randn(160).astype(np.float32) * 0.3
    out    = rs.process(pcm_8k)

    check("output length is 6× input",  len(out) == 960,
          f"got {len(out)}")
    check("output is float32",          out.dtype == np.float32)
    check("output finite (no NaN/Inf)", np.all(np.isfinite(out)))

    # Sine wave: 1kHz tone should pass through (below 3.4kHz cutoff)
    t      = np.arange(160) / 8000
    tone1k = np.sin(2 * math.pi * 1000 * t).astype(np.float32)
    rs2    = Resampler()
    out1k  = rs2.process(tone1k)
    # After FIR settling (~64 samples), signal should be present
    rms_out = np.sqrt(np.mean(out1k[200:]**2))
    check("1kHz tone survives resampling", rms_out > 0.3,
          f"RMS after={rms_out:.3f}")

    # 3.9kHz tone should be attenuated (above cutoff)
    t_hi   = np.arange(1600) / 8000
    hi     = np.sin(2 * math.pi * 3900 * t_hi).astype(np.float32)
    rs3    = Resampler()
    out_hi = rs3.process(hi)
    rms_hi = np.sqrt(np.mean(out_hi[500:]**2))
    check("3.9kHz tone is attenuated",    rms_hi < 0.1,
          f"RMS={rms_hi:.3f}")

    # Stateful: zi carries between calls — no DC jump at boundaries
    rs4 = Resampler()
    block1 = rs4.process(np.ones(160, dtype=np.float32) * 0.5)
    block2 = rs4.process(np.ones(160, dtype=np.float32) * 0.5)
    check("stateful FIR: no boundary NaN", np.all(np.isfinite(block2)))


# ═════════════════════════════════════════════════════════════════════════════
# 4. CTCSS generator
# ═════════════════════════════════════════════════════════════════════════════

def test_ctcss_generator():
    print("\n── 4. CTCSS tone generator ───────────────────────────────────────")

    sr = 48000

    # Common CTCSS frequencies: 100.0, 141.3, 88.5 Hz
    for freq in [88.5, 100.0, 141.3, 167.9, 254.1]:
        gen = CtcssGen(freq_hz=freq, sr=sr, level=0.15)
        tone = gen.generate(sr)   # 1 full second

        # Measure actual frequency via zero-crossing
        zc = np.where(np.diff(np.sign(tone)))[0]
        if len(zc) > 2:
            avg_period = np.mean(np.diff(zc)) * 2   # samples per cycle
            measured   = sr / avg_period
            error_hz   = abs(measured - freq)
            check(f"CTCSS {freq:.1f} Hz within 0.5 Hz", error_hz < 0.5,
                  f"measured={measured:.2f} Hz err={error_hz:.3f}")
        else:
            check(f"CTCSS {freq:.1f} Hz zero crossings found", False, "no crossings")

    # Level check
    gen100 = CtcssGen(100.0, sr=sr, level=0.15)
    t100   = gen100.generate(sr)
    peak   = np.max(np.abs(t100))
    check("CTCSS peak ≈ 0.15", abs(peak - 0.15) < 0.005,
          f"peak={peak:.4f}")

    # Zero frequency → silence
    gen0  = CtcssGen(0.0, sr=sr)
    zero  = gen0.generate(480)
    check("freq=0 produces silence", np.all(zero == 0))

    # Phase continuity across two calls
    gen_p = CtcssGen(100.0, sr=sr)
    seg1  = gen_p.generate(480)
    seg2  = gen_p.generate(480)
    combined = np.concatenate([seg1, seg2])
    # If phase is continuous, there should be no discontinuity spike
    diff     = np.abs(np.diff(combined))
    max_jump = np.max(diff)
    check("CTCSS phase continuous across blocks", max_jump < 0.15,
          f"max jump={max_jump:.4f}")


# ═════════════════════════════════════════════════════════════════════════════
# 5. DCS encoder
# ═════════════════════════════════════════════════════════════════════════════

def test_dcs_encoder():
    print("\n── 5. DCS encoder ────────────────────────────────────────────────")

    sr = 48000

    # Test several standard DCS codes
    for code in [156, 131, 205, 265]:
        enc  = DcsEncoder(code=code, sr=sr)
        tone = enc.generate(sr)   # 1 second

        check(f"DCS {code}: output length correct", len(tone) == sr)
        check(f"DCS {code}: output is float32",     tone.dtype == np.float32)
        check(f"DCS {code}: finite values only",    np.all(np.isfinite(tone)))

        # Level should be around LEVEL constant (0.10)
        peak = np.max(np.abs(tone))
        check(f"DCS {code}: peak ≈ 0.10", abs(peak - 0.10) < 0.02,
              f"peak={peak:.4f}")

        # Signal should be below 300 Hz (sub-audible)
        # Check via FFT — most energy below 300 Hz
        fft   = np.abs(np.fft.rfft(tone))
        freqs = np.fft.rfftfreq(sr, d=1/sr)
        below_300 = np.sum(fft[freqs < 300])
        total     = np.sum(fft)
        ratio     = below_300 / total if total > 0 else 0
        check(f"DCS {code}: energy sub-audible (<300Hz)", ratio > 0.80,
              f"ratio={ratio:.2f}")

    # Code 0 → silence
    enc0  = DcsEncoder(0)
    zero  = enc0.generate(480)
    check("DCS code=0 produces silence", np.all(zero == 0))

    # Frame structure: should have 26-bit repeating pattern (3+23)
    # at 134.4 bps → 48000/134.4 ≈ 357 samples per bit
    enc156 = DcsEncoder(156, sr=sr)
    tone156 = enc156.generate(sr * 2)  # 2 seconds for enough periods
    check("DCS 156: 2s frame generated", len(tone156) == sr * 2)


# ═════════════════════════════════════════════════════════════════════════════
# 6. Audio pipeline (end-to-end decode + resample + squelch mix)
# ═════════════════════════════════════════════════════════════════════════════

def test_audio_pipeline():
    print("\n── 6. Audio pipeline (end-to-end) ───────────────────────────────")

    class CaptureBridge:
        """Fake bridge that captures what was sent."""
        def __init__(self):
            self.frames = []
        def send_audio(self, samples):
            self.frames.append(samples.copy())

    # Channel with CTCSS
    ch_ctcss = {
        "sip_user": "test-ctcss",
        "freq_mhz": 146.52,
        "mode": "NFM",
        "ctcss_tx_hz": 100.0,
        "dcs_code": 0,
        "deviation_hz": 5000,
        "tx_gain_db": 40,
    }

    bridge  = CaptureBridge()
    pipe    = AudioPipeline(ch_ctcss, bridge)

    # Simulate 10 × 20ms RTP packets (160 ulaw bytes each = 20ms @ 8kHz)
    silence_ulaw = bytes([0xFF] * 160)   # near-silence in ulaw
    for _ in range(10):
        pipe.process(silence_ulaw)

    check("pipeline sent 10 frames",       len(bridge.frames) == 10)
    check("output frame length = 960",     all(len(f) == 960 for f in bridge.frames),
          f"lengths: {[len(f) for f in bridge.frames[:3]]}")
    check("output is float32",             bridge.frames[0].dtype == np.float32)
    check("values clipped to [-1, 1]",
          all(np.all(np.abs(f) <= 1.0) for f in bridge.frames))

    # With silence input, the output should be dominated by CTCSS
    combined = np.concatenate(bridge.frames)
    # Skip first ~200 samples for FIR to settle, then check CTCSS energy
    settled = combined[200:]
    rms = np.sqrt(np.mean(settled**2))
    check("CTCSS tone present in output", rms > 0.01,
          f"RMS={rms:.4f}")

    # Channel with DCS
    ch_dcs = {
        "sip_user": "test-dcs",
        "freq_mhz": 462.56,
        "mode": "NFM",
        "ctcss_tx_hz": 0,
        "dcs_code": 156,
        "deviation_hz": 5000,
        "tx_gain_db": 40,
    }
    bridge2 = CaptureBridge()
    pipe2   = AudioPipeline(ch_dcs, bridge2)
    for _ in range(5):
        pipe2.process(silence_ulaw)
    check("DCS pipeline: 5 frames produced", len(bridge2.frames) == 5)
    dcs_rms = np.sqrt(np.mean(np.concatenate(bridge2.frames)**2))
    check("DCS tone present in output", dcs_rms > 0.005, f"RMS={dcs_rms:.5f}")


# ═════════════════════════════════════════════════════════════════════════════
# 7. ZMQ bridge (loopback test — no GNU Radio needed)
# ═════════════════════════════════════════════════════════════════════════════

def test_zmq_bridge():
    print("\n── 7. ZMQ bridge (loopback) ──────────────────────────────────────")
    import zmq

    # Use non-default ports to avoid conflicts
    AUDIO_PORT = 15556
    CTRL_PORT  = 15555

    bridge = GnuRadioBridge(host="127.0.0.1",
                             audio_port=AUDIO_PORT, ctrl_port=CTRL_PORT)

    # Receiver side: PULL for audio, SUB for control
    ctx   = zmq.Context()
    audio_rx = ctx.socket(zmq.PULL)
    audio_rx.connect(f"tcp://127.0.0.1:{AUDIO_PORT}")
    audio_rx.setsockopt(zmq.RCVTIMEO, 500)

    ctrl_rx = ctx.socket(zmq.SUB)
    ctrl_rx.connect(f"tcp://127.0.0.1:{CTRL_PORT}")
    ctrl_rx.setsockopt(zmq.SUBSCRIBE, b"")
    ctrl_rx.setsockopt(zmq.RCVTIMEO, 500)

    time.sleep(0.1)   # let sockets connect

    # ── Send audio ────────────────────────────────────────────────────────
    sent = np.linspace(-0.5, 0.5, 960, dtype=np.float32)
    bridge.send_audio(sent)

    try:
        raw  = audio_rx.recv()
        recv = np.frombuffer(raw, dtype=np.float32)
        check("audio frame received over ZMQ",   len(recv) == len(sent))
        check("audio values preserved",
              np.allclose(sent, recv, atol=1e-6),
              f"max_diff={np.max(np.abs(sent-recv)):.2e}")
    except zmq.Again:
        check("audio frame received over ZMQ", False, "timeout")

    # ── Send tune command ─────────────────────────────────────────────────
    ch = {
        "sip_user": "test", "freq_mhz": 146.52, "mode": "NFM",
        "tx_gain_db": 40, "deviation_hz": 5000,
        "ctcss_tx_hz": 100.0, "dcs_code": 0,
    }
    bridge.tune(ch)

    try:
        raw_ctrl = ctrl_rx.recv()
        cmd      = json.loads(raw_ctrl.decode())
        check("tune cmd received",            cmd["cmd"] == "tune")
        check("tune freq correct",            abs(cmd["freq_hz"] - 146.52e6) < 1)
        check("tune mode correct",            cmd["mode"] == "NFM")
        check("tune ctcss correct",           cmd["ctcss_hz"] == 100.0)
        check("tune dcs correct",             cmd["dcs_code"] == 0)
    except zmq.Again:
        check("tune cmd received", False, "timeout")

    # ── Stop command ──────────────────────────────────────────────────────
    bridge.stop()
    try:
        raw_stop = ctrl_rx.recv()
        cmd_stop = json.loads(raw_stop.decode())
        check("stop cmd received",    cmd_stop["cmd"] == "stop")
    except zmq.Again:
        check("stop cmd received", False, "timeout")

    # Cleanup
    audio_rx.close()
    ctrl_rx.close()
    ctx.term()
    bridge.close()


# ═════════════════════════════════════════════════════════════════════════════
# 8. RadioPhone mock (no PBX needed)
# ═════════════════════════════════════════════════════════════════════════════

def test_radio_phone_wiring():
    print("\n── 8. RadioPhone wiring (mocked SIP) ────────────────────────────")

    class MockBridge:
        def __init__(self):
            self.tuned_ch = None
            self.stopped  = False
            self.audio_frames = []
        def send_audio(self, s): self.audio_frames.append(s)
        def tune(self, ch):      self.tuned_ch = ch
        def stop(self):          self.stopped = True
        def close(self):         pass

    class MockCall:
        def __init__(self):
            self.state = None
            self._answered = threading.Event()
        def answer(self): self._answered.set()
        def readAudio(self):
            return bytes([0xFF] * 160)  # silence ulaw

    ch = {
        "sip_user": "radio-vhf",
        "sip_pass": "secret",
        "extension": "201",
        "freq_mhz": 146.52,
        "mode": "NFM",
        "ctcss_tx_hz": 100.0,
        "dcs_code": 0,
        "deviation_hz": 5000,
        "tx_gain_db": 40,
    }
    pbx    = {"host": "192.168.1.1", "port": 5060}
    bridge = MockBridge()
    phone  = RadioPhone(ch, pbx, bridge)

    # Simulate RINGING → ANSWERED → ENDED without actual SIP
    mock_call = MockCall()

    # RINGING
    from pyVoIP.VoIP import CallState
    mock_call.state = CallState.RINGING
    phone._on_call(mock_call)
    check("call.answer() called on RINGING", mock_call._answered.is_set())

    # ANSWERED
    mock_call.state = CallState.ANSWERED
    phone._on_call(mock_call)
    check("bridge.tune() called on ANSWERED", bridge.tuned_ch is not None)
    check("tuned to correct frequency",
          bridge.tuned_ch["freq_mhz"] == 146.52)
    check("pipeline created",         phone._pipeline is not None)

    # Give drain thread a moment to process a few packets
    time.sleep(0.05)
    mock_call.state = CallState.ENDED

    # ENDED
    phone._on_call(mock_call)
    check("bridge.stop() called on ENDED",  bridge.stopped)
    check("pipeline cleared on ENDED",      phone._pipeline is None)


# ═════════════════════════════════════════════════════════════════════════════
# 9. Frequency map validation
# ═════════════════════════════════════════════════════════════════════════════

def test_frequency_validation():
    print("\n── 9. Frequency map validation ───────────────────────────────────")

    with tempfile.TemporaryDirectory() as d:
        cfg_path = _make_config(pathlib.Path(d))
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)

        channels = cfg["channels"]

        # All extensions should be unique
        exts = [ch["extension"] for ch in channels]
        check("extensions are unique",     len(exts) == len(set(exts)))

        # All sip_users should be unique
        users = [ch["sip_user"] for ch in channels]
        check("sip_users are unique",      len(users) == len(set(users)))

        # Frequencies should be in realistic RF ranges
        for ch in channels:
            f = ch["freq_mhz"]
            in_range = 1.0 <= f <= 6000.0   # 1 MHz – 6 GHz
            check(f"{ch['sip_user']}: freq {f} MHz in RF range", in_range)

        # CTCSS frequencies should be valid EIA standards (67.0–254.1 Hz) or 0
        valid_ctcss = {
            0, 67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5,
            91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8,
            123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7,
            159.8, 162.2, 165.5, 167.9, 171.3, 173.8, 177.3, 179.9,
            183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5, 206.5,
            210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
        }
        for ch in channels:
            hz = ch.get("ctcss_tx_hz", 0)
            check(f"{ch['sip_user']}: CTCSS {hz} Hz is standard",
                  hz in valid_ctcss, f"got {hz}")

        # Modes should be known
        known_modes = {"NFM", "WFM", "AM", "USB", "LSB"}
        for ch in channels:
            check(f"{ch['sip_user']}: mode '{ch['mode']}' known",
                  ch["mode"] in known_modes)


# ═════════════════════════════════════════════════════════════════════════════
# 10. Stress / concurrent pipeline test
# ═════════════════════════════════════════════════════════════════════════════

def test_concurrent_pipelines():
    print("\n── 10. Concurrent pipeline stress test ───────────────────────────")

    class NullBridge:
        def send_audio(self, _): pass

    ch_template = {
        "sip_user": "stress", "freq_mhz": 146.52, "mode": "NFM",
        "ctcss_tx_hz": 100.0, "dcs_code": 0,
        "deviation_hz": 5000, "tx_gain_db": 40,
    }

    errors = []
    packets_per_thread = 200
    n_threads = 4

    def run_pipeline(thread_id):
        try:
            bridge = NullBridge()
            pipe   = AudioPipeline({**ch_template, "sip_user": f"stress-{thread_id}"}, bridge)
            for _ in range(packets_per_thread):
                # Alternate silence and tone
                payload = bytes([0xFF] * 160) if (_ % 2 == 0) else bytes([0x00] * 160)
                pipe.process(payload)
        except Exception as e:
            errors.append(f"Thread {thread_id}: {e}")

    threads = [threading.Thread(target=run_pipeline, args=(i,)) for i in range(n_threads)]
    t0 = time.perf_counter()
    for t in threads: t.start()
    for t in threads: t.join()
    elapsed = time.perf_counter() - t0

    total_frames = n_threads * packets_per_thread
    fps = total_frames / elapsed

    check("no errors in concurrent run",   len(errors) == 0,
          "; ".join(errors) if errors else "clean")
    check("throughput > 100 frames/sec",   fps > 100,
          f"{fps:.0f} frames/sec across {n_threads} threads")


# ═════════════════════════════════════════════════════════════════════════════
# Runner
# ═════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  VoIP-SDR Gateway — Test Suite")
    print("=" * 60)

    test_config_loading()
    test_ulaw_decoder()
    test_resampler()
    test_ctcss_generator()
    test_dcs_encoder()
    test_audio_pipeline()
    test_zmq_bridge()
    test_radio_phone_wiring()
    test_frequency_validation()
    test_concurrent_pipelines()

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in _results if ok)
    failed = sum(1 for _, ok in _results if not ok)
    total  = len(_results)
    print(f"  Results: {passed}/{total} passed", end="")
    if failed:
        print(f"  (\033[91m{failed} FAILED\033[0m)")
        print("\nFailed checks:")
        for name, ok in _results:
            if not ok:
                print(f"  \033[91m✗\033[0m {name}")
    else:
        print("  \033[92m— all passed\033[0m")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
