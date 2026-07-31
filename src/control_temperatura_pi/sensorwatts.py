from __future__ import annotations

from dataclasses import dataclass
import json
import math
from urllib.request import urlopen


@dataclass(frozen=True)
class SensorWattsReading:
    voltage_v: float | None
    current_a: float | None
    active_power_w: float | None


def _optional_finite_float(payload: dict, field: str) -> float | None:
    try:
        value = float(payload[field])
    except (KeyError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def parse_sensorwatts_reading(payload: object) -> SensorWattsReading:
    if not isinstance(payload, dict):
        raise ValueError("SensorWatts no entregó un objeto JSON")

    return SensorWattsReading(
        voltage_v=_optional_finite_float(payload, "voltaje"),
        current_a=_optional_finite_float(payload, "corriente"),
        active_power_w=_optional_finite_float(payload, "potencia"),
    )


class SensorWattsClient:
    def __init__(self, url: str, timeout_s: float = 2.0) -> None:
        self._url = url
        self._timeout_s = timeout_s

    def read(self) -> SensorWattsReading:
        with urlopen(self._url, timeout=self._timeout_s) as response:
            encoding = response.headers.get_content_charset() or "utf-8"
            payload = json.loads(response.read().decode(encoding))
        return parse_sensorwatts_reading(payload)
