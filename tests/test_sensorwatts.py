import unittest

from control_temperatura_pi.sensorwatts import parse_sensorwatts_reading


class SensorWattsParsingTests(unittest.TestCase):
    def test_parses_real_endpoint_shape(self) -> None:
        reading = parse_sensorwatts_reading(
            {
                "voltaje": "123.20",
                "frecuencia": "59.95",
                "factorpot": "0.5709",
                "corriente": "0.12",
                "potencia": "8.29",
            }
        )

        self.assertEqual(reading.voltage_v, 123.20)
        self.assertEqual(reading.current_a, 0.12)
        self.assertEqual(reading.active_power_w, 8.29)

    def test_rejects_missing_required_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "potencia"):
            parse_sensorwatts_reading(
                {
                    "voltaje": "123.20",
                    "corriente": "0.12",
                }
            )

    def test_does_not_require_power_factor(self) -> None:
        reading = parse_sensorwatts_reading(
            {
                "voltaje": "123.20",
                "corriente": "0.12",
                "potencia": "8.29",
            }
        )
        self.assertEqual(reading.active_power_w, 8.29)


if __name__ == "__main__":
    unittest.main()
