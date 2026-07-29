from __future__ import annotations

from threading import Lock
from typing import Protocol


def logical_to_physical_duty(
    logical_duty_percent: float,
    active_duty_ceiling_percent: float,
) -> float:
    """Convierte demanda térmica lógica en el duty físico inverso.

    Cero lógico usa 100 % físico como estado de apagado seguro. Para cualquier
    demanda positiva se omite el rango muerto y se escala desde el límite
    activo hasta 0 % físico.
    """
    if not 0.0 < active_duty_ceiling_percent < 100.0:
        raise ValueError(
            "active_duty_ceiling_percent debe estar entre 0 y 100"
        )
    logical = min(100.0, max(0.0, float(logical_duty_percent)))
    if logical == 0.0:
        return 100.0
    return active_duty_ceiling_percent * (1.0 - logical / 100.0)


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
        active_duty_ceiling_percent: float,
    ) -> None:
        if not active_high:
            raise ValueError(
                "La etapa invertida requiere pwm.active_high = true"
            )
        logical_to_physical_duty(0.0, active_duty_ceiling_percent)
        try:
            from gpiozero import PWMOutputDevice
        except ImportError as error:
            raise RuntimeError("gpiozero no está instalado") from error

        self._device = PWMOutputDevice(
            pin=bcm_pin,
            active_high=active_high,
            initial_value=1.0,
            frequency=frequency_hz,
        )
        self._duty_percent = 0.0
        self._physical_duty_percent = 100.0
        self._active_duty_ceiling_percent = active_duty_ceiling_percent

    @property
    def duty_percent(self) -> float:
        return self._duty_percent

    @property
    def physical_duty_percent(self) -> float:
        return self._physical_duty_percent

    def set_duty_percent(self, duty_percent: float) -> None:
        self._duty_percent = min(100.0, max(0.0, duty_percent))
        self._physical_duty_percent = logical_to_physical_duty(
            self._duty_percent,
            self._active_duty_ceiling_percent,
        )
        self._device.value = self._physical_duty_percent / 100.0

    def close(self) -> None:
        self.set_duty_percent(0.0)
        self._device.close()
