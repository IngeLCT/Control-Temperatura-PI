import unittest

from scripts.probar_control_fase import (
    CONTROLLED_TEST_DEFAULT_STEP_MINUTES,
    controlled_test_duration_minutes,
    controlled_test_levels,
    controlled_test_step_seconds,
)


class ControlledTestSequenceTests(unittest.TestCase):
    def test_sequence_rises_to_100_and_returns_to_10(self) -> None:
        self.assertEqual(
            controlled_test_levels(),
            (
                10,
                20,
                30,
                40,
                50,
                60,
                70,
                80,
                90,
                100,
                90,
                80,
                70,
                60,
                50,
                40,
                30,
                20,
                10,
            ),
        )

    def test_sequence_supports_5_percent_steps(self) -> None:
        levels = controlled_test_levels(5)
        self.assertEqual(levels[0], 5)
        self.assertEqual(levels[19], 100)
        self.assertEqual(levels[-1], 5)
        self.assertEqual(len(levels), 39)

    def test_sequence_supports_20_percent_steps(self) -> None:
        self.assertEqual(
            controlled_test_levels(20),
            (20, 40, 60, 80, 100, 80, 60, 40, 20),
        )

    def test_sequence_rejects_steps_that_do_not_divide_100(self) -> None:
        with self.assertRaises(ValueError):
            controlled_test_levels(30)

    def test_sequence_duration_is_57_minutes(self) -> None:
        self.assertEqual(
            controlled_test_duration_minutes(
                CONTROLLED_TEST_DEFAULT_STEP_MINUTES
            ),
            57,
        )

    def test_sequence_duration_uses_selected_minutes_per_step(self) -> None:
        self.assertEqual(controlled_test_duration_minutes(1), 19)
        self.assertEqual(controlled_test_duration_minutes(2), 38)
        self.assertEqual(controlled_test_duration_minutes(5), 95)
        self.assertEqual(controlled_test_duration_minutes(1, 5), 39)
        self.assertEqual(controlled_test_duration_minutes(1, 20), 9)

    def test_decimal_minutes_are_converted_to_seconds(self) -> None:
        self.assertEqual(controlled_test_step_seconds(0.5), 30)
        self.assertEqual(controlled_test_step_seconds(1.5), 90)
        self.assertEqual(controlled_test_duration_minutes(0.5), 9.5)


if __name__ == "__main__":
    unittest.main()
