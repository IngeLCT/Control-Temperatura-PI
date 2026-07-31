import tempfile
import unittest
from pathlib import Path

from scripts.probar_control_fase import (
    chart_display_series,
    load_temperature_chart_csv,
)


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


if __name__ == "__main__":
    unittest.main()
