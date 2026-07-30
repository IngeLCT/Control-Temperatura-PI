import unittest
from types import SimpleNamespace
from unittest.mock import patch

from control_temperatura_pi.sensors import (
    discover_vernier_device_names,
    filter_device_names,
    select_device_by_name,
)


class DeviceSelectionTests(unittest.TestCase):
    def test_filters_only_unique_names_beginning_with_gdx(self) -> None:
        devices = [
            SimpleNamespace(name="GDX-TCA 1C1002R9"),
            SimpleNamespace(name="SensorWatts"),
            SimpleNamespace(name="GDX-FOR 071000U9"),
            SimpleNamespace(name="GDX-TCA 1C1002R9"),
            SimpleNamespace(name=None),
        ]
        self.assertEqual(
            filter_device_names(devices),
            ["GDX-TCA 1C1002R9", "GDX-FOR 071000U9"],
        )

    def test_gdx_filter_is_case_sensitive(self) -> None:
        devices = [
            SimpleNamespace(name="gdx-tca no autorizado"),
            SimpleNamespace(name="GDX-TCA AUTORIZADO"),
        ]
        self.assertEqual(
            filter_device_names(devices),
            ["GDX-TCA AUTORIZADO"],
        )

    def test_discovery_lists_names_without_opening_a_device(self) -> None:
        calls: dict[str, object] = {}

        class FakeGoDirect:
            def __init__(self, **kwargs) -> None:
                calls["init"] = kwargs

            def list_devices(self) -> list[object]:
                calls["listed"] = True
                return [
                    SimpleNamespace(name="GDX-TCA 1C1002R9"),
                    SimpleNamespace(name="OTRO DISPOSITIVO"),
                ]

            def quit(self) -> None:
                calls["quit"] = True

        fake_module = SimpleNamespace(GoDirect=FakeGoDirect)
        with patch.dict("sys.modules", {"godirect": fake_module}):
            names = discover_vernier_device_names(
                connection="ble",
                ble_backend="native",
            )

        self.assertEqual(names, ["GDX-TCA 1C1002R9"])
        self.assertEqual(
            calls["init"],
            {
                "use_usb": False,
                "use_ble": True,
                "use_ble_bg": False,
                "ble_com_port": None,
            },
        )
        self.assertTrue(calls["listed"])
        self.assertTrue(calls["quit"])

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
