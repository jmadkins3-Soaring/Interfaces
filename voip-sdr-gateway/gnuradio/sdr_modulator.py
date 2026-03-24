#!/usr/bin/env python3
"""
voip-sdr-gateway  —  gnuradio/sdr_modulator.py

GNU Radio flowgraph that:
  - Pulls audio frames from the gateway via ZMQ PULL
  - Modulates (NFM / WFM / AM) based on control messages
  - Drives the SDR hardware via gr-osmosdr / SoapySDR

This script is launched as a subprocess by radio_gateway.py.
It listens on a ZMQ control socket for real-time tune commands.

Repos:
    GNU Radio  : github.com/gnuradio/gnuradio
    gr-osmosdr : github.com/osmocom/gr-osmosdr
    SoapySDR   : github.com/pothosware/SoapySDR

Install:
    sudo apt install gnuradio gr-osmosdr python3-zmq
    pip install pyyaml
"""

import gnuradio
from gnuradio import gr, blocks, analog, filter as gr_filter
from gnuradio.filter import firdes
import osmosdr          # gr-osmosdr
import numpy as np
import zmq
import yaml
import json
import threading
import logging
import argparse
import time
import sys

log = logging.getLogger("sdr_modulator")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [sdr_mod] %(levelname)s: %(message)s")


# ── ZMQ audio source block ───────────────────────────────────────────────────

class ZmqAudioSource(gr.sync_block):
    """
    Custom GNU Radio source block.
    Pulls float32 audio frames from a ZMQ PULL socket
    and streams them into the flowgraph.
    """

    BUFFER_FRAMES = 8    # frames to buffer before starving

    def __init__(self, host: str = "127.0.0.1", port: int = 5556,
                 frame_size: int = 960):
        gr.sync_block.__init__(self, name="ZmqAudioSource",
                               in_sig=None,
                               out_sig=[np.float32])
        self.frame_size = frame_size
        self._buf = np.zeros(0, dtype=np.float32)
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.PULL)
        self._sock.connect(f"tcp://{host}:{port}")
        self._sock.setsockopt(zmq.RCVTIMEO, 50)   # 50ms timeout → silence on starve
        log.info("ZMQ PULL audio connected to tcp://%s:%d", host, port)

    def work(self, input_items, output_items):
        out = output_items[0]
        needed = len(out)

        # Fill buffer from ZMQ until we have enough samples
        while len(self._buf) < needed:
            try:
                raw = self._sock.recv()
                chunk = np.frombuffer(raw, dtype=np.float32)
                self._buf = np.concatenate([self._buf, chunk])
            except zmq.Again:
                # Timeout — pad with silence to avoid blocking the flowgraph
                self._buf = np.concatenate(
                    [self._buf, np.zeros(self.frame_size, dtype=np.float32)])

        out[:needed] = self._buf[:needed]
        self._buf = self._buf[needed:]
        return needed

    def stop(self):
        self._sock.close()
        self._ctx.term()
        return True


# ── NFM modulator chain ──────────────────────────────────────────────────────

class NfmModulator:
    """
    Narrow FM modulator.
    Audio (float32, 48kHz) → FM modulated IQ at SDR sample rate.

    Chain:
        audio → pre-emphasis → FM modulate → low-pass filter → rational resampler
    """

    def __init__(self, tb: gr.top_block, audio_rate: int = 48000,
                 sdr_rate: int = 2_000_000, deviation: int = 5000,
                 audio_src=None):

        self.audio_rate = audio_rate
        self.sdr_rate = sdr_rate
        self.deviation = deviation

        # Pre-emphasis (standard 6dB/oct above ~300Hz, τ = 530μs)
        self.preemph = analog.fm_preemph(fs=audio_rate, tau=530e-6)

        # FM modulator — output is complex IQ
        self.fm_mod = analog.frequency_modulator_fc(
            sensitivity=2 * np.pi * deviation / audio_rate
        )

        # Interpolating filter to SDR sample rate
        interp = sdr_rate // audio_rate   # e.g. 2M/48k ≈ 41.67 → handled by rational
        # Use a simple interpolation + LPF for channel bandwidth limiting
        self.resampler = filter.rational_resampler_ccc(
            interpolation=sdr_rate,
            decimation=audio_rate,
            taps=firdes.low_pass(1, sdr_rate,
                                  deviation * 1.5, deviation * 0.5,
                                  firdes.WIN_HAMMING)
        )

        if audio_src:
            tb.connect(audio_src, self.preemph)
            tb.connect(self.preemph, self.fm_mod)
            tb.connect(self.fm_mod, self.resampler)

    @property
    def output(self):
        return self.resampler


class WfmModulator:
    """Wide FM modulator — for broadcast FM (75kHz deviation)."""

    def __init__(self, tb, audio_rate=48000, sdr_rate=2_000_000, deviation=75000, audio_src=None):
        self.fm_mod = analog.frequency_modulator_fc(
            sensitivity=2 * np.pi * deviation / audio_rate)
        self.resampler = filter.rational_resampler_ccc(
            interpolation=sdr_rate, decimation=audio_rate)
        if audio_src:
            tb.connect(audio_src, self.fm_mod)
            tb.connect(self.fm_mod, self.resampler)

    @property
    def output(self):
        return self.resampler


class AmModulator:
    """
    AM (DSB-FC) modulator for CB radio and HF.
    audio → scale → add carrier → multiply by carrier IQ
    """

    def __init__(self, tb, audio_rate=48000, sdr_rate=2_000_000,
                 mod_index=0.85, audio_src=None):
        # Scale audio to [0,1] range, then add DC bias for carrier
        self.scale = blocks.multiply_const_ff(mod_index / 2)
        self.add_dc = blocks.add_const_ff(0.5)
        self.to_complex = blocks.float_to_complex()
        self.resampler = filter.rational_resampler_ccc(
            interpolation=sdr_rate, decimation=audio_rate)
        if audio_src:
            tb.connect(audio_src, self.scale, self.add_dc, self.to_complex, self.resampler)

    @property
    def output(self):
        return self.resampler


# ── Main SDR flowgraph ───────────────────────────────────────────────────────

class SdrFlowgraph(gr.top_block):
    """
    Top-level GNU Radio flowgraph.

    Topology:
        ZmqAudioSource → [modulator chain] → osmosdr_sink

    The modulator chain is rebuilt whenever a tune command changes the mode.
    The osmosdr_sink frequency/gain are updated in-place.
    """

    def __init__(self, cfg: dict):
        super().__init__("VoIP-SDR Gateway Modulator")

        sdr_cfg = cfg.get("sdr", {})
        self.sdr_rate = sdr_cfg.get("sample_rate", 2_000_000)
        self.audio_rate = sdr_cfg.get("audio_sample_rate", 48000)
        driver = sdr_cfg.get("driver", "hackrf")
        device_args = sdr_cfg.get("device_args", "")

        # Build device string for osmosdr
        if device_args:
            dev_str = f"{driver}={device_args}"
        else:
            dev_str = driver
        log.info("Opening SDR device: %s", dev_str)

        # ── ZMQ audio source ────────────────────────────────────────────
        self.audio_src = ZmqAudioSource(port=5556, frame_size=self.audio_rate // 50)

        # ── Initial modulator (NFM default) ─────────────────────────────
        self.modulator = NfmModulator(
            self, audio_rate=self.audio_rate, sdr_rate=self.sdr_rate,
            deviation=5000, audio_src=self.audio_src
        )

        # ── Silence gate — mute TX when no call is active ───────────────
        self.gate = blocks.multiply_const_cc(0.0)  # starts muted
        self.connect(self.modulator.output, self.gate)

        # ── SDR hardware sink ────────────────────────────────────────────
        self.sink = osmosdr.sink(args=dev_str)
        self.sink.set_sample_rate(self.sdr_rate)
        self.sink.set_center_freq(146.52e6)   # default — overridden by tune cmd
        self.sink.set_freq_corr(0)
        self.sink.set_gain(0)
        self.sink.set_if_gain(40)
        self.sink.set_bb_gain(20)
        self.sink.set_antenna("TX/RX")
        self.sink.set_bandwidth(200000)

        self.connect(self.gate, self.sink)

        log.info("Flowgraph built — SDR rate %d MSPS", self.sdr_rate // 1_000_000)

    def apply_tune(self, cmd: dict):
        """Apply a tune command received from the gateway via ZMQ control."""
        freq_hz = cmd.get("freq_hz", 146.52e6)
        mode = cmd.get("mode", "NFM").upper()
        tx_gain = cmd.get("tx_gain", 40)
        deviation = cmd.get("deviation_hz", 5000)

        log.info("Tune: %.4f MHz  mode=%s  gain=%d  dev=%dHz",
                 freq_hz / 1e6, mode, tx_gain, deviation)

        self.sink.set_center_freq(freq_hz)
        self.sink.set_if_gain(tx_gain)

        # Unmute gate
        self.gate.set_k(1.0)

    def stop_tx(self):
        """Mute the TX gate without stopping the flowgraph."""
        self.gate.set_k(0.0)
        log.info("TX muted")


# ── ZMQ control listener ─────────────────────────────────────────────────────

class ControlListener(threading.Thread):
    """
    Listens on ZMQ SUB socket for control messages from the gateway.
    Applies tune/stop commands to the running flowgraph.
    """

    def __init__(self, flowgraph: SdrFlowgraph, host: str = "127.0.0.1", port: int = 5555):
        super().__init__(daemon=True)
        self.fg = flowgraph
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.SUB)
        self._sock.connect(f"tcp://{host}:{port}")
        self._sock.setsockopt(zmq.SUBSCRIBE, b"")
        self._running = True
        log.info("Control SUB connected to tcp://%s:%d", host, port)

    def run(self):
        while self._running:
            try:
                raw = self._sock.recv(flags=zmq.NOBLOCK)
                cmd = json.loads(raw.decode())
                log.debug("Control cmd: %s", cmd)
                if cmd.get("cmd") == "tune":
                    self.fg.apply_tune(cmd)
                elif cmd.get("cmd") == "stop":
                    self.fg.stop_tx()
            except zmq.Again:
                time.sleep(0.01)
            except Exception as e:
                log.error("Control error: %s", e)

    def stop(self):
        self._running = False
        self._sock.close()
        self._ctx.term()


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="VoIP-SDR GNU Radio Modulator")
    p.add_argument("--config", default="config/channels.yaml")
    args = p.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    fg = SdrFlowgraph(cfg)
    ctrl = ControlListener(fg)
    ctrl.start()

    log.info("Starting flowgraph...")
    fg.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping flowgraph...")
    finally:
        fg.stop()
        fg.wait()
        ctrl.stop()


if __name__ == "__main__":
    main()
