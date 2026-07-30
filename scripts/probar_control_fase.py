from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime
from pathlib import Path
import threading
import time
from typing import TextIO

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


CONTROLLED_TEST_DEFAULT_STEP_MINUTES = 3.0
CONTROLLED_TEST_MIN_STEP_MINUTES = 0.1
CONTROLLED_TEST_MAX_STEP_MINUTES = 60.0


def controlled_test_levels(step_percent: int = 10) -> tuple[int, ...]:
    if not 1 <= step_percent <= 100 or 100 % step_percent != 0:
        raise ValueError("El paso PWM debe dividir exactamente 100")
    return (
        *range(step_percent, 101, step_percent),
        *range(100 - step_percent, 0, -step_percent),
    )


def controlled_test_duration_minutes(
    step_minutes: float,
    step_percent: int = 10,
) -> float:
    return len(controlled_test_levels(step_percent)) * step_minutes


def controlled_test_step_seconds(step_minutes: float) -> float:
    return step_minutes * 60.0


def format_minutes(minutes: float) -> str:
    return f"{minutes:g}"


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
    parser.add_argument(
        "--csv-dir",
        default="data/registros",
        help="Directorio interno de la Raspberry para conservar los CSV",
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
    pwm_view = {
        "slider": 0.0,
        "logical": "0.0 %",
        "physical": "PWM físico: 100.0 %",
        "voltage": "Referencia estimada: 3.30 V",
        "enabled": False,
        "status": "SALIDA APAGADA",
        "slider_enabled": False,
        "manual_controls_enabled": True,
    }
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
        "active_power": "--.-- W",
        "status": f"CONECTANDO A {args.sensorwatts_url}...",
    }
    recording_lock = threading.Lock()
    recording = False
    recording_started = 0.0
    recording_file: TextIO | None = None
    recording_writer: csv.DictWriter | None = None
    recording_path: Path | None = None
    recording_samples = 0
    csv_dir = Path(args.csv_dir).expanduser().resolve()
    existing_csv_files = (
        list(csv_dir.glob("*.csv"))
        if csv_dir.is_dir()
        else []
    )
    last_saved_csv_path: Path | None = max(
        existing_csv_files,
        key=lambda path: path.stat().st_mtime,
        default=None,
    )
    recording_view = {
        "status": (
            f"ÚLTIMO CSV EN PI · {last_saved_csv_path.name}"
            if last_saved_csv_path is not None
            else "REGISTRO DETENIDO · 0 MUESTRAS"
        ),
        "button": "INICIAR REGISTRO CSV",
        "manual_enabled": True,
        "has_saved_file": last_saved_csv_path is not None,
    }
    controlled_test_running = False
    controlled_test_thread = None
    controlled_test_owner_id: str | None = None
    controlled_test_cancel = threading.Event()
    controlled_test_view = {
        "status": (
            "PRUEBA CONTROLADA DETENIDA · 19 PASOS · "
            f"{format_minutes(controlled_test_duration_minutes(
                CONTROLLED_TEST_DEFAULT_STEP_MINUTES
            ))} MIN"
        ),
        "button": "INICIAR PRUEBA CONTROLADA",
        "controls_enabled": True,
    }
    chart_lock = threading.Lock()
    chart_times_s: list[float] = []
    chart_temperatures_c: list[float | None] = []
    ui_clients_lock = threading.Lock()
    ui_clients: dict[str, tuple[object, object]] = {}
    ui_loop: asyncio.AbstractEventLoop | None = None
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
        "Potencia_Activa_W",
    ]

    def update_pwm_view(logical_duty: float, status: str | None = None) -> None:
        physical_duty = logical_to_physical_duty(
            logical_duty,
            args.active_ceiling,
        )
        pwm_view["slider"] = logical_duty
        pwm_view["logical"] = f"{logical_duty:.1f} %"
        pwm_view["physical"] = f"PWM físico: {physical_duty:.1f} %"
        pwm_view["voltage"] = (
            f"Referencia estimada: {3.3 * physical_duty / 100.0:.2f} V"
        )
        pwm_view["enabled"] = enabled
        pwm_view["slider_enabled"] = enabled and not controlled_test_running
        pwm_view["manual_controls_enabled"] = not controlled_test_running
        if status is not None:
            pwm_view["status"] = status

    def set_output(duty_percent: float, status: str | None = None) -> float:
        nonlocal requested_duty
        with lock:
            requested_duty = min(args.max_duty, max(0.0, duty_percent))
            applied = requested_duty if enabled else 0.0
            pwm.set_duty_percent(applied)
        update_pwm_view(applied, status)
        return applied

    def disable_output(reason: str = "SALIDA APAGADA") -> None:
        nonlocal enabled, requested_duty
        with lock:
            enabled = False
            requested_duty = 0.0
            pwm.set_duty_percent(0.0)
        update_pwm_view(0.0, reason)

    def update_connected_charts() -> None:
        with chart_lock:
            times = [round(value, 1) for value in chart_times_s]
            temperatures = list(chart_temperatures_c)
        with ui_clients_lock:
            clients = list(ui_clients.values())
        for client, chart in clients:
            if not client.has_socket_connection:
                continue
            chart.options["xAxis"]["data"] = times
            chart.options["series"][0]["data"] = temperatures
            chart.update()

    def schedule_chart_update() -> None:
        loop = ui_loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(update_connected_charts)

    def attempt_owner_download(path: Path, owner_id: str | None) -> None:
        if owner_id is None:
            return

        def download() -> None:
            with ui_clients_lock:
                entry = ui_clients.get(owner_id)
                if entry is None:
                    entry = next(
                        (
                            candidate
                            for candidate in ui_clients.values()
                            if candidate[0].has_socket_connection
                        ),
                        None,
                    )
            if entry is None:
                recording_view["status"] = (
                    f"CSV GUARDADO EN PI · DESCARGA PENDIENTE · {path.name}"
                )
                return
            client, _ = entry
            if not client.has_socket_connection:
                recording_view["status"] = (
                    f"CSV GUARDADO EN PI · DESCARGA PENDIENTE · {path.name}"
                )
                return
            with client:
                ui.download.file(path, filename=path.name, media_type="text/csv")
                ui.notify(
                    f"CSV guardado en la Raspberry y enviado: {path.name}",
                    type="positive",
                )

        loop = ui_loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(download)

    def start_recording(prefix: str) -> bool:
        nonlocal recording, recording_started
        nonlocal recording_file, recording_writer, recording_path
        nonlocal recording_samples
        with recording_lock:
            if recording:
                return False
            csv_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().astimezone()
            recording_path = csv_dir / (
                f"{prefix}_{timestamp:%Y%m%d_%H%M%S}.csv"
            )
            recording_file = recording_path.open(
                "w",
                encoding="utf-8-sig",
                newline="",
                buffering=1,
            )
            recording_writer = csv.DictWriter(
                recording_file,
                fieldnames=csv_fields,
            )
            recording_writer.writeheader()
            recording_file.flush()
            recording_started = time.monotonic()
            recording_samples = 0
            recording = True
            recording_view["button"] = "DETENER Y DESCARGAR CSV"
            recording_view["status"] = (
                f"REGISTRANDO EN PI · {recording_path.name}"
            )
        with chart_lock:
            chart_times_s.clear()
            chart_temperatures_c.clear()
        schedule_chart_update()
        return True

    def finish_recording() -> Path | None:
        nonlocal recording, recording_file, recording_writer
        nonlocal recording_path, last_saved_csv_path
        with recording_lock:
            if not recording:
                return None
            recording = False
            file_handle = recording_file
            saved_path = recording_path
            recording_file = None
            recording_writer = None
            recording_path = None
            if file_handle is not None:
                file_handle.flush()
                file_handle.close()
            if saved_path is None:
                return None
            last_saved_csv_path = saved_path
            recording_view["button"] = "INICIAR REGISTRO CSV"
            recording_view["has_saved_file"] = True
            recording_view["status"] = (
                f"CSV GUARDADO EN PI · {recording_samples} MUESTRAS · "
                f"{saved_path.name}"
            )
            return saved_path

    def append_record(reading) -> None:
        nonlocal recording_samples
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
            if not recording or recording_writer is None:
                return
            elapsed_s = time.monotonic() - recording_started
            row = {
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
                "Potencia_Activa_W": f"{reading.active_power_w:.2f}",
            }
            try:
                recording_writer.writerow(row)
                if recording_file is not None:
                    recording_file.flush()
            except Exception as error:
                recording_view["status"] = (
                    f"ERROR AL GUARDAR CSV: {describe_error(error)}"
                )
                controlled_test_cancel.set()
                return
            recording_samples += 1
            recording_view["status"] = (
                f"REGISTRANDO EN PI · {recording_samples} MUESTRAS · "
                f"{elapsed_s:.0f} s"
            )
        with chart_lock:
            chart_times_s.append(elapsed_s)
            chart_temperatures_c.append(temperature)
        schedule_chart_update()

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

    def run_controlled_test(
        step_minutes: float,
        step_percent: int,
        owner_id: str,
    ) -> None:
        nonlocal controlled_test_running, controlled_test_owner_id
        levels = controlled_test_levels(step_percent)
        step_seconds = controlled_test_step_seconds(step_minutes)
        completed = False
        try:
            for stage, level in enumerate(levels, start=1):
                if controlled_test_cancel.is_set():
                    break
                set_output(
                    float(level),
                    status=f"PRUEBA CONTROLADA · PWM {level} %",
                )
                deadline = time.monotonic() + step_seconds
                while not controlled_test_cancel.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    seconds = int(remaining + 0.999)
                    controlled_test_view["status"] = (
                        f"PASO {stage}/{len(levels)} · PWM {level} % · "
                        f"RESTAN {seconds} s"
                    )
                    if controlled_test_cancel.wait(min(1.0, remaining)):
                        break
            else:
                completed = True
        finally:
            reason = (
                "PRUEBA COMPLETADA · SALIDA 0 %"
                if completed
                else "PRUEBA CANCELADA · SALIDA 0 %"
            )
            disable_output(reason)
            saved_path = finish_recording()
            controlled_test_running = False
            controlled_test_owner_id = None
            pwm_view["manual_controls_enabled"] = True
            pwm_view["slider_enabled"] = False
            controlled_test_view["button"] = "INICIAR PRUEBA CONTROLADA"
            controlled_test_view["controls_enabled"] = True
            controlled_test_view["status"] = (
                f"{reason} · CSV GUARDADO EN PI"
                if saved_path is not None
                else f"{reason} · SIN CSV"
            )
            recording_view["manual_enabled"] = True
            if saved_path is not None:
                attempt_owner_download(saved_path, owner_id)

    sensorwatts_thread = threading.Thread(
        target=read_sensorwatts,
        name="sensorwatts-monitor",
        daemon=True,
    )
    sensorwatts_thread.start()

    def close_hardware() -> None:
        controlled_test_cancel.set()
        if controlled_test_thread is not None:
            controlled_test_thread.join(timeout=2.0)
        disable_output("PROGRAMA CERRADO · SALIDA 0 %")
        finish_recording()
        pwm.close()
        sensor_stop.set()
        if sensor_thread is not None:
            sensor_thread.join(timeout=2.0)
        sensorwatts_stop.set()
        sensorwatts_thread.join(timeout=args.sensorwatts_timeout + 0.5)

    app.on_shutdown(close_hardware)

    @ui.page("/")
    def index() -> None:
        nonlocal enabled, ui_loop
        nonlocal controlled_test_thread, controlled_test_owner_id
        client = ui.context.client
        ui_loop = asyncio.get_running_loop()

        def toggle_manual_recording() -> None:
            if controlled_test_running:
                ui.notify(
                    "El registro está administrado por la prueba controlada",
                    type="warning",
                )
                return
            with recording_lock:
                active = recording
            if active:
                saved_path = finish_recording()
                if saved_path is not None:
                    ui.download.file(
                        saved_path,
                        filename=saved_path.name,
                        media_type="text/csv",
                    )
                    ui.notify(
                        f"CSV guardado en la Raspberry: {saved_path.name}",
                        type="positive",
                    )
                return
            try:
                start_recording("sensorwatts")
            except Exception as error:
                ui.notify(
                    f"No fue posible iniciar el CSV: {describe_error(error)}",
                    type="negative",
                )

        def download_last_csv() -> None:
            path = last_saved_csv_path
            if path is None or not path.exists():
                ui.notify("No hay un CSV guardado disponible", type="warning")
                return
            ui.download.file(path, filename=path.name, media_type="text/csv")

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

        with ui.card().classes("w-full max-w-7xl"):
            ui.label("Indicadores").classes("text-h6 font-bold")
            with ui.row().classes(
                "w-full justify-between gap-6 flex-wrap"
            ):
                with ui.column().classes("items-center"):
                    ui.label("Temperatura").classes("text-caption")
                    ui.label().bind_text_from(
                        sensor_view,
                        "temperature",
                    ).classes("text-h5")
                with ui.column().classes("items-center"):
                    ui.label("Voltaje").classes("text-caption")
                    ui.label().bind_text_from(
                        sensorwatts_view,
                        "voltage",
                    ).classes("text-h5")
                with ui.column().classes("items-center"):
                    ui.label("Corriente").classes("text-caption")
                    ui.label().bind_text_from(
                        sensorwatts_view,
                        "current",
                    ).classes("text-h5")
                with ui.column().classes("items-center"):
                    ui.label("Potencia activa").classes("text-caption")
                    ui.label().bind_text_from(
                        sensorwatts_view,
                        "active_power",
                    ).classes("text-h5")
            ui.label().bind_text_from(
                sensor_view,
                "status",
            ).classes("text-caption text-grey-7")
            ui.label().bind_text_from(
                sensorwatts_view,
                "status",
            ).classes("text-caption text-grey-7")

        with ui.card().classes("w-full max-w-7xl") as controls_card:
            ui.label("Controles de prueba y registro").classes(
                "text-h6 font-bold"
            )
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

            with ui.row().classes("w-full gap-4"):
                controlled_step_input = ui.number(
                    label="Tiempo por paso (minutos)",
                    value=CONTROLLED_TEST_DEFAULT_STEP_MINUTES,
                    min=CONTROLLED_TEST_MIN_STEP_MINUTES,
                    max=CONTROLLED_TEST_MAX_STEP_MINUTES,
                    step=0.1,
                ).props("outlined").classes("grow")
                pwm_step_input = ui.number(
                    label="Paso PWM (%)",
                    value=10,
                    min=1,
                    max=100,
                    step=1,
                ).props("outlined").classes("grow")

            def selected_test_configuration() -> tuple[float, int] | None:
                try:
                    raw_minutes = float(controlled_step_input.value)
                    raw_step = float(pwm_step_input.value)
                except (TypeError, ValueError):
                    return None
                if not raw_step.is_integer():
                    return None
                minutes = raw_minutes
                step = int(raw_step)
                if not (
                    CONTROLLED_TEST_MIN_STEP_MINUTES
                    <= minutes
                    <= CONTROLLED_TEST_MAX_STEP_MINUTES
                ):
                    return None
                try:
                    controlled_test_levels(step)
                except ValueError:
                    return None
                return minutes, step

            def update_controlled_summary() -> None:
                configuration = selected_test_configuration()
                if configuration is None or controlled_test_running:
                    return
                minutes, step = configuration
                stages = len(controlled_test_levels(step))
                total = controlled_test_duration_minutes(minutes, step)
                controlled_test_view["status"] = (
                    f"PRUEBA DETENIDA · {stages} PASOS · "
                    f"{format_minutes(total)} MIN"
                )

            controlled_step_input.on_value_change(
                lambda: update_controlled_summary()
            )
            pwm_step_input.on_value_change(
                lambda: update_controlled_summary()
            )
            controlled_step_input.bind_enabled_from(
                controlled_test_view,
                "controls_enabled",
            )
            pwm_step_input.bind_enabled_from(
                controlled_test_view,
                "controls_enabled",
            )

            def toggle_controlled_test() -> None:
                nonlocal controlled_test_running, controlled_test_thread
                nonlocal controlled_test_owner_id, enabled
                if controlled_test_running:
                    controlled_test_cancel.set()
                    controlled_test_view["button"] = "CANCELANDO PRUEBA..."
                    controlled_test_view["status"] = (
                        "CANCELACIÓN SOLICITADA · FORZANDO SALIDA 0 %"
                    )
                    disable_output("CANCELACIÓN SOLICITADA · SALIDA 0 %")
                    return

                if args.max_duty < 100.0:
                    ui.notify(
                        "La prueba requiere --max-duty 100",
                        type="warning",
                    )
                    return
                configuration = selected_test_configuration()
                if configuration is None:
                    ui.notify(
                        "Usa un tiempo de 0.1 a 60 minutos y un paso PWM "
                        "entero que divida exactamente 100 "
                        "(por ejemplo 5, 10, 20 o 25)",
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

                minutes, step = configuration
                try:
                    if not start_recording("prueba_controlada"):
                        ui.notify(
                            "Ya existe un registro activo",
                            type="warning",
                        )
                        return
                except Exception as error:
                    ui.notify(
                        f"No fue posible crear el CSV interno: "
                        f"{describe_error(error)}",
                        type="negative",
                    )
                    return

                enabled = True
                set_output(0.0, "PRUEBA CONTROLADA INICIANDO")
                controlled_test_running = True
                controlled_test_cancel.clear()
                controlled_test_owner_id = client.id
                pwm_view["manual_controls_enabled"] = False
                pwm_view["slider_enabled"] = False
                controlled_test_view["button"] = (
                    "CANCELAR PRUEBA CONTROLADA"
                )
                controlled_test_view["controls_enabled"] = False
                recording_view["manual_enabled"] = False
                stages = len(controlled_test_levels(step))
                total = controlled_test_duration_minutes(minutes, step)
                controlled_test_view["status"] = (
                    f"INICIANDO · PASO {step} % · {stages} ETAPAS · "
                    f"{format_minutes(total)} MIN"
                )
                controlled_test_thread = threading.Thread(
                    target=run_controlled_test,
                    args=(minutes, step, client.id),
                    name="controlled-pwm-test",
                    daemon=True,
                )
                controlled_test_thread.start()

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

            ui.button(
                on_click=toggle_manual_recording,
            ).bind_text_from(
                recording_view,
                "button",
            ).bind_enabled_from(
                recording_view,
                "manual_enabled",
            ).classes("w-full")
            ui.button(
                "DESCARGAR ÚLTIMO CSV GUARDADO",
                on_click=download_last_csv,
            ).bind_visibility_from(
                recording_view,
                "has_saved_file",
            ).classes("w-full")
            ui.label().bind_text_from(
                recording_view,
                "status",
            ).classes("text-caption text-grey-7")
            ui.label(f"Directorio interno: {csv_dir}").classes(
                "text-caption text-grey-7"
            )

        main_cards = ui.element("div").classes(
            "grid grid-cols-1 lg:grid-cols-2 gap-4 "
            "w-full max-w-7xl items-stretch"
        )

        with ui.card().classes("w-full h-full") as active_pwm_card:
            ui.label("Control PWM").classes("text-h6 font-bold")
            ui.label().bind_text_from(
                pwm_view,
                "logical",
            ).classes("text-h3")
            ui.label().bind_text_from(
                pwm_view,
                "physical",
            ).classes("text-subtitle1")
            ui.label().bind_text_from(
                pwm_view,
                "voltage",
            ).classes("text-subtitle1")

            active_slider = ui.slider(
                min=0.0,
                max=args.max_duty,
                step=1.0,
                value=float(pwm_view["slider"]),
            ).classes("w-full")
            active_slider.bind_value_from(pwm_view, "slider")
            active_slider.bind_enabled_from(pwm_view, "slider_enabled")

            active_enable_switch = ui.switch(
                "Habilitar salida",
                value=bool(pwm_view["enabled"]),
            )
            active_enable_switch.bind_value_from(pwm_view, "enabled")
            active_enable_switch.bind_enabled_from(
                pwm_view,
                "manual_controls_enabled",
            )
            ui.label().bind_text_from(
                pwm_view,
                "status",
            ).classes("text-h6 font-bold")

            def change_active_duty(event) -> None:
                if controlled_test_running:
                    return
                set_output(float(event.value), "SALIDA HABILITADA")

            def change_active_enabled(event) -> None:
                nonlocal enabled
                if controlled_test_running:
                    return
                enabled = bool(event.value)
                if enabled:
                    set_output(
                        float(active_slider.value),
                        "SALIDA HABILITADA",
                    )
                else:
                    disable_output("SALIDA APAGADA")

            def active_emergency_stop() -> None:
                controlled_test_cancel.set()
                disable_output("PARO APLICADO · SALIDA 0 %")

            active_slider.on_value_change(change_active_duty)
            active_enable_switch.on_value_change(change_active_enabled)
            ui.button(
                "PARO · FORZAR 0 %",
                on_click=active_emergency_stop,
                color="negative",
            ).classes("w-full text-h6")
        active_pwm_card.move(main_cards)

        with chart_lock:
            initial_times = [round(value, 1) for value in chart_times_s]
            initial_temperatures = list(chart_temperatures_c)
        chart_options = {
            "animation": False,
            "title": {"text": "Temperatura vs tiempo"},
            "tooltip": {"trigger": "axis"},
            "xAxis": {
                "type": "category",
                "name": "Tiempo (s)",
                "data": initial_times,
            },
            "yAxis": {
                "type": "value",
                "name": "Temperatura (°C)",
                "scale": True,
            },
            "series": [
                {
                    "name": "Temperatura",
                    "type": "line",
                    "showSymbol": False,
                    "connectNulls": False,
                    "data": initial_temperatures,
                }
            ],
            "dataZoom": [
                {"type": "inside"},
                {"type": "slider"},
            ],
        }
        with ui.card().classes("w-full h-full") as chart_card:
            temperature_chart = ui.echart(chart_options).classes(
                "w-full h-96"
            )
            ui.label(
                "La gráfica comienza y se limpia al iniciar un registro "
                "manual o una prueba controlada."
            ).classes("text-caption text-grey-7")
        chart_card.move(main_cards)
        controls_card.move()

        with ui_clients_lock:
            ui_clients[client.id] = (client, temperature_chart)

        def disconnect_client() -> None:
            with ui_clients_lock:
                ui_clients.pop(client.id, None)
                another_client_connected = any(
                    candidate[0].has_socket_connection
                    for candidate in ui_clients.values()
                )
            if not controlled_test_running and not another_client_connected:
                disable_output(
                    "NAVEGADOR DESCONECTADO · SALIDA MANUAL 0 %"
                )

        client.on_disconnect(disconnect_client)

        ui.label(
            "La prueba controlada continúa en la Raspberry aunque el navegador "
            "pierda conexión. El paro local de la página solo funciona mientras "
            "el navegador esté conectado."
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
