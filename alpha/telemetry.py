"""Live readings for the HUD. Every number here is measured from this computer."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    cpu: float
    memory: float
    disk: float
    battery: float | None
    charging: bool
    down_kb: float
    up_kb: float


class Telemetry:
    def __init__(self):
        import psutil

        self._psutil = psutil
        self._drive = os.environ.get("SystemDrive", "C:") + os.sep
        self._last_net = psutil.net_io_counters()
        self._last_time = time.monotonic()
        psutil.cpu_percent(None)

    def read(self) -> Reading:
        psutil = self._psutil
        now = time.monotonic()
        net = psutil.net_io_counters()
        seconds = max(0.001, now - self._last_time)
        down = (net.bytes_recv - self._last_net.bytes_recv) / 1024 / seconds
        up = (net.bytes_sent - self._last_net.bytes_sent) / 1024 / seconds
        self._last_net, self._last_time = net, now
        battery = psutil.sensors_battery()
        return Reading(
            cpu=psutil.cpu_percent(None),
            memory=psutil.virtual_memory().percent,
            disk=psutil.disk_usage(self._drive).percent,
            battery=None if battery is None else float(battery.percent),
            charging=bool(battery and battery.power_plugged),
            down_kb=max(0.0, down),
            up_kb=max(0.0, up),
        )


def speed_text(kilobytes: float) -> str:
    if kilobytes >= 1024:
        return f"{kilobytes / 1024:.1f} MB/s"
    return f"{kilobytes:.0f} KB/s"
