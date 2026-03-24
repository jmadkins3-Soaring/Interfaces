#!/usr/bin/env python3
"""
tests/test_gateway.py  —  v2

Full test suite for voip-sdr-gateway v2.
New tests: CapabilityDetector, factory gating, DVSI framing,
           DMR channel filtering, Pluto+ frequency range gate.

Run:  python tests/test_gateway.py
"""

import sys, os, math, json, threading, time, tempfile, pathlib
import numpy as np
import yaml

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from gateway.capability_detector import CapabilityStore, CapabilityDetector
from gateway.audio_pipeline_factory import (
    AudioPipelineFactory, AnalogPipeline, DmrPipeline,
    decode_ulaw_float, decode_ulaw_pcm, Resampler, CtcssGen, DcsEncoder, _ULAW_TABLE,
)
from gateway.dvsi_vocoder import (
    _build_packet, _build_encode_packet, _build_decode_packet,
    PKT_HEADER, PKT_SPEECH, PKT_CHAND, PCM_BYTES_PER_FRAME, AMBE_BYTES_PER_FRAME,
)
from gateway.radio_gateway import GnuRadioBridge, RadioPhone, RadioGateway

PASS = "\033[92m✓\033[0m"; FAIL = "\033[91m✗\033[0m"
_results = []

def check(name, expr, detail=""):
    status = PASS if expr else FAIL
    msg = f"{status} {name}"
    if detail: msg += f"  ({detail})"
    print(msg)
    _results.append((name, expr))
    return expr

def _make_cfg(tmp_path, extra=None):
    channels = [
        {"sip_user":"radio-vhf","sip_pass":"s","extension":"201","display_name":"VHF",
         "freq_mhz":146.520,"mode":"NFM","deviation_hz":5000,"ctcss_tx_hz":100.0,
         "ctcss_rx_hz":100.0,"dcs_code":0,"tx_gain_db":40},
        {"sip_user":"radio-dcs","sip_pass":"s","extension":"202","display_name":"DCS",
         "freq_mhz":462.5625,"mode":"NFM","deviation_hz":5000,"ctcss_tx_hz":0,
         "ctcss_rx_hz":0,"dcs_code":156,"tx_gain_db":40},
    ]
    if extra: channels.extend(extra)
    cfg = {"pbx":{"host":"192.168.1.1","port":5060},
           "sdr":{"driver":"plutosdr","device_args":"ip:192.168.2.1",
                  "sample_rate":520833,"audio_sample_rate":48000,"buffer_size":32768},
           "channels":channels}
    p = pathlib.Path(tmp_path) / "channels.yaml"
    p.write_text(yaml.dump(cfg))
    return str(p)

_DMR_CH = {"sip_user":"radio-dmr","sip_pass":"s","extension":"301",
            "display_name":"DMR","freq_mhz":446.500,"mode":"DMR",
            "dmr_id":3120101,"dmr_colorcode":1,"dmr_timeslot":1,
            "dmr_talkgroup":9,"tx_gain_db":40}

# ── 1. Config ──────────────────────────────────────────────────────────────
def test_config():
    print("\n── 1. Config loading (v2) ───────────────────────────────────────")
    with tempfile.TemporaryDirectory() as d:
        p = _make_cfg(d, extra=[_DMR_CH])
        with open(p) as f: cfg = yaml.safe_load(f)
        check("pbx host present",           "host" in cfg["pbx"])
        check("three channels",              len(cfg["channels"]) == 3)
        check("Pluto+ driver",               cfg["sdr"]["driver"] == "plutosdr")
        check("Ethernet URI",                cfg["sdr"]["device_args"].startswith("ip:"))
        check("sample_rate 520833",          cfg["sdr"]["sample_rate"] == 520833)
        check("buffer_size present",         "buffer_size" in cfg["sdr"])
        check("DMR channel present",         any(c["mode"]=="DMR" for c in cfg["channels"]))
        check("DMR colorcode",               cfg["channels"][2].get("dmr_colorcode") == 1)

# ── 2. CapabilityStore ────────────────────────────────────────────────────
def test_capability_store():
    print("\n── 2. CapabilityStore ───────────────────────────────────────────")
    c0 = CapabilityStore()
    check("default dvsi=False",  not c0.dvsi_present)
    check("default dmr=False",   not c0.dmr_capable)
    check("summary NOT FOUND",   "NOT FOUND" in c0.summary())
    cv = CapabilityStore(dvsi_present=True, dvsi_port="/dev/ttyUSB0", dvsi_version="AMBE-3000R2")
    check("dvsi=True",           cv.dvsi_present)
    check("dmr=True",            cv.dmr_capable)
    check("summary PRESENT",     "PRESENT" in cv.summary())
    check("summary port",        "/dev/ttyUSB0" in cv.summary())

# ── 3. CapabilityDetector (no hardware) ──────────────────────────────────
def test_capability_detector():
    print("\n── 3. CapabilityDetector (no hardware) ──────────────────────────")
    caps = CapabilityDetector().probe()
    check("returns CapabilityStore",   isinstance(caps, CapabilityStore))
    check("no dongle → False",         not caps.dvsi_present)
    check("no dongle → port=None",     caps.dvsi_port is None)
    check("summary non-empty",         len(caps.summary()) > 10)
    caps2 = CapabilityDetector().probe()
    check("probe idempotent",          caps2.dvsi_present == caps.dvsi_present)

# ── 4. DVSI packet framing ────────────────────────────────────────────────
def test_dvsi_framing():
    print("\n── 4. DVSI packet framing ───────────────────────────────────────")
    pcm  = bytes(PCM_BYTES_PER_FRAME)
    pkt  = _build_encode_packet(pcm)
    check("encode header 0x61",        pkt[0] == PKT_HEADER)
    check("encode type PKT_SPEECH",    pkt[4] == PKT_SPEECH)
    check("encode length",             len(pkt) == 5 + PCM_BYTES_PER_FRAME)
    length = (pkt[2] << 8) | pkt[3]
    check("encode length field",       length == PCM_BYTES_PER_FRAME + 1)
    ambe = bytes(AMBE_BYTES_PER_FRAME)
    dpkt = _build_decode_packet(ambe)
    check("decode header 0x61",        dpkt[0] == PKT_HEADER)
    check("decode type PKT_CHAND",     dpkt[4] == PKT_CHAND)
    check("decode length",             len(dpkt) == 5 + AMBE_BYTES_PER_FRAME)
    try:
        _build_encode_packet(b"short"); check("encode rejects short", False)
    except ValueError: check("encode rejects short", True)
    try:
        _build_decode_packet(b"short"); check("decode rejects short", False)
    except ValueError: check("decode rejects short", True)

# ── 5. Factory gating ────────────────────────────────────────────────────
def test_factory_gating():
    print("\n── 5. AudioPipelineFactory gating ───────────────────────────────")
    class NB:
        def send_audio(self, _): pass
    bridge = NB()
    ach = {"sip_user":"t","freq_mhz":146.52,"mode":"NFM","ctcss_tx_hz":100.0,
           "dcs_code":0,"deviation_hz":5000,"tx_gain_db":40}
    c0 = CapabilityStore(dvsi_present=False)
    cv = CapabilityStore(dvsi_present=True, dvsi_port="/dev/ttyUSB0", dvsi_version="v1")
    p1 = AudioPipelineFactory.build(ach, bridge, c0)
    check("analog+no dongle → Analog", isinstance(p1, AnalogPipeline)); p1.close()
    p2 = AudioPipelineFactory.build(ach, bridge, cv)
    check("analog+dongle → Analog",    isinstance(p2, AnalogPipeline)); p2.close()
    p3 = AudioPipelineFactory.build(_DMR_CH, bridge, c0)
    check("DMR+no dongle → fallback Analog", isinstance(p3, AnalogPipeline)); p3.close()

# ── 6. ulaw decoder (corrected G.711 expectations) ───────────────────────
def test_ulaw_decoder():
    print("\n── 6. ulaw decoder ──────────────────────────────────────────────")
    silence = bytes([0xFF] * 160)
    out = decode_ulaw_float(silence)
    check("silence near-zero",         np.max(np.abs(out)) < 0.01)
    check("output float32",            out.dtype == np.float32)
    check("output length 160",         len(out) == 160)
    out_i = decode_ulaw_pcm(silence)
    check("pcm output int16",          out_i.dtype == np.int16)
    all_b = decode_ulaw_float(bytes(range(256)))
    check("all 256 in [-1,1]",         np.all(np.abs(all_b) <= 1.0))
    check("table 256 entries",         len(_ULAW_TABLE) == 256)
    # G.711 reference (verified via audioop):
    s0 = decode_ulaw_float(bytes([0x00]))[0]
    s7 = decode_ulaw_float(bytes([0x7F]))[0]
    s8 = decode_ulaw_float(bytes([0x80]))[0]
    check("0x00 → large negative",     s0 < -0.9,  f"got {s0:.4f}")
    check("0x7F → near silence",       abs(s7) < 0.002, f"got {s7:.6f}")
    check("0x80 → large positive",     s8 > 0.9,   f"got {s8:.4f}")

# ── 7. Resampler ──────────────────────────────────────────────────────────
def test_resampler():
    print("\n── 7. Resampler (8kHz → 48kHz) ──────────────────────────────────")
    rs  = Resampler()
    pcm = np.random.randn(160).astype(np.float32) * 0.3
    out = rs.process(pcm)
    check("output 6× input",  len(out) == 960, f"got {len(out)}")
    check("float32",           out.dtype == np.float32)
    check("finite",            np.all(np.isfinite(out)))
    t    = np.arange(160) / 8000
    tone = np.sin(2 * math.pi * 1000 * t).astype(np.float32)
    rs2  = Resampler(); out2 = rs2.process(tone)
    rms  = np.sqrt(np.mean(out2[200:]**2))
    check("1kHz passes", rms > 0.3, f"RMS={rms:.3f}")
    rs4 = Resampler()
    b1  = rs4.process(np.ones(160, dtype=np.float32) * 0.5)
    b2  = rs4.process(np.ones(160, dtype=np.float32) * 0.5)
    check("stateful FIR OK", np.all(np.isfinite(b2)))

# ── 8. CTCSS ─────────────────────────────────────────────────────────────
def test_ctcss():
    print("\n── 8. CTCSS tone generator ───────────────────────────────────────")
    sr = 48000
    for freq in [88.5, 100.0, 141.3, 167.9, 254.1]:
        g = CtcssGen(freq_hz=freq, sr=sr, level=0.15)
        t = g.generate(sr)
        zc = np.where(np.diff(np.sign(t)))[0]
        if len(zc) > 2:
            m = sr / np.mean(np.diff(zc)) / 2
            check(f"CTCSS {freq:.1f} Hz", abs(m - freq) < 0.5, f"measured={m:.2f}")
        else:
            check(f"CTCSS {freq:.1f} Hz zero crossings", False)
    g0 = CtcssGen(0.0); check("freq=0 silence", np.all(g0.generate(480) == 0))
    gp = CtcssGen(100.0)
    s1 = gp.generate(480); s2 = gp.generate(480)
    mj = np.max(np.abs(np.diff(np.concatenate([s1, s2]))))
    check("phase continuous", mj < 0.15, f"max jump={mj:.4f}")

# ── 9. DCS ───────────────────────────────────────────────────────────────
def test_dcs():
    print("\n── 9. DCS encoder ────────────────────────────────────────────────")
    for code in [156, 131, 205, 265]:
        e = DcsEncoder(code=code, sr=48000); t = e.generate(48000)
        check(f"DCS {code} length",  len(t) == 48000)
        check(f"DCS {code} float32", t.dtype == np.float32)
        check(f"DCS {code} finite",  np.all(np.isfinite(t)))
        check(f"DCS {code} peak",    abs(np.max(np.abs(t)) - 0.10) < 0.02)
    e0 = DcsEncoder(0); check("DCS 0 silence", np.all(e0.generate(480) == 0))

# ── 10. AnalogPipeline end-to-end ────────────────────────────────────────
def test_analog_pipeline():
    print("\n── 10. AnalogPipeline end-to-end ────────────────────────────────")
    class CB:
        def __init__(self): self.frames = []
        def send_audio(self, s): self.frames.append(s.copy())
    ch = {"sip_user":"t","freq_mhz":146.52,"mode":"NFM","ctcss_tx_hz":100.0,
          "dcs_code":0,"deviation_hz":5000,"tx_gain_db":40}
    b = CB(); p = AnalogPipeline(ch, b); s = bytes([0xFF]*160)
    for _ in range(10): p.process(s)
    check("10 frames",           len(b.frames) == 10)
    check("frame len 960",       all(len(f)==960 for f in b.frames))
    check("float32",             b.frames[0].dtype == np.float32)
    check("clipped [-1,1]",      all(np.all(np.abs(f)<=1.0) for f in b.frames))
    rms = np.sqrt(np.mean(np.concatenate(b.frames[2:])**2))
    check("CTCSS present",       rms > 0.01, f"RMS={rms:.4f}")
    p.close()

# ── 11. ZMQ bridge ───────────────────────────────────────────────────────
def test_zmq_bridge():
    print("\n── 11. ZMQ bridge (loopback) ─────────────────────────────────────")
    import zmq
    AP = 15556; CP = 15555
    bridge = GnuRadioBridge(host="127.0.0.1", audio_port=AP, ctrl_port=CP)
    ctx = zmq.Context()
    arx = ctx.socket(zmq.PULL); arx.connect(f"tcp://127.0.0.1:{AP}")
    arx.setsockopt(zmq.RCVTIMEO, 500)
    crx = ctx.socket(zmq.SUB); crx.connect(f"tcp://127.0.0.1:{CP}")
    crx.setsockopt(zmq.SUBSCRIBE, b""); crx.setsockopt(zmq.RCVTIMEO, 500)
    time.sleep(0.1)
    sent = np.linspace(-0.5, 0.5, 960, dtype=np.float32)
    bridge.send_audio(sent)
    try:
        recv = np.frombuffer(arx.recv(), dtype=np.float32)
        check("audio received",       len(recv) == len(sent))
        check("values preserved",     np.allclose(sent, recv, atol=1e-6))
    except zmq.Again: check("audio received", False, "timeout")
    bridge.tune(_DMR_CH)
    try:
        cmd = json.loads(crx.recv().decode())
        check("tune received",        cmd["cmd"] == "tune")
        check("mode field present",   "mode" in cmd)
        check("mode = DMR",           cmd["mode"] == "DMR")
        check("dmr_colorcode",        "dmr_colorcode" in cmd)
        check("dmr_timeslot",         "dmr_timeslot" in cmd)
    except zmq.Again: check("tune received", False, "timeout")
    bridge.stop()
    try:
        stop = json.loads(crx.recv().decode())
        check("stop received", stop["cmd"] == "stop")
    except zmq.Again: check("stop received", False, "timeout")
    arx.close(); crx.close(); ctx.term(); bridge.close()

# ── 12. RadioPhone wiring ─────────────────────────────────────────────────
def test_radio_phone_wiring():
    print("\n── 12. RadioPhone wiring (mocked SIP) ───────────────────────────")
    class MB:
        def __init__(self): self.tuned_ch = None; self.stopped = False
        def send_audio(self, _): pass
        def tune(self, ch): self.tuned_ch = ch
        def stop(self): self.stopped = True
        def close(self): pass
    class MC:
        def __init__(self): self.state = None; self._ans = threading.Event()
        def answer(self): self._ans.set()
        def readAudio(self): return bytes([0xFF]*160)
    ch = {"sip_user":"t","sip_pass":"s","extension":"201","freq_mhz":146.52,
          "mode":"NFM","ctcss_tx_hz":100.0,"dcs_code":0,"deviation_hz":5000,"tx_gain_db":40}
    from pyVoIP.VoIP import CallState
    mc = MC(); b = MB(); phone = RadioPhone(ch, {"host":"192.168.1.1","port":5060}, b, CapabilityStore())
    mc.state = CallState.RINGING; phone._on_call(mc)
    check("answer() called",          mc._ans.is_set())
    mc.state = CallState.ANSWERED; phone._on_call(mc)
    check("bridge.tune() called",     b.tuned_ch is not None)
    check("pipeline created",         phone._pipeline is not None)
    time.sleep(0.05); mc.state = CallState.ENDED; phone._on_call(mc)
    check("bridge.stop() called",     b.stopped)
    check("pipeline cleared",         phone._pipeline is None)

# ── 13. DMR channel filtering ─────────────────────────────────────────────
def test_dmr_filtering():
    print("\n── 13. DMR channel filtering in RadioGateway ────────────────────")
    with tempfile.TemporaryDirectory() as d:
        p = _make_cfg(d, extra=[_DMR_CH])
        gw = RadioGateway.__new__(RadioGateway)
        with open(p) as f: gw.cfg = yaml.safe_load(f)
        c0 = CapabilityStore(dvsi_present=False)
        cv = CapabilityStore(dvsi_present=True, dvsi_port="/dev/ttyUSB0", dvsi_version="v1")
        def skip(caps):
            n = 0
            for ch in gw.cfg["channels"]:
                if ch.get("mode","NFM").upper() == "DMR" and not caps.dvsi_present: n += 1
                elif not RadioGateway._freq_in_range(ch["freq_mhz"]): n += 1
            return n
        check("DMR skipped without dongle",  skip(c0) == 1)
        check("DMR kept with dongle",        skip(cv) == 0)

# ── 14. Pluto+ frequency range ────────────────────────────────────────────
def test_freq_range():
    print("\n── 14. Pluto+ frequency range gate ──────────────────────────────")
    for f, label in [(70.0,"lower bound"),(146.52,"VHF ham"),(446.0,"UHF"),
                     (462.56,"GMRS"),(6000.0,"upper bound")]:
        check(f"in range {f} MHz ({label})", RadioGateway._freq_in_range(f))
    for f, label in [(27.185,"CB HF"),(50.0,"6m below 70"),(6001.0,"above 6GHz")]:
        check(f"out of range {f} MHz ({label})", not RadioGateway._freq_in_range(f))

# ── 15. Stress test ───────────────────────────────────────────────────────
def test_stress():
    print("\n── 15. Concurrent pipeline stress test ───────────────────────────")
    class NB:
        def send_audio(self, _): pass
    errors = []; N = 4; P = 200
    def run(tid):
        try:
            ch = {"sip_user":f"s{tid}","freq_mhz":146.52,"mode":"NFM",
                  "ctcss_tx_hz":100.0,"dcs_code":0,"deviation_hz":5000,"tx_gain_db":40}
            p = AnalogPipeline(ch, NB())
            for i in range(P): p.process(bytes([0xFF if i%2==0 else 0x00]*160))
            p.close()
        except Exception as e: errors.append(str(e))
    threads = [threading.Thread(target=run, args=(i,)) for i in range(N)]
    t0 = time.perf_counter()
    for t in threads: t.start()
    for t in threads: t.join()
    fps = (N * P) / (time.perf_counter() - t0)
    check("no errors", len(errors)==0, "; ".join(errors) if errors else "clean")
    check(f"throughput > 100 fps", fps > 100, f"{fps:.0f} fps")

def main():
    print("=" * 60)
    print("  VoIP-SDR Gateway v2 — Test Suite")
    print("=" * 60)
    test_config(); test_capability_store(); test_capability_detector()
    test_dvsi_framing(); test_factory_gating(); test_ulaw_decoder()
    test_resampler(); test_ctcss(); test_dcs(); test_analog_pipeline()
    test_zmq_bridge(); test_radio_phone_wiring()
    test_dmr_filtering(); test_freq_range(); test_stress()
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in _results if ok)
    failed = sum(1 for _, ok in _results if not ok)
    print(f"  Results: {passed}/{len(_results)} passed", end="")
    if failed:
        print(f"  (\033[91m{failed} FAILED\033[0m)")
        for name, ok in _results:
            if not ok: print(f"  \033[91m✗\033[0m {name}")
    else:
        print("  \033[92m— all passed\033[0m")
    print("=" * 60)
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    import sys; sys.exit(main())
