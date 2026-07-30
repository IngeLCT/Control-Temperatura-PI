from __future__ import annotations

import argparse
import asyncio
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
from control_temperatura_pi.sensors import (
    VernierGDXTCASensor,
    discover_vernier_device_names,
)
from control_temperatura_pi.sensorwatts import SensorWattsClient


CONTROLLED_TEST_STEP_SECONDS = 180.0


def controlled_test_levels() -> tuple[int, ...]:
    return (
        *range(10, 101, 10),
        *range(90, 0, -10),
    )


def describe_error(error: Exception) -> str:
    message = str(error).strip()
    return (
        f"{type(error).__name__}: {message}"
        if message
        else type(error).__name__
    )


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
            "Habilitar el escaneo y la conexión manual de sensores GDX; "
            "no se conecta automáticamente."
        ),
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Ruta con la configuración BLE/USB usada por --sensor",
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

    from nicegui import app, run, ui

    sensor = None
    sensor_thread = None
    sensor_stop = threading.Event()
    sensor_lock = threading.Lock()
    sensor_temperature_c: float | None = None
    sensor_error: str | None = None
    sensor_last_read = 0.0
    sensor_connecting = False
    sensor_scanning = False
    sensor_view = {
        "temperature": "--.- °C",
        "status": (
            "SENSOR DESCONECTADO"
            if args.sensor
            else "SENSOR NO HABILITADO · USA --sensor"
        ),
        "scan_button": "ESCANEAR SENSORES GDX",
        "scan_enabled": args.sensor,
        "connect_button": "CONECTAR SENSOR SELECCIONADO",
        "connect_enabled": False,
    }

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

    def scan_sensor_names() -> list[str]:
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        try:
            sensor_config = load_config(args.config).sensor
            return discover_vernier_device_names(
                connection=sensor_config.connection,
                ble_backend=sensor_config.ble_backend,
                ble_com_port=sensor_config.ble_com_port,
                prefix="GDX",
            )
        finally:
            asyncio.set_event_loop(None)
            event_loop.close()

    def connect_and_read_sensor(device_name: str) -> None:
        nonlocal sensor
        nonlocal sensor_connecting, sensor_temperature_c
        nonlocal sensor_error, sensor_last_read
        created_sensor = None
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        try:
            sensor_config = load_config(args.config).sensor
            created_sensor = VernierGDXTCASensor(
                connection=sensor_config.connection,
                sample_period_ms=sensor_config.sample_period_ms,
                ble_backend=sensor_config.ble_backend,
                ble_com_port=sensor_config.ble_com_port,
                device_name=device_name,
            )
            if sensor_stop.is_set():
                created_sensor.close()
                return
            with sensor_lock:
                sensor = created_sensor
                sensor_connecting = False
                sensor_error = None
                sensor_view["status"] = "ESPERANDO PRIMERA LECTURA..."
                sensor_view["connect_button"] = "SENSOR CONECTADO"
                sensor_view["connect_enabled"] = False
                sensor_view["scan_enabled"] = False
            while not sensor_stop.is_set():
                try:
                    temperature = created_sensor.read_temperature_c()
                    with sensor_lock:
                        sensor_temperature_c = temperature
                        sensor_error = None
                        sensor_last_read = time.monotonic()
                        sensor_view["temperature"] = f"{temperature:.1f} °C"
                        sensor_view["status"] = "SENSOR CONECTADO"
                except Exception as error:
                    with sensor_lock:
                        sensor_error = describe_error(error)
                        sensor_view["status"] = (
                            f"ERROR DEL SENSOR: {sensor_error}"
                        )
                    if sensor_stop.wait(0.5):
                        break
        except Exception as error:
            with sensor_lock:
                sensor_connecting = False
                sensor_error = describe_error(error)
                sensor_view["status"] = f"ERROR DEL SENSOR: {sensor_error}"
                sensor_view["connect_button"] = "REINTENTAR CONEXIÓN"
                sensor_view["connect_enabled"] = True
                sensor_view["scan_enabled"] = True
        finally:
            if created_sensor is not None:
                created_sensor.close()
            asyncio.set_event_loop(None)
            event_loop.close()

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
    sensorwatts_view = {
        "voltage": "--.-- V",
        "current": "--.--- A",
        "power_factor": "-.----",
        "active_power": "--.-- W",
        "status": f"CONECTANDO A {args.sensorwatts_url}...",
    }
    recording_lock = threading.Lock()
    recording = False
    recording_started = 0.0
    recorded_rows: list[dict[str, object]] = []
    recording_view = {
        "status": "REGISTRO DETENIDO · 0 MUESTRAS",
        "button": "INICIAR REGISTRO CSV",
    }
    controlled_test_running = False
    controlled_test_cancel = threading.Event()
    controlled_test_view = {
        "status": "PRUEBA CONTROLADA DETENIDA · 19 PASOS · 57 MIN",
        "button": "INICIAR PRUEBA CONTROLADA",
    }
    csv_fields = [
        "Fecha",
        "Hora",
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
                    "Fecha": now.strftime("%d-%m-%Y"),
                    "Hora": now.strftime("%H:%M:%S"),
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
            recording_view["status"] = (
                f"REGISTRANDO · {len(recorded_rows)} MUESTRAS · "
                f"{elapsed_s:.0f} s"
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
                    sensorwatts_view["voltage"] = (
                        f"{reading.voltage_v:.2f} V"
                    )
                    sensorwatts_view["current"] = (
                        f"{reading.current_a:.3f} A"
                    )
                    sensorwatts_view["power_factor"] = (
                        f"{reading.power_factor:.4f}"
                    )
                    sensorwatts_view["active_power"] = (
                        f"{reading.active_power_w:.2f} W"
                    )
                    sensorwatts_view["status"] = "SENSORWATTS CONECTADO"
                append_record(reading)
            except Exception as error:
                with sensorwatts_lock:
                    sensorwatts_error = describe_error(error)
                    sensorwatts_view["status"] = (
                        f"ERROR SENSORWATTS: {sensorwatts_error}"
                    )
            if sensorwatts_stop.wait(1.0):
                break

    sensorwatts_thread = threading.Thread(
        target=read_sensorwatts,
        name="sensorwatts-monitor",
        daemon=True,
    )
    sensorwatts_thread.start()

    def close_hardware() -> None:
        controlled_test_cancel.set()
        disable_output()
        pwm.close()
        sensor_stop.set()
        if sensor_thread is not None:
            sensor_thread.join(timeout=2.0)
        sensorwatts_stop.set()
        sensorwatts_thread.join(timeout=args.sensorwatts_timeout + 0.5)

    app.on_shutdown(close_hardware)

    @ui.page("/")
    def index() -> None:
        nonlocal enabled
        client = ui.context.client

        def toggle_recording(*, controlled: bool = False) -> bool:
            nonlocal recording, recording_started
            if controlled_test_running and not controlled:
                ui.notify(
                    "El registro está administrado por la prueba controlada",
                    type="warning",
                )
                return False

            rows_to_download: list[dict[str, object]] | None = None
            with recording_lock:
                if recording:
                    recording = False
                    rows_to_download = list(recorded_rows)
                    recorded_rows.clear()
                    recording_view["button"] = "INICIAR REGISTRO CSV"
                else:
                    recorded_rows.clear()
                    recording_started = time.monotonic()
                    recording = True
                    recording_view["button"] = "DETENER Y DESCARGAR CSV"
                    recording_view["status"] = (
                        "REGISTRANDO · ESPERANDO MUESTRAS"
                    )

            if rows_to_download is None:
                return True

            if not rows_to_download:
                recording_view["status"] = "REGISTRO DETENIDO · SIN MUESTRAS"
                ui.notify(
                    "No se recibieron muestras válidas de SensorWatts",
                    type="warning",
                )
                return True

            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=csv_fields)
            writer.writeheader()
            writer.writerows(rows_to_download)
            prefix = "prueba_controlada" if controlled else "sensorwatts"
            filename = (
                f"{prefix}_"
                f"{datetime.now().astimezone():%Y%m%d_%H%M%S}.csv"
            )
            ui.download.content(
                output.getvalue().encode("utf-8-sig"),
                filename,
                media_type="text/csv",
            )
            recording_view["status"] = (
                f"DESCARGADO · {len(rows_to_download)} MUESTRAS · "
                "REGISTRO LIMPIO"
            )
            ui.notify(
                f"CSV descargado con {len(rows_to_download)} muestras",
                type="positive",
            )
            return True

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

        main_cards = ui.element("div").classes(
            "grid grid-cols-1 lg:grid-cols-2 gap-4 "
            "w-full max-w-7xl items-stretch"
        )

        with ui.card().classes("w-full h-full") as temperature_card:
            ui.label("Temperatura de referencia").classes("text-subtitle2")
            ui.label().bind_text_from(
                sensor_view,
                "temperature",
            ).classes("text-h3")
            ui.label().bind_text_from(
                sensor_view,
                "status",
            ).classes("text-subtitle1 text-grey-7")
            ui.label(
                "Esta lectura no modifica la demanda térmica ni la salida PWM."
            ).classes("text-caption text-grey-7")

            sensor_select = ui.select(
                options=[],
                label="Sensor GDX disponible",
            ).props("outlined").classes("w-full")
            sensor_select.disable()

            async def scan_sensors() -> None:
                nonlocal sensor_scanning, sensor_error
                if not args.sensor:
                    ui.notify(
                        "Inicia el programa con --sensor para habilitar el Vernier",
                        type="warning",
                    )
                    return
                with sensor_lock:
                    if sensor is not None or sensor_connecting or sensor_scanning:
                        return
                    sensor_scanning = True
                    sensor_error = None
                    sensor_view["status"] = "ESCANEANDO DISPOSITIVOS BLE..."
                    sensor_view["scan_button"] = "ESCANEANDO..."
                    sensor_view["scan_enabled"] = False
                    sensor_view["connect_enabled"] = False
                sensor_select.disable()
                try:
                    names = await run.io_bound(scan_sensor_names)
                    names = names or []
                    sensor_select.set_options(
                        names,
                        value=names[0] if names else None,
                    )
                    if names:
                        sensor_select.enable()
                        with sensor_lock:
                            sensor_view["status"] = (
                                f"{len(names)} SENSOR(ES) GDX ENCONTRADO(S)"
                            )
                            sensor_view["connect_button"] = (
                                "CONECTAR SENSOR SELECCIONADO"
                            )
                            sensor_view["connect_enabled"] = True
                        ui.notify(
                            f"Se encontraron {len(names)} sensores GDX",
                            type="positive",
                        )
                    else:
                        with sensor_lock:
                            sensor_view["status"] = (
                                "NO SE ENCONTRARON DISPOSITIVOS QUE EMPIECEN CON GDX"
                            )
                        ui.notify(
                            "No se encontraron sensores GDX encendidos",
                            type="warning",
                        )
                except Exception as error:
                    with sensor_lock:
                        sensor_error = describe_error(error)
                        sensor_view["status"] = (
                            f"ERROR AL ESCANEAR: {sensor_error}"
                        )
                    ui.notify(
                        f"Falló el escaneo: {describe_error(error)}",
                        type="negative",
                    )
                finally:
                    with sensor_lock:
                        sensor_scanning = False
                        sensor_view["scan_button"] = "VOLVER A ESCANEAR GDX"
                        sensor_view["scan_enabled"] = True

            def start_sensor_connection() -> None:
                nonlocal sensor_thread, sensor_connecting
                nonlocal sensor_temperature_c, sensor_error
                if not args.sensor:
                    ui.notify(
                        "Inicia el programa con --sensor para habilitar el Vernier",
                        type="warning",
                    )
                    return
                selected_device = str(sensor_select.value or "").strip()
                if not selected_device.startswith("GDX"):
                    ui.notify(
                        "Primero escanea y selecciona un sensor GDX",
                        type="warning",
                    )
                    return
                with sensor_lock:
                    if sensor is not None or sensor_connecting or sensor_scanning:
                        return
                    sensor_connecting = True
                    sensor_temperature_c = None
                    sensor_error = None
                    sensor_view["temperature"] = "--.- °C"
                    sensor_view["status"] = f"CONECTANDO A {selected_device}..."
                    sensor_view["connect_button"] = "CONECTANDO..."
                    sensor_view["connect_enabled"] = False
                    sensor_view["scan_enabled"] = False
                sensor_select.disable()
                sensor_thread = threading.Thread(
                    target=connect_and_read_sensor,
                    args=(selected_device,),
                    name="vernier-temperature-reference",
                    daemon=True,
                )
                sensor_thread.start()

            ui.button(
                on_click=scan_sensors,
            ).bind_text_from(
                sensor_view,
                "scan_button",
            ).bind_enabled_from(
                sensor_view,
                "scan_enabled",
            ).classes("w-full")

            ui.button(
                on_click=start_sensor_connection,
            ).bind_text_from(
                sensor_view,
                "connect_button",
            ).bind_enabled_from(
                sensor_view,
                "connect_enabled",
            ).classes("w-full")
        temperature_card.move(main_cards)

        with ui.card().classes("w-full h-full") as pwm_card:
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
                if controlled_test_running:
                    return
                applied = set_output(float(event.value))
                update_labels(applied)

            def change_enabled(event) -> None:
                nonlocal enabled
                if controlled_test_running:
                    return
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
                controlled_test_cancel.set()
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

            async def toggle_controlled_test() -> None:
                nonlocal controlled_test_running, enabled, recording
                if controlled_test_running:
                    controlled_test_cancel.set()
                    controlled_test_view["button"] = "CANCELANDO PRUEBA..."
                    controlled_test_view["status"] = (
                        "CANCELACIÓN SOLICITADA · FORZANDO SALIDA 0 %"
                    )
                    disable_output()
                    return

                if args.max_duty < 100.0:
                    ui.notify(
                        "La prueba requiere --max-duty 100",
                        type="warning",
                    )
                    return

                with recording_lock:
                    recording_in_progress = recording
                if recording_in_progress:
                    ui.notify(
                        "Detén primero el registro CSV manual",
                        type="warning",
                    )
                    return

                with sensorwatts_lock:
                    watts_available = (
                        sensorwatts_reading is not None
                        and time.monotonic() - sensorwatts_last_read < 5.0
                    )
                if not watts_available:
                    ui.notify(
                        "SensorWatts debe estar entregando mediciones válidas",
                        type="warning",
                    )
                    return

                controlled_test_running = True
                controlled_test_cancel.clear()
                controlled_test_view["button"] = "CANCELAR PRUEBA CONTROLADA"
                controlled_test_view["status"] = "INICIANDO PRUEBA CONTROLADA"
                record_button.disable()
                enable_switch.set_value(True)
                enable_switch.disable()
                slider.disable()
                enabled = True
                set_output(0.0)
                update_labels(0.0)
                status_label.set_text("PRUEBA CONTROLADA ACTIVA")
                status_label.classes(
                    replace="text-h6 font-bold text-negative"
                )

                if not toggle_recording(controlled=True):
                    controlled_test_cancel.set()

                levels = controlled_test_levels()
                completed = False
                try:
                    for stage, level in enumerate(levels, start=1):
                        if controlled_test_cancel.is_set():
                            break

                        slider.set_value(float(level))
                        applied = set_output(float(level))
                        update_labels(applied)
                        deadline = (
                            time.monotonic() + CONTROLLED_TEST_STEP_SECONDS
                        )

                        while not controlled_test_cancel.is_set():
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                            seconds = int(remaining + 0.999)
                            controlled_test_view["status"] = (
                                f"PASO {stage}/{len(levels)} · "
                                f"PWM {level} % · RESTAN {seconds} s"
                            )
                            await asyncio.sleep(min(1.0, remaining))
                    else:
                        completed = True
                finally:
                    disable_output()
                    controlled_test_running = False
                    if client.has_socket_connection:
                        slider.set_value(0.0)
                        update_labels(0.0)
                        enable_switch.set_value(False)
                        enable_switch.enable()
                        slider.disable()

                        with recording_lock:
                            must_finish_recording = recording
                        if must_finish_recording:
                            toggle_recording(controlled=True)

                        record_button.enable()
                        controlled_test_view["button"] = (
                            "INICIAR PRUEBA CONTROLADA"
                        )
                        controlled_test_view["status"] = (
                            "PRUEBA COMPLETADA · PWM 0 % · CSV DESCARGADO"
                            if completed
                            else (
                                "PRUEBA CANCELADA · PWM 0 % · "
                                "CSV PARCIAL DESCARGADO"
                            )
                        )
                        status_label.set_text("SALIDA APAGADA")
                        status_label.classes(
                            replace="text-h6 font-bold text-positive"
                        )
                    else:
                        with recording_lock:
                            recording = False
                            recorded_rows.clear()
                            recording_view["button"] = "INICIAR REGISTRO CSV"
                            recording_view["status"] = (
                                "REGISTRO CANCELADO POR DESCONEXIÓN"
                            )

            ui.button(
                on_click=toggle_controlled_test,
                color="primary",
            ).bind_text_from(
                controlled_test_view,
                "button",
            ).classes("w-full text-h6")
            ui.label().bind_text_from(
                controlled_test_view,
                "status",
            ).classes("text-subtitle2 text-grey-7")

            def disconnect_client() -> None:
                controlled_test_cancel.set()
                disable_output()

            client.on_disconnect(disconnect_client)
        pwm_card.move(main_cards)

        with ui.card().classes("w-full max-w-7xl"):
            ui.label("SensorWatts").classes("text-h6 font-bold")
            with ui.row().classes("w-full justify-between"):
                with ui.column().classes("items-center"):
                    ui.label("Voltaje").classes("text-caption")
                    ui.label().bind_text_from(
                        sensorwatts_view,
                        "voltage",
                    ).classes("text-h6")
                with ui.column().classes("items-center"):
                    ui.label("Corriente").classes("text-caption")
                    ui.label().bind_text_from(
                        sensorwatts_view,
                        "current",
                    ).classes("text-h6")
                with ui.column().classes("items-center"):
                    ui.label("FP").classes("text-caption")
                    ui.label().bind_text_from(
                        sensorwatts_view,
                        "power_factor",
                    ).classes("text-h6")
                with ui.column().classes("items-center"):
                    ui.label("Potencia activa").classes("text-caption")
                    ui.label().bind_text_from(
                        sensorwatts_view,
                        "active_power",
                    ).classes("text-h6")

            ui.label().bind_text_from(
                sensorwatts_view,
                "status",
            ).classes("text-subtitle1 text-grey-7")
            ui.label().bind_text_from(
                recording_view,
                "status",
            ).classes("text-subtitle1")

            record_button = ui.button(
                on_click=toggle_recording,
            ).bind_text_from(
                recording_view,
                "button",
            ).classes("w-full text-h6")

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
