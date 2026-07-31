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

    def test_missing_field_does_not_discard_valid_fields(self) -> None:
        reading = parse_sensorwatts_reading(
            {
                "voltaje": "123.20",
                "corriente": "0.12",
            }
        )
        self.assertEqual(reading.voltage_v, 123.20)
        self.assertEqual(reading.current_a, 0.12)
        self.assertIsNone(reading.active_power_w)

    def test_invalid_voltage_preserves_current_and_power(self) -> None:
        reading = parse_sensorwatts_reading(
            {
                "voltaje": "reiniciando",
                "corriente": "1.25",
                "potencia": "145.8",
            }
        )
        self.assertIsNone(reading.voltage_v)
        self.assertEqual(reading.current_a, 1.25)
        self.assertEqual(reading.active_power_w, 145.8)

    def test_non_finite_values_are_invalid_independently(self) -> None:
        reading = parse_sensorwatts_reading(
            {
                "voltaje": "nan",
                "corriente": "inf",
                "potencia": "10.5",
            }
        )
        self.assertIsNone(reading.voltage_v)
        self.assertIsNone(reading.current_a)
        self.assertEqual(reading.active_power_w, 10.5)

    def test_rejects_non_object_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "objeto JSON"):
            parse_sensorwatts_reading([])

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
