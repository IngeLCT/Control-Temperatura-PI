from __future__ import annotations

import argparse
from pathlib import Path

from control_temperatura_pi.config import load_config
from control_temperatura_pi.sensors import VernierGDXTCASensor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prueba aislada del Vernier GDX-TCA sin crear salida PWM"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="Archivo TOML del proyecto",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=10,
        help="Cantidad de lecturas",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.samples <= 0:
        raise SystemExit("--samples debe ser mayor que cero")

    config = load_config(args.config)
    sensor_config = config.sensor
    print(f"Buscando: {sensor_config.device_name}")
    print(
        f"Conexión: {sensor_config.connection}; "
        f"backend BLE: {sensor_config.ble_backend}"
    )
    print("Esta prueba no crea GPIO ni salida PWM.")

    sensor: VernierGDXTCASensor | None = None
    try:
        sensor = VernierGDXTCASensor(
            connection=sensor_config.connection,
            sample_period_ms=sensor_config.sample_period_ms,
            ble_backend=sensor_config.ble_backend,
            ble_com_port=sensor_config.ble_com_port,
            device_name=sensor_config.device_name,
        )
        for sample_number in range(1, args.samples + 1):
            temperature = sensor.read_temperature_c()
            print(f"{sample_number:02d}: {temperature:.2f} °C", flush=True)
    except KeyboardInterrupt:
        print("\nPrueba interrumpida.")
    finally:
        if sensor is not None:
            sensor.close()
            print("Sensor desconectado.")


if __name__ == "__main__":
    main()
