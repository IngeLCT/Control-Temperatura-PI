import unittest
from types import SimpleNamespace

from control_temperatura_pi.sensors import select_device_by_name


class DeviceSelectionTests(unittest.TestCase):
    def test_selects_only_exact_configured_sensor(self) -> None:
        devices = [
            SimpleNamespace(name="GDX-TCA OTRO"),
            SimpleNamespace(name="GDX-TCA 1C1002R9"),
        ]
        selected = select_device_by_name(devices, "GDX-TCA 1C1002R9")
        self.assertIs(selected, devices[1])

    def test_does_not_fall_back_to_another_sensor(self) -> None:
        devices = [SimpleNamespace(name="GDX-TCA OTRO")]
        selected = select_device_by_name(devices, "GDX-TCA 1C1002R9")
        self.assertIsNone(selected)


if __name__ == "__main__":
    unittest.main()
