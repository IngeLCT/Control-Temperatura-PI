from __future__ import annotations

from dataclasses import dataclass, replace
import math
from threading import Event, Lock, Thread
import time

from .config import ControlConfig
from .pid import PIDController
from .pwm import PWMOutput
from .sensors import TemperatureSensor


@dataclass(frozen=True)
class ControlState:
    setpoint_c: float
    temperature_c: float | None = None
    ambient_temperature_c: float | None = None
    duty_percent: float = 0.0
    enabled: bool = False
    sensor_connected: bool = False
    status: str = "Iniciando"
    fault: str | None = None
    sample_time: float | None = None


class TemperatureController:
    def __init__(
        self,
        sensor: TemperatureSensor,
        pwm: PWMOutput,
        pid: PIDController,
        config: ControlConfig,
    ) -> None:
        self._sensor = sensor
        self._pwm = pwm
        self._pid = pid
        self._config = config
        self._state = ControlState(setpoint_c=config.setpoint_initial_c)
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = Thread(target=self._run, name="temperature-control", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(3.0, self._config.sample_period_s * 2))
        self._pwm.close()
        self._sensor.close()

    def get_state(self) -> ControlState:
        with self._lock:
            return replace(self._state)

    def set_setpoint(self, value_c: float) -> None:
        with self._lock:
            minimum = (
                self._state.ambient_temperature_c
                if self._state.ambient_temperature_c is not None
                else self._config.setpoint_min_c
            )
            bounded = min(
                self._config.setpoint_max_c,
                max(minimum, float(value_c)),
            )
            self._state = replace(self._state, setpoint_c=bounded)

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            can_enable = self._state.sensor_connected and self._state.fault is None
            actual_enabled = bool(enabled and can_enable)
            self._state = replace(
                self._state,
                enabled=actual_enabled,
                status="Control activo" if actual_enabled else "Control detenido",
            )
        if not actual_enabled:
            self._pid.reset()
            self._set_safe_output()

    def _run(self) -> None:
        last_loop = time.monotonic()
        while not self._stop_event.is_set():
            loop_start = time.monotonic()
            dt = max(loop_start - last_loop, self._config.sample_period_s)
            last_loop = loop_start
            try:
                temperature = float(self._sensor.read_temperature_c())
                if not math.isfinite(temperature):
                    raise ValueError("El sensor entregó un valor no finito")
                self._process_temperature(temperature, dt)
            except Exception as error:
                self._trip_fault(f"Fallo del sensor: {error}")
            elapsed = time.monotonic() - loop_start
            self._stop_event.wait(max(0.0, self._config.sample_period_s - elapsed))

        self._set_safe_output()

    def _process_temperature(self, temperature: float, dt: float) -> None:
        if temperature >= self._config.maximum_safe_temperature_c:
            self._trip_fault(
                f"Temperatura {temperature:.1f} °C igual o superior al límite seguro"
            )
            return

        with self._lock:
            ambient = self._state.ambient_temperature_c
            if ambient is None:
                ambient = temperature
                self._state = replace(
                    self._state,
                    ambient_temperature_c=ambient,
                    setpoint_c=max(self._state.setpoint_c, ambient),
                )
            enabled = self._state.enabled
            setpoint = self._state.setpoint_c

        ambient_mode = (
            enabled
            and setpoint <= ambient + self._config.ambient_off_tolerance_c
        )
        if ambient_mode:
            self._pid.reset()
            duty = 0.0
        else:
            duty = self._pid.update(setpoint, temperature, dt) if enabled else 0.0
        self._pwm.set_duty_percent(duty)
        with self._lock:
            self._state = replace(
                self._state,
                temperature_c=temperature,
                duty_percent=duty,
                sensor_connected=True,
                status=(
                    "Objetivo ambiente; salida apagada"
                    if ambient_mode
                    else "Control activo"
                    if enabled
                    else "Listo; control detenido"
                ),
                fault=None,
                sample_time=time.time(),
            )

    def _trip_fault(self, message: str) -> None:
        self._pid.reset()
        self._set_safe_output()
        with self._lock:
            self._state = replace(
                self._state,
                enabled=False,
                duty_percent=0.0,
                sensor_connected=False,
                status="Fallo de seguridad",
                fault=message,
            )

    def _set_safe_output(self) -> None:
        self._pwm.set_duty_percent(0.0)
