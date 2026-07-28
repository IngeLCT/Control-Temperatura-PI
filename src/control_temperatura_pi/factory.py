from __future__ import annotations

from .config import AppConfig
from .controller import TemperatureController
from .pid import PIDController
from .pwm import GPIOZeroPWMOutput, SimulatedPWMOutput
from .sensors import SimulatedTemperatureSensor, VernierGDXTCASensor


def build_controller(config: AppConfig) -> TemperatureController:
    if config.pwm.backend == "gpiozero":
        pwm = GPIOZeroPWMOutput(
            bcm_pin=config.pwm.bcm_pin,
            frequency_hz=config.pwm.frequency_hz,
            active_high=config.pwm.active_high,
        )
    else:
        pwm = SimulatedPWMOutput()

    try:
        if config.sensor.backend == "vernier":
            sensor = VernierGDXTCASensor(
                connection=config.sensor.connection,
                sample_period_ms=config.sensor.sample_period_ms,
                ble_backend=config.sensor.ble_backend,
                ble_com_port=config.sensor.ble_com_port,
                device_name=config.sensor.device_name,
            )
        else:
            simulation = config.simulation
            sensor = SimulatedTemperatureSensor(
                duty_provider=lambda: pwm.duty_percent,
                ambient_temperature_c=simulation.ambient_temperature_c,
                heating_rate_c_per_s=simulation.heating_rate_c_per_s,
                cooling_coefficient_per_s=simulation.cooling_coefficient_per_s,
            )
    except Exception:
        pwm.close()
        raise

    pid_config = config.pid
    pid = PIDController(
        kp=pid_config.kp,
        ki=pid_config.ki,
        kd=pid_config.kd,
        output_min=pid_config.output_min_percent,
        output_max=pid_config.output_max_percent,
    )
    return TemperatureController(sensor, pwm, pid, config.control)
