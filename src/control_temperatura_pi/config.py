from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class ApplicationConfig:
    host: str
    port: int
    title: str


@dataclass(frozen=True)
class ControlConfig:
    sample_period_s: float
    setpoint_initial_c: float
    setpoint_min_c: float
    setpoint_max_c: float
    maximum_safe_temperature_c: float
    ambient_off_tolerance_c: float


@dataclass(frozen=True)
class PidConfig:
    kp: float
    ki: float
    kd: float
    output_min_percent: float
    output_max_percent: float


@dataclass(frozen=True)
class SensorConfig:
    backend: str
    connection: str
    sample_period_ms: int
    ble_backend: str
    ble_com_port: str
    device_name: str


@dataclass(frozen=True)
class PwmConfig:
    backend: str
    bcm_pin: int
    frequency_hz: float
    active_high: bool
    active_duty_ceiling_percent: float


@dataclass(frozen=True)
class SimulationConfig:
    ambient_temperature_c: float
    heating_rate_c_per_s: float
    cooling_coefficient_per_s: float


@dataclass(frozen=True)
class AppConfig:
    application: ApplicationConfig
    control: ControlConfig
    pid: PidConfig
    sensor: SensorConfig
    pwm: PwmConfig
    simulation: SimulationConfig


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open("rb") as config_file:
        raw = tomllib.load(config_file)

    config = AppConfig(
        application=ApplicationConfig(**raw["application"]),
        control=ControlConfig(**raw["control"]),
        pid=PidConfig(**raw["pid"]),
        sensor=SensorConfig(**raw["sensor"]),
        pwm=PwmConfig(**raw["pwm"]),
        simulation=SimulationConfig(**raw["simulation"]),
    )
    _validate(config)
    return config


def _validate(config: AppConfig) -> None:
    control = config.control
    pid = config.pid
    if control.sample_period_s <= 0:
        raise ValueError("control.sample_period_s debe ser mayor que cero")
    if not control.setpoint_min_c <= control.setpoint_initial_c <= control.setpoint_max_c:
        raise ValueError("La temperatura objetivo inicial está fuera del rango del slider")
    if control.maximum_safe_temperature_c <= control.setpoint_max_c:
        raise ValueError("El límite seguro debe ser mayor que el objetivo máximo")
    if control.ambient_off_tolerance_c < 0:
        raise ValueError("La tolerancia del modo ambiente no puede ser negativa")
    if pid.output_min_percent < 0 or pid.output_max_percent > 100:
        raise ValueError("Los límites PID deben estar dentro de 0 a 100 %")
    if pid.output_min_percent >= pid.output_max_percent:
        raise ValueError("El límite PID mínimo debe ser menor que el máximo")
    if config.sensor.backend not in {"simulated", "vernier"}:
        raise ValueError("sensor.backend debe ser 'simulated' o 'vernier'")
    if config.sensor.connection not in {"usb", "ble"}:
        raise ValueError("sensor.connection debe ser 'usb' o 'ble'")
    if config.sensor.ble_backend not in {"native", "bluegiga"}:
        raise ValueError("sensor.ble_backend debe ser 'native' o 'bluegiga'")
    if config.pwm.backend not in {"simulated", "gpiozero"}:
        raise ValueError("pwm.backend debe ser 'simulated' o 'gpiozero'")
    if not config.pwm.active_high:
        raise ValueError(
            "La etapa invertida requiere pwm.active_high = true"
        )
    if not 0 < config.pwm.active_duty_ceiling_percent < 100:
        raise ValueError(
            "pwm.active_duty_ceiling_percent debe estar entre 0 y 100"
        )
