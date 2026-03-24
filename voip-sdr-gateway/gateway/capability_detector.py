#!/usr/bin/env python3
"""
gateway/capability_detector.py

Probes attached hardware at startup and returns a CapabilityStore
that every other component reads to decide what it can offer.

Detection is intentionally conservative:
  - Any failure at any stage → dvsi_present = False
  - No exceptions propagate out of probe() — callers always get a result
  - The system runs fine without the dongle; DMR channels simply aren't
    registered on the PBX

DVSI USB-3000 identification:
  USB VID: 0x09fb  (DVSI Inc.)
  USB PID: 0x6003  (USB-3000 / AMBE-3000)
  Serial:  460800 baud, 8N1, no flow control

Probe sequence:
  1. Scan /sys/bus/usb/devices for matching VID:PID
  2. Find the associated ttyUSB* device node
  3. Open serial port, send Product ID request (0x00 0x00 0x09)
  4. Expect response containing 'AMBE' within 200ms
  5. Close port — DvsiVocoder will reopen it when needed
"""

import os
import glob
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("capability_detector")

DVSI_VID = "09fb"
DVSI_PID = "6003"
DVSI_BAUD = 460800

# Product ID query packet per DVSI USB-3000 reference manual
# Packet: start=0x61, channel=0x00, length=0x00 0x01, type=PKT_PRODID=0x00
_PRODID_PACKET = bytes([0x61, 0x00, 0x00, 0x01, 0x00])
_PRODID_RESPONSE_MARKER = b"AMBE"


@dataclass
class CapabilityStore:
    """Runtime capability snapshot — set once at startup, read everywhere."""

    dvsi_present: bool = False
    dvsi_port: Optional[str] = None
    dvsi_version: Optional[str] = None

    # Future capability flags — add as hardware support grows
    sdr_present: bool = False
    sdr_driver: Optional[str] = None
    sdr_uri: Optional[str] = None

    @property
    def dmr_capable(self) -> bool:
        return self.dvsi_present

    def summary(self) -> str:
        lines = ["Hardware capability report:"]
        if self.dvsi_present:
            lines.append(f"  DVSI dongle : PRESENT  ({self.dvsi_port}  v{self.dvsi_version})")
        else:
            lines.append("  DVSI dongle : NOT FOUND  — DMR channels disabled")
        if self.sdr_present:
            lines.append(f"  SDR         : {self.sdr_driver}  {self.sdr_uri}")
        return "\n".join(lines)


class CapabilityDetector:
    """
    Probes USB bus and hardware serials to determine what capabilities
    are available.  Call probe() once at startup.
    """

    def probe(self) -> CapabilityStore:
        caps = CapabilityStore()
        try:
            caps = self._probe_dvsi(caps)
        except Exception as e:
            log.warning("DVSI probe raised unexpected error: %s — analog only", e)
        return caps

    # ── Private ──────────────────────────────────────────────────────────

    def _probe_dvsi(self, caps: CapabilityStore) -> CapabilityStore:
        port = self._find_dvsi_port()
        if not port:
            log.info("DVSI USB-3000 not found on USB bus")
            return caps

        log.info("DVSI device found, probing %s ...", port)
        version = self._verify_dvsi(port)
        if version:
            caps.dvsi_present = True
            caps.dvsi_port    = port
            caps.dvsi_version = version
            log.info("DVSI USB-3000 confirmed: %s  version=%s", port, version)
        else:
            log.warning("DVSI device at %s did not respond to product ID query", port)
        return caps

    def _find_dvsi_port(self) -> Optional[str]:
        """
        Walk /sys/bus/usb/devices looking for VID:PID match,
        then find the ttyUSB* node it exposes.
        """
        # Method 1: sysfs (Linux)
        try:
            port = self._find_via_sysfs()
            if port:
                return port
        except Exception:
            pass

        # Method 2: brute-force scan all ttyUSB devices
        try:
            port = self._find_via_ttyusb_scan()
            if port:
                return port
        except Exception:
            pass

        return None

    def _find_via_sysfs(self) -> Optional[str]:
        """Match VID:PID in /sys/bus/usb/devices and resolve ttyUSB*."""
        base = "/sys/bus/usb/devices"
        if not os.path.exists(base):
            return None

        for dev in os.listdir(base):
            dev_path = os.path.join(base, dev)
            vid_path = os.path.join(dev_path, "idVendor")
            pid_path = os.path.join(dev_path, "idProduct")
            if not (os.path.exists(vid_path) and os.path.exists(pid_path)):
                continue
            try:
                vid = open(vid_path).read().strip().lower()
                pid = open(pid_path).read().strip().lower()
            except OSError:
                continue
            if vid == DVSI_VID and pid == DVSI_PID:
                # Found the USB device — now find the ttyUSB node under it
                tty = self._resolve_tty(dev_path)
                if tty:
                    return tty
        return None

    def _resolve_tty(self, usb_dev_path: str) -> Optional[str]:
        """Search recursively under a USB device sysfs path for ttyUSB*."""
        for root, dirs, files in os.walk(usb_dev_path):
            for d in dirs:
                if d.startswith("ttyUSB"):
                    return f"/dev/{d}"
        return None

    def _find_via_ttyusb_scan(self) -> Optional[str]:
        """
        Fallback: open each /dev/ttyUSB* and try the product ID query.
        Slower but works without sysfs (e.g. some embedded distros).
        """
        for port in sorted(glob.glob("/dev/ttyUSB*")):
            version = self._verify_dvsi(port)
            if version:
                return port
        return None

    def _verify_dvsi(self, port: str) -> Optional[str]:
        """
        Open the serial port, send a product ID request, check the response.
        Returns version string on success, None on any failure.
        """
        try:
            import serial
        except ImportError:
            log.warning("pyserial not installed — cannot verify DVSI dongle")
            return None

        ser = None
        try:
            ser = serial.Serial(
                port=port,
                baudrate=DVSI_BAUD,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.2,
                xonxoff=False,
                rtscts=False,
            )
            ser.reset_input_buffer()
            ser.write(_PRODID_PACKET)
            ser.flush()
            time.sleep(0.05)
            response = ser.read(64)
            if _PRODID_RESPONSE_MARKER in response:
                # Extract version string — starts after "AMBE" marker
                idx = response.index(_PRODID_RESPONSE_MARKER)
                version_bytes = response[idx:idx + 20].split(b"\x00")[0]
                return version_bytes.decode("ascii", errors="replace").strip()
            return None
        except Exception as e:
            log.debug("Serial probe %s failed: %s", port, e)
            return None
        finally:
            if ser and ser.is_open:
                ser.close()
