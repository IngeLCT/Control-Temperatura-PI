from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "no instalado"


def command_output(command: list[str]) -> str:
    if shutil.which(command[0]) is None:
        return f"{command[0]} no está instalado"
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"{' '.join(command)} excedió 5 segundos"
    output = (result.stdout or result.stderr).strip()
    return output or f"sin salida (código {result.returncode})"


def main() -> None:
    model_path = Path("/proc/device-tree/model")
    model = (
        model_path.read_text(errors="replace").rstrip("\x00")
        if model_path.exists()
        else "no disponible"
    )

    print(f"Modelo: {model}")
    print(f"Sistema: {platform.platform()}")
    print(f"Arquitectura: {platform.machine()}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"NiceGUI: {package_version('nicegui')}")
    print(f"godirect: {package_version('godirect')}")
    print(f"gpiozero: {package_version('gpiozero')}")
    print(f"bleak: {package_version('bleak')}")
    print(f"hidapi: {package_version('hidapi')}")
    print("\nAdaptadores USB:")
    print(command_output(["lsusb"]))
    print("\nControladores Bluetooth:")
    print(command_output(["bluetoothctl", "list"]))
    print("\nEstado del servicio Bluetooth:")
    print(command_output(["systemctl", "is-active", "bluetooth"]))
    print("\nBloqueos de radio:")
    print(command_output(["rfkill", "list", "bluetooth"]))


if __name__ == "__main__":
    main()
