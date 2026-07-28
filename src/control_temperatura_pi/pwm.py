from __future__ import annotations

from threading import Lock
from typing import Protocol


class PWMOutput(Protocol):
    @property
    def duty_percent(self) -> float: ...

    def set_duty_percent(self, duty_percent: float) -> None: ...

    def close(self) -> None: ...


class SimulatedPWMOutput:
    def __init__(self) -> None:
        self._duty_percent = 0.0
        self._lock = Lock()

    @property
    def duty_percent(self) -> float:
        with self._lock:
            return self._duty_percent

    def set_duty_percent(self, duty_percent: float) -> None:
        with self._lock:
            self._duty_percent = min(100.0, max(0.0, duty_percent))

    def close(self) -> None:
        self.set_duty_percent(0.0)


class GPIOZeroPWMOutput:
    def __init__(
        self,
        bcm_pin: int,
        frequency_hz: float,
        active_high: bool,
    ) -> None:
        try:
            from gpiozero import PWMOutputDevice
        except ImportError as error:
            raise RuntimeError("gpiozero no está instalado") from error

        self._device = PWMOutputDevice(
            pin=bcm_pin,
            active_high=active_high,
            initial_value=0.0,
            frequency=frequency_hz,
        )
        self._duty_percent = 0.0

    @property
    def duty_percent(self) -> float:
        return self._duty_percent

    def set_duty_percent(self, duty_percent: float) -> None:
        self._duty_percent = min(100.0, max(0.0, duty_percent))
        self._device.value = self._duty_percent / 100.0

    def close(self) -> None:
        self.set_duty_percent(0.0)
        self._device.close()
