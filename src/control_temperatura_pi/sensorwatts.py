from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.request import urlopen


@dataclass(frozen=True)
class SensorWattsReading:
    voltage_v: float
    current_a: float
    power_factor: float
    active_power_w: float


def parse_sensorwatts_reading(payload: object) -> SensorWattsReading:
    if not isinstance(payload, dict):
        raise ValueError("SensorWatts no entregó un objeto JSON")

    required_fields = {
        "voltaje": "voltaje",
        "corriente": "corriente",
        "factorpot": "factor de potencia",
        "potencia": "potencia activa",
    }
    values: dict[str, float] = {}
    for field, description in required_fields.items():
        if field not in payload:
            raise ValueError(f"SensorWatts no entregó el campo '{field}'")
        try:
            values[field] = float(payload[field])
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"El valor de {description} de SensorWatts no es numérico"
            ) from error

    return SensorWattsReading(
        voltage_v=values["voltaje"],
        current_a=values["corriente"],
        power_factor=values["factorpot"],
        active_power_w=values["potencia"],
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
