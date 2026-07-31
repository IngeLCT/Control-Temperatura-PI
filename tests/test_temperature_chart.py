import os
import tempfile
import unittest
from pathlib import Path

from scripts.probar_control_fase import (
    chart_display_series,
    elapsed_time_axis_ticks,
    format_elapsed_mm_ss,
    list_saved_csv_paths,
    load_temperature_chart_csv,
    resolve_saved_csv_path,
    sensorwatts_csv_values,
    temperature_plotly_figure,
)
from control_temperatura_pi.sensorwatts import SensorWattsReading


class TemperatureChartTests(unittest.TestCase):
    def test_formats_elapsed_seconds_as_unbounded_minutes_and_seconds(self) -> None:
        self.assertEqual(format_elapsed_mm_ss(0.0), "00:00")
        self.assertEqual(format_elapsed_mm_ss(65.0), "01:05")
        self.assertEqual(format_elapsed_mm_ss(1000.0), "16:40")
        self.assertEqual(format_elapsed_mm_ss(3661.0), "61:01")

    def test_time_axis_uses_mm_ss_labels(self) -> None:
        values, labels = elapsed_time_axis_ticks([0.0, 500.0, 1000.0])

        self.assertEqual(len(values), len(labels))
        self.assertEqual(labels[0], "00:00")
        self.assertEqual(labels[-1], "16:40")

    def test_lists_saved_csv_files_newest_first_and_resolves_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            csv_dir = Path(directory)
            older = csv_dir / "anterior.csv"
            newer = csv_dir / "reciente.csv"
            ignored = csv_dir / "nota.txt"
            older.write_text("Tiempo_s,Temperatura_C\n", encoding="utf-8")
            newer.write_text("Tiempo_s,Temperatura_C\n", encoding="utf-8")
            ignored.write_text("no es csv", encoding="utf-8")
            os.utime(older, (100.0, 100.0))
            os.utime(newer, (200.0, 200.0))

            self.assertEqual(
                list_saved_csv_paths(csv_dir),
                [newer, older],
            )
            self.assertEqual(
                resolve_saved_csv_path(csv_dir, "anterior.csv"),
                older,
            )
            self.assertIsNone(
                resolve_saved_csv_path(csv_dir, "../anterior.csv")
            )
            self.assertIsNone(resolve_saved_csv_path(csv_dir, "nota.txt"))

    def test_builds_plotly_figure_for_temperature_series(self) -> None:
        figure = temperature_plotly_figure(
            [0.0, 1.0],
            [24.5, 25.0],
            session_revision=3,
        )

        trace = figure["data"][0]
        self.assertEqual(trace["type"], "scattergl")
        self.assertEqual(trace["x"], [0.0, 1.0])
        self.assertEqual(trace["y"], [24.5, 25.0])
        self.assertFalse(trace["connectgaps"])
        self.assertEqual(trace["customdata"], ["00:00", "00:01"])
        self.assertEqual(
            figure["layout"]["xaxis"]["title"],
            "Tiempo (MM:SS)",
        )
        self.assertEqual(
            figure["layout"]["uirevision"],
            "temperature-session-3",
        )

    def test_display_series_preserves_every_temperature_point(self) -> None:
        times = [float(value) for value in range(2500)]
        temperatures = [float(value + 20) for value in range(2500)]

        shown_times, shown_temperatures = chart_display_series(
            times,
            temperatures,
        )

        self.assertEqual(len(shown_times), 2500)
        self.assertEqual(shown_times[0], 0.0)
        self.assertEqual(shown_times[-1], 2499.0)
        self.assertEqual(shown_temperatures[0], 20.0)
        self.assertEqual(shown_temperatures[-1], 2519.0)

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
