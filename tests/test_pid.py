import unittest

from control_temperatura_pi.pid import PIDController


class PIDControllerTests(unittest.TestCase):
    def test_output_is_limited_to_configured_range(self) -> None:
        pid = PIDController(kp=10, ki=1, kd=0, output_min=0, output_max=100)
        self.assertEqual(pid.update(setpoint=100, measurement=0, dt=1), 100)
        pid.reset()
        self.assertEqual(pid.update(setpoint=0, measurement=100, dt=1), 0)

    def test_integral_recovers_after_saturation(self) -> None:
        pid = PIDController(kp=2, ki=1, kd=0, output_min=0, output_max=100)
        for _ in range(20):
            self.assertEqual(pid.update(setpoint=100, measurement=0, dt=1), 100)
        self.assertAlmostEqual(pid.update(setpoint=20, measurement=20, dt=1), 0)

    def test_rejects_non_positive_time_step(self) -> None:
        pid = PIDController(kp=1, ki=0, kd=0)
        with self.assertRaises(ValueError):
            pid.update(setpoint=10, measurement=0, dt=0)


if __name__ == "__main__":
    unittest.main()
