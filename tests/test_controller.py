import time
import unittest

from control_temperatura_pi.config import ControlConfig
from control_temperatura_pi.controller import TemperatureController
from control_temperatura_pi.pid import PIDController
from control_temperatura_pi.pwm import SimulatedPWMOutput


class FixedSensor:
    def __init__(self, temperature: float) -> None:
        self.temperature = temperature

    def read_temperature_c(self) -> float:
        return self.temperature

    def close(self) -> None:
        pass


def control_config(maximum_safe_temperature_c: float = 80.0) -> ControlConfig:
    return ControlConfig(
        sample_period_s=0.01,
        setpoint_initial_c=40.0,
        setpoint_min_c=20.0,
        setpoint_max_c=70.0,
        maximum_safe_temperature_c=maximum_safe_temperature_c,
        ambient_off_tolerance_c=0.5,
    )


class TemperatureControllerTests(unittest.TestCase):
    def test_control_starts_disabled_and_output_is_zero(self) -> None:
        pwm = SimulatedPWMOutput()
        controller = TemperatureController(
            FixedSensor(25.0),
            pwm,
            PIDController(2, 0, 0),
            control_config(),
        )
        controller.start()
        time.sleep(0.03)
        state = controller.get_state()
        controller.stop()
        self.assertTrue(state.sensor_connected)
        self.assertFalse(state.enabled)
        self.assertEqual(pwm.duty_percent, 0)

    def test_overtemperature_disables_output(self) -> None:
        pwm = SimulatedPWMOutput()
        controller = TemperatureController(
            FixedSensor(90.0),
            pwm,
            PIDController(2, 0, 0),
            control_config(),
        )
        controller.start()
        time.sleep(0.03)
        state = controller.get_state()
        controller.stop()
        self.assertIsNotNone(state.fault)
        self.assertFalse(state.enabled)
        self.assertEqual(pwm.duty_percent, 0)

    def test_ambient_setpoint_keeps_output_off(self) -> None:
        pwm = SimulatedPWMOutput()
        controller = TemperatureController(
            FixedSensor(25.0),
            pwm,
            PIDController(2, 0.1, 0),
            control_config(),
        )
        controller.start()
        time.sleep(0.03)
        controller.set_setpoint(25.0)
        controller.set_enabled(True)
        time.sleep(0.03)
        state = controller.get_state()
        controller.stop()
        self.assertTrue(state.enabled)
        self.assertEqual(state.duty_percent, 0)
        self.assertIn("ambiente", state.status.lower())


if __name__ == "__main__":
    unittest.main()
