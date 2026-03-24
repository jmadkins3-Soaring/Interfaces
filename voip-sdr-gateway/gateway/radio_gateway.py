#!/usr/bin/env python3
"""
gateway/radio_gateway.py  —  v2 (DMR + Pluto+ optimizations)

Changes from v1:
  - CapabilityDetector probed at startup before any SIP registration
  - DMR channels skipped when DVSI dongle absent
  - AudioPipelineFactory replaces direct AudioPipeline construction
  - GnuRadioBridge tune() includes mode field for FM/4FSK switching
  - Pluto+ frequency range gate added
  - Re-exports of audio primitives kept for test compatibility
"""

import threading, logging, time, json, sys
import numpy as np
import zmq, yaml

from pyVoIP.VoIP import VoIPPhone, CallState, InvalidStateError
from pyVoIP.VoIP.VoIP import VoIPCall

from gateway.capability_detector import CapabilityDetector, CapabilityStore
from gateway.audio_pipeline_factory import (
    AudioPipelineFactory,
    decode_ulaw_float as decode_ulaw,
    Resampler,
    CtcssGen,
    DcsEncoder,
)

log = logging.getLogger("radio_gateway")

# Re-export for test-suite backward compatibility
_ULAW_TABLE = None


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
        cmd = {
            "cmd":           "tune",
            "freq_hz":       ch["freq_mhz"] * 1e6,
            "mode":          ch.get("mode", "NFM"),
            "tx_gain":       ch.get("tx_gain_db", 40),
            "deviation_hz":  ch.get("deviation_hz", 5000),
            "ctcss_hz":      ch.get("ctcss_tx_hz", 0),
            "dcs_code":      ch.get("dcs_code", 0),
            "dmr_colorcode": ch.get("dmr_colorcode", 1),
            "dmr_timeslot":  ch.get("dmr_timeslot", 1),
        }
        self._ctrl.send(json.dumps(cmd).encode())
        log.info("Tune → %.4f MHz  mode=%s", ch["freq_mhz"], ch.get("mode","NFM"))

    def stop(self):
        self._ctrl.send(json.dumps({"cmd": "stop"}).encode())

    def close(self):
        self._audio.close(); self._ctrl.close(); self._ctx.term()


class RadioPhone:
    def __init__(self, channel_cfg, pbx_cfg, bridge: GnuRadioBridge,
                 caps: CapabilityStore):
        self.ch = channel_cfg; self.pbx = pbx_cfg
        self.bridge = bridge; self.caps = caps
        self._phone = None
        self._lock  = threading.Lock()
        self._pipeline = None

    def start(self):
        pbx = self.pbx; ch = self.ch
        self._phone = VoIPPhone(
            server=pbx["host"], port=pbx.get("port", 5060),
            username=ch["sip_user"], password=ch["sip_pass"],
            callCallback=self._on_call,
        )
        self._phone.start()
        mode = ch.get("mode", "NFM")
        extra = (f"  cc={ch.get('dmr_colorcode',1)} ts={ch.get('dmr_timeslot',1)}"
                 if mode == "DMR" else "")
        log.info("[%s] Registered → %s ext %s  %.4f MHz [%s]%s",
                 ch["sip_user"], pbx["host"], ch["extension"],
                 ch["freq_mhz"], mode, extra)

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
                self._pipeline = AudioPipelineFactory.build(
                    self.ch, self.bridge, self.caps)
            self.bridge.tune(self.ch)
            threading.Thread(target=self._drain_rtp,
                             args=(call,), daemon=True).start()
        elif state == CallState.ENDED:
            with self._lock:
                if self._pipeline:
                    self._pipeline.close(); self._pipeline = None
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


class RadioGateway:
    def __init__(self, config_path="config/channels.yaml"):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)
        self.bridge = GnuRadioBridge()
        self.phones: list[RadioPhone] = []
        self.caps = CapabilityStore()

    def start(self):
        log.info("Probing hardware capabilities...")
        self.caps = CapabilityDetector().probe()
        log.info("\n%s", self.caps.summary())
        self._start_gnuradio()
        self._register_phones()
        n_a = sum(1 for p in self.phones if p.ch.get("mode","NFM") != "DMR")
        n_d = sum(1 for p in self.phones if p.ch.get("mode") == "DMR")
        log.info("Gateway running — %d analog, %d DMR channel(s). Ctrl-C to stop.",
                 n_a, n_d)
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            log.info("Shutting down…")
        finally:
            self.stop()

    def stop(self):
        for p in self.phones: p.stop()
        self.bridge.close()
        if hasattr(self, "_gr_proc") and self._gr_proc:
            self._gr_proc.terminate()

    def _start_gnuradio(self):
        import subprocess
        self._gr_proc = subprocess.Popen(
            [sys.executable, "gnuradio/sdr_modulator.py",
             "--config", "config/channels.yaml"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        time.sleep(2)
        if self._gr_proc.poll() is not None:
            raise RuntimeError("GNU Radio exited immediately")
        log.info("GNU Radio PID %d", self._gr_proc.pid)

    def _register_phones(self):
        pbx = self.cfg["pbx"]; skipped = 0
        for ch in self.cfg["channels"]:
            mode = ch.get("mode", "NFM").upper()
            if mode == "DMR" and not self.caps.dvsi_present:
                log.warning("[%s] Skipping — DMR requires DVSI dongle (not found)",
                            ch["sip_user"])
                skipped += 1; continue
            if not self._freq_in_range(ch["freq_mhz"]):
                log.warning("[%s] Skipping — %.3f MHz outside Pluto+ range",
                            ch["sip_user"], ch["freq_mhz"])
                skipped += 1; continue
            p = RadioPhone(ch, pbx, self.bridge, self.caps)
            p.start(); self.phones.append(p); time.sleep(0.3)
        if skipped:
            log.info("%d channel(s) skipped", skipped)

    @staticmethod
    def _freq_in_range(freq_mhz: float) -> bool:
        return 70.0 <= freq_mhz <= 6000.0


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    ap = argparse.ArgumentParser(description="VoIP-SDR Gateway v2")
    ap.add_argument("--config", default="config/channels.yaml")
    RadioGateway(ap.parse_args().config).start()
