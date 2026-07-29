import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from control_temperatura_pi.pwm import (
    GPIOZeroPWMOutput,
    logical_to_physical_duty,
)


class FakePWMOutputDevice:
    def __init__(self, **kwargs) -> None:
        self.initial_value = kwargs["initial_value"]
        self.value = self.initial_value
        self.closed = False

    def close(self) -> None:
        self.closed = True


class PhaseControlMappingTests(unittest.TestCase):
    def test_maps_off_to_safe_physical_high(self) -> None:
        self.assertEqual(logical_to_physical_duty(0, 80), 100)

    def test_skips_dead_range_for_positive_demand(self) -> None:
        self.assertAlmostEqual(logical_to_physical_duty(1, 80), 79.2)
        self.assertAlmostEqual(logical_to_physical_duty(50, 80), 40.0)
        self.assertAlmostEqual(logical_to_physical_duty(100, 80), 0.0)

    def test_real_output_starts_and_closes_in_safe_state(self) -> None:
        fake_gpiozero = SimpleNamespace(PWMOutputDevice=FakePWMOutputDevice)
        with patch.dict(sys.modules, {"gpiozero": fake_gpiozero}):
            output = GPIOZeroPWMOutput(
                bcm_pin=18,
                frequency_hz=1000,
                active_high=True,
                active_duty_ceiling_percent=80,
            )
            self.assertEqual(output._device.initial_value, 1.0)
            self.assertEqual(output.physical_duty_percent, 100.0)

            output.set_duty_percent(50)
            self.assertEqual(output.duty_percent, 50)
            self.assertAlmostEqual(output.physical_duty_percent, 40)
            self.assertAlmostEqual(output._device.value, 0.4)

            output.close()
            self.assertEqual(output.duty_percent, 0)
            self.assertEqual(output.physical_duty_percent, 100)
            self.assertEqual(output._device.value, 1.0)
            self.assertTrue(output._device.closed)


if __name__ == "__main__":
    unittest.main()
