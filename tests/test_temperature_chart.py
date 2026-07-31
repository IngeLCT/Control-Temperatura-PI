import tempfile
import unittest
from pathlib import Path

from scripts.probar_control_fase import (
    chart_display_series,
    load_temperature_chart_csv,
    sensorwatts_csv_values,
)
from control_temperatura_pi.sensorwatts import SensorWattsReading


class TemperatureChartTests(unittest.TestCase):
    def test_display_series_limits_points_and_preserves_ends(self) -> None:
        times = [float(value) for value in range(20)]
        temperatures = [float(value + 20) for value in range(20)]

        shown_times, shown_temperatures = chart_display_series(
            times,
            temperatures,
            max_points=5,
        )

        self.assertEqual(len(shown_times), 5)
        self.assertEqual(shown_times[0], 0.0)
        self.assertEqual(shown_times[-1], 19.0)
        self.assertEqual(shown_temperatures[0], 20.0)
        self.assertEqual(shown_temperatures[-1], 39.0)

    def test_loads_time_and_temperature_from_saved_csv(self) -> None:
        content = (
            "Tiempo_s,Temperatura_C,Voltaje_V\n"
            "0.0,24.50,127.0\n"
            "1.0,,127.1\n"
            "2.0,25.10,127.2\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registro.csv"
            path.write_text(content, encoding="utf-8-sig")

            times, temperatures = load_temperature_chart_csv(path)

        self.assertEqual(times, [0.0, 1.0, 2.0])
        self.assertEqual(temperatures, [24.5, None, 25.1])

    def test_invalid_sensorwatts_sample_leaves_electrical_fields_empty(
        self,
    ) -> None:
        self.assertEqual(
            sensorwatts_csv_values(None),
            {
                "Voltaje_V": "",
                "Corriente_A": "",
                "Potencia_Activa_W": "",
            },
        )

    def test_valid_sensorwatts_sample_is_formatted(self) -> None:
        reading = SensorWattsReading(
            voltage_v=123.456,
            current_a=1.2345,
            active_power_w=98.765,
        )
        self.assertEqual(
            sensorwatts_csv_values(reading),
            {
                "Voltaje_V": "123.46",
                "Corriente_A": "1.234",
                "Potencia_Activa_W": "98.77",
            },
        )

    def test_partial_sensorwatts_sample_keeps_valid_fields(self) -> None:
        reading = SensorWattsReading(
            voltage_v=None,
            current_a=2.3456,
            active_power_w=210.987,
        )
        self.assertEqual(
            sensorwatts_csv_values(reading),
            {
                "Voltaje_V": "",
                "Corriente_A": "2.346",
                "Potencia_Activa_W": "210.99",
            },
        )


if __name__ == "__main__":
    unittest.main()
