from __future__ import annotations

import argparse
import csv
from datetime import datetime
import io
import threading
import time

from control_temperatura_pi.config import load_config
from control_temperatura_pi.pwm import (
    GPIOZeroPWMOutput,
    SimulatedPWMOutput,
    logical_to_physical_duty,
)
from control_temperatura_pi.sensors import VernierGDXTCASensor
from control_temperatura_pi.sensorwatts import SensorWattsClient


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
            "Habilitar el botón para conectar el Vernier configurado en "
            "config.toml; no se conecta automáticamente."
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
    parser.add_argument(
        "--sensorwatts-url",
        default="http://192.168.1.211/readings",
        help="Endpoint JSON del medidor SensorWatts",
    )
    parser.add_argument(
        "--sensorwatts-timeout",
        type=float,
        default=2.0,
        help="Tiempo máximo de cada consulta HTTP a SensorWatts, en segundos",
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
    if not args.sensorwatts_url.startswith(("http://", "https://")):
        raise SystemExit("--sensorwatts-url debe comenzar con http:// o https://")
    if args.sensorwatts_timeout <= 0:
        raise SystemExit("--sensorwatts-timeout debe ser mayor que cero")
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
    sensor_connecting = False

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

    def connect_and_read_sensor() -> None:
        nonlocal sensor
        nonlocal sensor_connecting, sensor_temperature_c
        nonlocal sensor_error, sensor_last_read
        created_sensor = None
        try:
            sensor_config = load_config(args.config).sensor
            created_sensor = VernierGDXTCASensor(
                connection=sensor_config.connection,
                sample_period_ms=sensor_config.sample_period_ms,
                ble_backend=sensor_config.ble_backend,
                ble_com_port=sensor_config.ble_com_port,
                device_name=sensor_config.device_name,
            )
            if sensor_stop.is_set():
                created_sensor.close()
                return
            with sensor_lock:
                sensor = created_sensor
                sensor_connecting = False
                sensor_error = None
            while not sensor_stop.is_set():
                try:
                    temperature = created_sensor.read_temperature_c()
                    with sensor_lock:
                        sensor_temperature_c = temperature
                        sensor_error = None
                        sensor_last_read = time.monotonic()
                except Exception as error:
                    with sensor_lock:
                        sensor_error = str(error)
                    if sensor_stop.wait(0.5):
                        break
        except Exception as error:
            with sensor_lock:
                sensor_connecting = False
                sensor_error = str(error)
        finally:
            if sensor_stop.is_set() and created_sensor is not None:
                created_sensor.close()

    lock = threading.Lock()
    enabled = False
    requested_duty = 0.0
    sensorwatts = SensorWattsClient(
        args.sensorwatts_url,
        timeout_s=args.sensorwatts_timeout,
    )
    sensorwatts_stop = threading.Event()
    sensorwatts_lock = threading.Lock()
    sensorwatts_reading = None
    sensorwatts_error: str | None = None
    sensorwatts_last_read = 0.0
    recording_lock = threading.Lock()
    recording = False
    recording_started = 0.0
    recorded_rows: list[dict[str, object]] = []
    csv_fields = [
        "FechaHora",
        "Tiempo_s",
        "PWM_Slider_percent",
        "V_estimada",
        "PWM_fisico_percent",
        "Temperatura_C",
        "Voltaje_V",
        "Corriente_A",
        "FP",
        "Potencia_Activa_W",
    ]

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

    def append_record(reading) -> None:
        with lock:
            logical_duty = requested_duty if enabled else 0.0
        physical_duty = logical_to_physical_duty(
            logical_duty,
            args.active_ceiling,
        )
        with sensor_lock:
            temperature = sensor_temperature_c
        now = datetime.now().astimezone()
        with recording_lock:
            if not recording:
                return
            elapsed_s = time.monotonic() - recording_started
            recorded_rows.append(
                {
                    "FechaHora": now.isoformat(timespec="seconds"),
                    "Tiempo_s": f"{elapsed_s:.1f}",
                    "PWM_Slider_percent": f"{logical_duty:.1f}",
                    "V_estimada": f"{3.3 * physical_duty / 100.0:.3f}",
                    "PWM_fisico_percent": f"{physical_duty:.1f}",
                    "Temperatura_C": (
                        "" if temperature is None else f"{temperature:.2f}"
                    ),
                    "Voltaje_V": f"{reading.voltage_v:.2f}",
                    "Corriente_A": f"{reading.current_a:.3f}",
                    "FP": f"{reading.power_factor:.4f}",
                    "Potencia_Activa_W": f"{reading.active_power_w:.2f}",
                }
            )

    def read_sensorwatts() -> None:
        nonlocal sensorwatts_reading, sensorwatts_error, sensorwatts_last_read
        while not sensorwatts_stop.is_set():
            try:
                reading = sensorwatts.read()
                with sensorwatts_lock:
                    sensorwatts_reading = reading
                    sensorwatts_error = None
                    sensorwatts_last_read = time.monotonic()
                append_record(reading)
            except Exception as error:
                with sensorwatts_lock:
                    sensorwatts_error = str(error)
            if sensorwatts_stop.wait(1.0):
                break

    sensorwatts_thread = threading.Thread(
        target=read_sensorwatts,
        name="sensorwatts-monitor",
        daemon=True,
    )
    sensorwatts_thread.start()

    def close_hardware() -> None:
        disable_output()
        pwm.close()
        sensor_stop.set()
        if sensor is not None:
            sensor.close()
        if sensor_thread is not None:
            sensor_thread.join(timeout=2.0)
        sensorwatts_stop.set()
        sensorwatts_thread.join(timeout=args.sensorwatts_timeout + 0.5)

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
                "SENSOR DESCONECTADO"
                if args.sensor
                else "SENSOR NO HABILITADO"
            ).classes("text-subtitle1 text-grey-7")
            ui.label(
                "Esta lectura no modifica la demanda térmica ni la salida PWM."
            ).classes("text-caption text-grey-7")

            def start_sensor_connection() -> None:
                nonlocal sensor_thread, sensor_connecting
                nonlocal sensor_temperature_c, sensor_error
                if not args.sensor:
                    ui.notify(
                        "Inicia el programa con --sensor para habilitar el Vernier",
                        type="warning",
                    )
                    return
                with sensor_lock:
                    if sensor is not None or sensor_connecting:
                        return
                    sensor_connecting = True
                    sensor_temperature_c = None
                    sensor_error = None
                sensor_button.disable()
                sensor_button.set_text("CONECTANDO...")
                sensor_thread = threading.Thread(
                    target=connect_and_read_sensor,
                    name="vernier-temperature-reference",
                    daemon=True,
                )
                sensor_thread.start()

            sensor_button = ui.button(
                "CONECTAR SENSOR DE TEMPERATURA",
                on_click=start_sensor_connection,
            ).classes("w-full")
            if not args.sensor:
                sensor_button.disable()

            def update_temperature() -> None:
                with sensor_lock:
                    temperature = sensor_temperature_c
                    error = sensor_error
                    last_read = sensor_last_read
                    connecting = sensor_connecting
                    connected = sensor is not None
                if not args.sensor:
                    sensor_status_label.set_text(
                        "SENSOR NO HABILITADO · USA --sensor"
                    )
                    return
                if connecting:
                    sensor_button.disable()
                    sensor_button.set_text("CONECTANDO...")
                    sensor_status_label.set_text("CONECTANDO SENSOR...")
                    sensor_status_label.classes(
                        replace="text-subtitle1 text-primary"
                    )
                    return
                if error is not None:
                    sensor_status_label.set_text(f"ERROR DEL SENSOR: {error}")
                    sensor_status_label.classes(
                        replace="text-subtitle1 text-negative"
                    )
                    if not connected:
                        sensor_button.enable()
                        sensor_button.set_text("REINTENTAR CONEXIÓN")
                    return
                if temperature is None:
                    sensor_status_label.set_text(
                        "SENSOR DESCONECTADO"
                        if not connected
                        else "ESPERANDO PRIMERA LECTURA..."
                    )
                    if not connected:
                        sensor_button.enable()
                        sensor_button.set_text(
                            "CONECTAR SENSOR DE TEMPERATURA"
                        )
                    return
                temperature_label.set_text(f"{temperature:.1f} °C")
                sensor_button.disable()
                sensor_button.set_text("SENSOR CONECTADO")
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

        with ui.card().classes("w-full max-w-2xl"):
            ui.label("SensorWatts").classes("text-h6 font-bold")
            with ui.row().classes("w-full justify-between"):
                with ui.column().classes("items-center"):
                    ui.label("Voltaje").classes("text-caption")
                    watts_voltage_label = ui.label("--.-- V").classes("text-h6")
                with ui.column().classes("items-center"):
                    ui.label("Corriente").classes("text-caption")
                    watts_current_label = ui.label("--.--- A").classes("text-h6")
                with ui.column().classes("items-center"):
                    ui.label("FP").classes("text-caption")
                    watts_pf_label = ui.label("-.----").classes("text-h6")
                with ui.column().classes("items-center"):
                    ui.label("Potencia activa").classes("text-caption")
                    watts_power_label = ui.label("--.-- W").classes("text-h6")

            watts_status_label = ui.label(
                f"CONECTANDO A {args.sensorwatts_url}..."
            ).classes("text-subtitle1 text-grey-7")
            recording_status_label = ui.label("REGISTRO DETENIDO · 0 MUESTRAS")

            def toggle_recording() -> None:
                nonlocal recording, recording_started
                rows_to_download: list[dict[str, object]] | None = None
                with recording_lock:
                    if recording:
                        recording = False
                        rows_to_download = list(recorded_rows)
                        recorded_rows.clear()
                    else:
                        recorded_rows.clear()
                        recording_started = time.monotonic()
                        recording = True

                if rows_to_download is None:
                    record_button.set_text("DETENER Y DESCARGAR CSV")
                    recording_status_label.set_text(
                        "REGISTRANDO · ESPERANDO MUESTRAS"
                    )
                    recording_status_label.classes(
                        replace="text-subtitle1 text-negative font-bold"
                    )
                    return

                record_button.set_text("INICIAR REGISTRO CSV")
                recording_status_label.classes(
                    replace="text-subtitle1 text-grey-7"
                )
                if not rows_to_download:
                    recording_status_label.set_text(
                        "REGISTRO DETENIDO · SIN MUESTRAS"
                    )
                    ui.notify(
                        "No se recibieron muestras válidas de SensorWatts",
                        type="warning",
                    )
                    return

                output = io.StringIO(newline="")
                writer = csv.DictWriter(output, fieldnames=csv_fields)
                writer.writeheader()
                writer.writerows(rows_to_download)
                filename = (
                    "sensorwatts_"
                    f"{datetime.now().astimezone():%Y%m%d_%H%M%S}.csv"
                )
                ui.download.content(
                    output.getvalue().encode("utf-8-sig"),
                    filename,
                    media_type="text/csv",
                )
                recording_status_label.set_text(
                    f"DESCARGADO · {len(rows_to_download)} MUESTRAS · "
                    "REGISTRO LIMPIO"
                )
                ui.notify(
                    f"CSV descargado con {len(rows_to_download)} muestras",
                    type="positive",
                )

            record_button = ui.button(
                "INICIAR REGISTRO CSV",
                on_click=toggle_recording,
            ).classes("w-full text-h6")

            def update_sensorwatts() -> None:
                with sensorwatts_lock:
                    reading = sensorwatts_reading
                    error = sensorwatts_error
                    last_read = sensorwatts_last_read
                if reading is not None:
                    watts_voltage_label.set_text(f"{reading.voltage_v:.2f} V")
                    watts_current_label.set_text(f"{reading.current_a:.3f} A")
                    watts_pf_label.set_text(f"{reading.power_factor:.4f}")
                    watts_power_label.set_text(
                        f"{reading.active_power_w:.2f} W"
                    )
                if error is not None:
                    watts_status_label.set_text(
                        f"ERROR SENSORWATTS: {error}"
                    )
                    watts_status_label.classes(
                        replace="text-subtitle1 text-negative"
                    )
                elif reading is None:
                    watts_status_label.set_text("ESPERANDO PRIMERA LECTURA...")
                else:
                    age_s = time.monotonic() - last_read
                    watts_status_label.set_text(
                        "SENSORWATTS CONECTADO"
                        if age_s <= 3.0
                        else f"LECTURA SIN ACTUALIZAR · {age_s:.1f} s"
                    )
                    watts_status_label.classes(
                        replace=(
                            "text-subtitle1 text-positive"
                            if age_s <= 3.0
                            else "text-subtitle1 text-warning"
                        )
                    )

                with recording_lock:
                    is_recording = recording
                    sample_count = len(recorded_rows)
                    elapsed_s = (
                        time.monotonic() - recording_started
                        if is_recording
                        else 0.0
                    )
                if is_recording:
                    record_button.set_text("DETENER Y DESCARGAR CSV")
                    recording_status_label.set_text(
                        f"REGISTRANDO · {sample_count} MUESTRAS · "
                        f"{elapsed_s:.0f} s"
                    )

            ui.timer(0.5, update_sensorwatts)

        ui.label(
            "El voltaje mostrado es una estimación ideal del PWM físico. "
            "Confirma el valor real con multímetro."
        ).classes("text-caption text-grey-7")

    print(
        f"Modo: {'GPIO real' if args.real else 'simulado'}; "
        f"BCM{args.pin}; {args.frequency:g} Hz; máximo {args.max_duty:g} %"
    )
    print(
        "Sensor Vernier disponible como referencia; conéctalo desde la página."
        if args.sensor
        else "Sensor no habilitado; usa --sensor para activar su botón."
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
