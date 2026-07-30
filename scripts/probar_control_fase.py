from __future__ import annotations

import argparse
import threading
import time

from control_temperatura_pi.config import load_config
from control_temperatura_pi.pwm import (
    GPIOZeroPWMOutput,
    SimulatedPWMOutput,
    logical_to_physical_duty,
)
from control_temperatura_pi.sensors import VernierGDXTCASensor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prueba manual y aislada de la referencia PWM del control de fase"
        )
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Crear la salida física. Sin esta opción se usa una salida simulada.",
    )
    parser.add_argument(
        "--sensor",
        action="store_true",
        help=(
            "Conectar el Vernier configurado en config.toml solo para mostrar "
            "su temperatura; no modifica la potencia."
        ),
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Ruta de configuración usada por --sensor",
    )
    parser.add_argument("--pin", type=int, default=18, help="Pin en numeración BCM")
    parser.add_argument(
        "--frequency",
        type=float,
        default=1000.0,
        help="Frecuencia PWM en Hz",
    )
    parser.add_argument(
        "--max-duty",
        type=float,
        default=100.0,
        help="Límite superior del slider, entre 1 y 100 %%",
    )
    parser.add_argument(
        "--active-ceiling",
        type=float,
        default=80.0,
        help="Duty físico donde comienza la zona activa, entre 0 y 100 %%",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Dirección de escucha")
    parser.add_argument("--port", type=int, default=8081, help="Puerto web")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.pin < 0:
        raise SystemExit("--pin no puede ser negativo")
    if args.frequency <= 0:
        raise SystemExit("--frequency debe ser mayor que cero")
    if not 1.0 <= args.max_duty <= 100.0:
        raise SystemExit("--max-duty debe estar entre 1 y 100")
    if not 0.0 < args.active_ceiling < 100.0:
        raise SystemExit("--active-ceiling debe estar entre 0 y 100")
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port debe estar entre 1 y 65535")


def main() -> None:
    args = parse_args()
    validate_args(args)

    from nicegui import app, ui

    sensor = None
    sensor_thread = None
    sensor_stop = threading.Event()
    sensor_lock = threading.Lock()
    sensor_temperature_c: float | None = None
    sensor_error: str | None = None
    sensor_last_read = 0.0

    if args.sensor:
        sensor_config = load_config(args.config).sensor
        sensor = VernierGDXTCASensor(
            connection=sensor_config.connection,
            sample_period_ms=sensor_config.sample_period_ms,
            ble_backend=sensor_config.ble_backend,
            ble_com_port=sensor_config.ble_com_port,
            device_name=sensor_config.device_name,
        )

    try:
        pwm = (
            GPIOZeroPWMOutput(
                bcm_pin=args.pin,
                frequency_hz=args.frequency,
                active_high=True,
                active_duty_ceiling_percent=args.active_ceiling,
            )
            if args.real
            else SimulatedPWMOutput()
        )
    except Exception:
        if sensor is not None:
            sensor.close()
        raise

    if sensor is not None:
        def read_sensor() -> None:
            nonlocal sensor_temperature_c, sensor_error, sensor_last_read
            while not sensor_stop.is_set():
                try:
                    temperature = sensor.read_temperature_c()
                    with sensor_lock:
                        sensor_temperature_c = temperature
                        sensor_error = None
                        sensor_last_read = time.monotonic()
                except Exception as error:
                    with sensor_lock:
                        sensor_error = str(error)
                    if sensor_stop.wait(0.5):
                        break

        sensor_thread = threading.Thread(
            target=read_sensor,
            name="vernier-temperature-reference",
            daemon=True,
        )
        sensor_thread.start()

    lock = threading.Lock()
    enabled = False
    requested_duty = 0.0

    def set_output(duty_percent: float) -> float:
        nonlocal requested_duty
        with lock:
            requested_duty = min(args.max_duty, max(0.0, duty_percent))
            applied = requested_duty if enabled else 0.0
            pwm.set_duty_percent(applied)
            return applied

    def disable_output() -> None:
        nonlocal enabled, requested_duty
        with lock:
            enabled = False
            requested_duty = 0.0
            pwm.set_duty_percent(0.0)

    def close_hardware() -> None:
        disable_output()
        pwm.close()
        sensor_stop.set()
        if sensor is not None:
            sensor.close()
        if sensor_thread is not None:
            sensor_thread.join(timeout=2.0)

    app.on_shutdown(close_hardware)

    @ui.page("/")
    def index() -> None:
        nonlocal enabled
        client = ui.context.client

        ui.label("Prueba manual del control de fase").classes("text-h4 font-bold")
        mode = "GPIO REAL" if args.real else "SIMULACIÓN"
        mode_color = "text-negative" if args.real else "text-primary"
        ui.label(
            f"{mode} · BCM{args.pin} · {args.frequency:g} Hz"
        ).classes(f"text-h6 font-bold {mode_color}")
        ui.label(
            "La potencia se controla únicamente con el slider; "
            "la temperatura es solo una referencia y no utiliza PID."
        ).classes("text-subtitle1")

        with ui.card().classes("w-full max-w-2xl"):
            ui.label("Temperatura de referencia").classes("text-subtitle2")
            temperature_label = ui.label("--.- °C").classes("text-h3")
            sensor_status_label = ui.label(
                "CONECTANDO SENSOR..." if args.sensor else "SENSOR NO HABILITADO"
            ).classes("text-subtitle1 text-grey-7")
            ui.label(
                "Esta lectura no modifica la demanda térmica ni la salida PWM."
            ).classes("text-caption text-grey-7")

            def update_temperature() -> None:
                if not args.sensor:
                    return
                with sensor_lock:
                    temperature = sensor_temperature_c
                    error = sensor_error
                    last_read = sensor_last_read
                if error is not None:
                    sensor_status_label.set_text(f"ERROR DEL SENSOR: {error}")
                    sensor_status_label.classes(
                        replace="text-subtitle1 text-negative"
                    )
                    return
                if temperature is None:
                    sensor_status_label.set_text("ESPERANDO PRIMERA LECTURA...")
                    return
                temperature_label.set_text(f"{temperature:.1f} °C")
                age_s = time.monotonic() - last_read
                if age_s > 3.0:
                    sensor_status_label.set_text(
                        f"LECTURA SIN ACTUALIZAR · {age_s:.1f} s"
                    )
                    sensor_status_label.classes(
                        replace="text-subtitle1 text-warning"
                    )
                else:
                    sensor_status_label.set_text("SENSOR CONECTADO")
                    sensor_status_label.classes(
                        replace="text-subtitle1 text-positive"
                    )

            ui.timer(0.5, update_temperature)

        with ui.card().classes("w-full max-w-2xl"):
            ui.label("Demanda térmica solicitada").classes("text-subtitle2")
            duty_label = ui.label("0.0 %").classes("text-h3")
            physical_label = ui.label("PWM físico: 100.0 %").classes(
                "text-subtitle1"
            )
            voltage_label = ui.label("Referencia estimada: 3.30 V").classes(
                "text-subtitle1"
            )
            slider = ui.slider(
                min=0.0,
                max=args.max_duty,
                step=1.0,
                value=0.0,
            ).classes("w-full")
            slider.disable()

            enable_switch = ui.switch("Habilitar salida", value=False)
            status_label = ui.label("SALIDA APAGADA").classes(
                "text-h6 font-bold text-positive"
            )

            def update_labels(logical_duty: float) -> None:
                physical_duty = logical_to_physical_duty(
                    logical_duty,
                    args.active_ceiling,
                )
                duty_label.set_text(f"{logical_duty:.1f} %")
                physical_label.set_text(f"PWM físico: {physical_duty:.1f} %")
                voltage_label.set_text(
                    f"Referencia estimada: "
                    f"{3.3 * physical_duty / 100.0:.2f} V"
                )

            def change_duty(event) -> None:
                applied = set_output(float(event.value))
                update_labels(applied)

            def change_enabled(event) -> None:
                nonlocal enabled
                enabled = bool(event.value)
                if enabled:
                    slider.enable()
                    applied = set_output(float(slider.value))
                    status_label.set_text("SALIDA HABILITADA")
                    status_label.classes(
                        replace="text-h6 font-bold text-negative"
                    )
                else:
                    slider.set_value(0.0)
                    slider.disable()
                    disable_output()
                    applied = 0.0
                    status_label.set_text("SALIDA APAGADA")
                    status_label.classes(
                        replace="text-h6 font-bold text-positive"
                    )
                update_labels(applied)

            def emergency_stop() -> None:
                slider.set_value(0.0)
                enable_switch.set_value(False)
                disable_output()
                update_labels(0.0)
                status_label.set_text("PARO APLICADO · SALIDA 0 %")
                status_label.classes(replace="text-h6 font-bold text-negative")

            slider.on_value_change(change_duty)
            enable_switch.on_value_change(change_enabled)
            ui.button(
                "PARO · FORZAR 0 %",
                on_click=emergency_stop,
                color="negative",
            ).classes("w-full text-h6")

            def disconnect_client() -> None:
                disable_output()

            client.on_disconnect(disconnect_client)

        ui.label(
            "El voltaje mostrado es una estimación ideal del PWM físico. "
            "Confirma el valor real con multímetro."
        ).classes("text-caption text-grey-7")

    print(
        f"Modo: {'GPIO real' if args.real else 'simulado'}; "
        f"BCM{args.pin}; {args.frequency:g} Hz; máximo {args.max_duty:g} %"
    )
    print(
        "Sensor Vernier habilitado solo como referencia."
        if args.sensor
        else "Sensor no habilitado; usa --sensor para mostrar la temperatura."
    )
    print("La salida se inicializó en 0 %. Ctrl+C también ejecuta el apagado.")
    ui.run(
        host=args.host,
        port=args.port,
        title="Prueba PWM control de fase",
        reload=False,
    )


if __name__ == "__main__":
    main()
