import unittest

from scripts.probar_control_fase import (
    CONTROLLED_TEST_STEP_SECONDS,
    controlled_test_levels,
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

    def test_sequence_duration_is_57_minutes(self) -> None:
        total_seconds = (
            len(controlled_test_levels()) * CONTROLLED_TEST_STEP_SECONDS
        )
        self.assertEqual(total_seconds, 57 * 60)


if __name__ == "__main__":
    unittest.main()
