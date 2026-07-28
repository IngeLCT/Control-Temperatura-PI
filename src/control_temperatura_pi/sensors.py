from __future__ import annotations

import time
from typing import Callable, Protocol


def select_device_by_name(devices: list[object], expected_name: str) -> object | None:
    if not expected_name:
        return None
    return next(
        (
            device
            for device in devices
            if str(getattr(device, "name", "")) == expected_name
        ),
        None,
    )


class TemperatureSensor(Protocol):
    def read_temperature_c(self) -> float: ...

    def close(self) -> None: ...


class SimulatedTemperatureSensor:
    def __init__(
        self,
        duty_provider: Callable[[], float],
        ambient_temperature_c: float,
        heating_rate_c_per_s: float,
        cooling_coefficient_per_s: float,
    ) -> None:
        self._duty_provider = duty_provider
        self._ambient = ambient_temperature_c
        self._heating_rate = heating_rate_c_per_s
        self._cooling_coefficient = cooling_coefficient_per_s
        self._temperature = ambient_temperature_c
        self._last_update = time.monotonic()

    def read_temperature_c(self) -> float:
        now = time.monotonic()
        dt = now - self._last_update
        self._last_update = now
        duty_fraction = self._duty_provider() / 100.0
        heating = self._heating_rate * duty_fraction
        cooling = self._cooling_coefficient * (self._temperature - self._ambient)
        self._temperature += (heating - cooling) * dt
        return self._temperature

    def close(self) -> None:
        pass


class VernierGDXTCASensor:
    """Lectura directa del canal predeterminado (tipo K) del GDX-TCA."""

    def __init__(
        self,
        connection: str,
        sample_period_ms: int,
        ble_backend: str = "native",
        ble_com_port: str = "",
        device_name: str = "",
    ) -> None:
        try:
            from godirect import GoDirect
        except ImportError as error:
            raise RuntimeError("La biblioteca 'godirect' no está instalada") from error

        self._godirect = GoDirect(
            use_usb=connection == "usb",
            use_ble=connection == "ble",
            use_ble_bg=connection == "ble" and ble_backend == "bluegiga",
            ble_com_port=ble_com_port or None,
        )
        if device_name:
            devices = self._godirect.list_devices()
            self._device = select_device_by_name(devices, device_name)
        else:
            self._device = self._godirect.get_device(threshold=-100)
        if self._device is None:
            self._godirect.quit()
            detail = f" con nombre '{device_name}'" if device_name else ""
            raise RuntimeError(f"No se encontró un sensor Vernier Go Direct{detail}")
        if not self._device.open(auto_start=False):
            self._godirect.quit()
            raise RuntimeError("No fue posible abrir el sensor Vernier")

        self._device.start(period=sample_period_ms)
        sensors = self._device.get_enabled_sensors()
        if not sensors:
            self.close()
            raise RuntimeError("El Vernier no habilitó su canal de temperatura")
        self._sensor = sensors[0]

    def read_temperature_c(self) -> float:
        if not self._device.read():
            raise RuntimeError("No se recibió una lectura del Vernier GDX-TCA")
        if not self._sensor.values:
            raise RuntimeError("La lectura Vernier llegó sin valores")
        value = float(self._sensor.values[-1])
        self._sensor.clear()
        return value

    def close(self) -> None:
        device = getattr(self, "_device", None)
        godirect = getattr(self, "_godirect", None)
        if device is not None:
            try:
                self._device.stop()
            except Exception:
                pass
            try:
                self._device.close()
            except Exception:
                pass
        if godirect is not None:
            try:
                self._godirect.quit()
            except Exception:
                pass
