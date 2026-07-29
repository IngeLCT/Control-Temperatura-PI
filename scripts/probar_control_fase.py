from __future__ import annotations

import argparse
import threading
import time

from control_temperatura_pi.pwm import GPIOZeroPWMOutput, SimulatedPWMOutput


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
        "--watchdog",
        type=float,
        default=30.0,
        help="Segundos de inactividad antes de apagar; 0 deshabilita el watchdog",
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
    if args.watchdog < 0:
        raise SystemExit("--watchdog no puede ser negativo")
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port debe estar entre 1 y 65535")


def main() -> None:
    args = parse_args()
    validate_args(args)

    from nicegui import app, ui

    pwm = (
        GPIOZeroPWMOutput(
            bcm_pin=args.pin,
            frequency_hz=args.frequency,
            active_high=True,
        )
        if args.real
        else SimulatedPWMOutput()
    )
    lock = threading.Lock()
    enabled = False
    requested_duty = 0.0
    last_activity = time.monotonic()

    def set_output(duty_percent: float) -> float:
        nonlocal requested_duty, last_activity
        with lock:
            requested_duty = min(args.max_duty, max(0.0, duty_percent))
            last_activity = time.monotonic()
            applied = requested_duty if enabled else 0.0
            pwm.set_duty_percent(applied)
            return applied

    def disable_output() -> None:
        nonlocal enabled, requested_duty, last_activity
        with lock:
            enabled = False
            requested_duty = 0.0
            last_activity = time.monotonic()
            pwm.set_duty_percent(0.0)

    def close_output() -> None:
        disable_output()
        pwm.close()

    app.on_shutdown(close_output)

    @ui.page("/")
    def index() -> None:
        nonlocal enabled, last_activity

        ui.label("Prueba manual del control de fase").classes("text-h4 font-bold")
        mode = "GPIO REAL" if args.real else "SIMULACIÓN"
        mode_color = "text-negative" if args.real else "text-primary"
        ui.label(
            f"{mode} · BCM{args.pin} · {args.frequency:g} Hz"
        ).classes(f"text-h6 font-bold {mode_color}")
        ui.label(
            "No utiliza sensor de temperatura ni PID. La salida inicia en 0 %."
        ).classes("text-subtitle1")

        with ui.card().classes("w-full max-w-2xl"):
            ui.label("Duty PWM solicitado").classes("text-subtitle2")
            duty_label = ui.label("0.0 %").classes("text-h3")
            voltage_label = ui.label("Referencia estimada: 0.00 V").classes(
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
            watchdog_label = ui.label("")

            def update_labels(applied: float) -> None:
                duty_label.set_text(f"{applied:.1f} %")
                voltage_label.set_text(
                    f"Referencia estimada: {3.3 * applied / 100.0:.2f} V"
                )

            def change_duty(event) -> None:
                applied = set_output(float(event.value))
                update_labels(applied)

            def change_enabled(event) -> None:
                nonlocal enabled, last_activity
                enabled = bool(event.value)
                last_activity = time.monotonic()
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

            def refresh_watchdog() -> None:
                if args.watchdog == 0:
                    watchdog_label.set_text("Watchdog deshabilitado")
                    return
                remaining = max(
                    0.0, args.watchdog - (time.monotonic() - last_activity)
                )
                watchdog_label.set_text(
                    f"Apagado por inactividad en {remaining:.0f} s"
                    if enabled
                    else f"Watchdog: {args.watchdog:g} s"
                )
                if enabled and remaining <= 0.0:
                    emergency_stop()
                    status_label.set_text("WATCHDOG · SALIDA FORZADA A 0 %")

            ui.timer(0.5, refresh_watchdog)

        ui.label(
            "El voltaje mostrado es una estimación ideal de 3.3 V × duty. "
            "Confirma el valor real con multímetro."
        ).classes("text-caption text-grey-7")

    print(
        f"Modo: {'GPIO real' if args.real else 'simulado'}; "
        f"BCM{args.pin}; {args.frequency:g} Hz; máximo {args.max_duty:g} %"
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
