from __future__ import annotations

from collections import deque
from datetime import datetime

from nicegui import app, ui

from .config import AppConfig
from .controller import TemperatureController


def run_ui(controller: TemperatureController, config: AppConfig) -> None:
    history: deque[tuple[str, float, float]] = deque(maxlen=300)

    @ui.page("/")
    def index() -> None:
        state = controller.get_state()
        ui.label(config.application.title).classes("text-h4 font-bold")
        ui.label("Raspberry Pi 3 B+ · Vernier GDX-TCA · PID").classes(
            "text-subtitle1 text-grey-7"
        )

        with ui.row().classes("w-full gap-4"):
            with ui.card().classes("min-w-64"):
                ui.label("Temperatura actual").classes("text-subtitle2")
                temperature_label = ui.label("--.- °C").classes("text-h3")
            with ui.card().classes("min-w-64"):
                ui.label("Salida PWM").classes("text-subtitle2")
                duty_label = ui.label("0.0 %").classes("text-h3")
            with ui.card().classes("min-w-64"):
                ui.label("Temperatura ambiente inicial").classes("text-subtitle2")
                ambient_label = ui.label("--.- °C").classes("text-h3")

        with ui.card().classes("w-full max-w-3xl"):
            ui.label("Temperatura objetivo").classes("text-h6")
            setpoint_label = ui.label(f"{state.setpoint_c:.1f} °C").classes("text-h4")
            slider = ui.slider(
                min=config.control.setpoint_min_c,
                max=config.control.setpoint_max_c,
                step=0.5,
                value=state.setpoint_c,
            ).classes("w-full")

            def change_setpoint(event) -> None:
                controller.set_setpoint(float(event.value))
                setpoint_label.set_text(f"{float(event.value):.1f} °C")

            slider.on_value_change(change_setpoint)
            enable_switch = ui.switch("Habilitar control PID", value=False)
            enable_switch.on_value_change(
                lambda event: controller.set_enabled(bool(event.value))
            )

        status = ui.label("Iniciando").classes("text-subtitle1")
        fault = ui.label("").classes("text-negative font-bold")
        chart = ui.echart(
            {
                "tooltip": {"trigger": "axis"},
                "legend": {"data": ["Temperatura", "Objetivo"]},
                "xAxis": {"type": "category", "data": []},
                "yAxis": {"type": "value", "name": "°C"},
                "series": [
                    {"name": "Temperatura", "type": "line", "data": [], "showSymbol": False},
                    {"name": "Objetivo", "type": "line", "data": [], "showSymbol": False},
                ],
            }
        ).classes("w-full h-80")

        applied_ambient_min: float | None = None

        def refresh() -> None:
            nonlocal applied_ambient_min
            current = controller.get_state()
            temperature_label.set_text(
                "--.- °C"
                if current.temperature_c is None
                else f"{current.temperature_c:.1f} °C"
            )
            duty_label.set_text(f"{current.duty_percent:.1f} %")
            ambient_label.set_text(
                "--.- °C"
                if current.ambient_temperature_c is None
                else f"{current.ambient_temperature_c:.1f} °C"
            )
            if (
                current.ambient_temperature_c is not None
                and current.ambient_temperature_c != applied_ambient_min
            ):
                applied_ambient_min = current.ambient_temperature_c
                slider.props(f"min={applied_ambient_min:.1f}")
            status.set_text(current.status)
            fault.set_text(current.fault or "")
            if enable_switch.value != current.enabled:
                enable_switch.set_value(current.enabled)
            if current.temperature_c is not None:
                history.append(
                    (
                        datetime.now().strftime("%H:%M:%S"),
                        current.temperature_c,
                        current.setpoint_c,
                    )
                )
                chart.options["xAxis"]["data"] = [item[0] for item in history]
                chart.options["series"][0]["data"] = [item[1] for item in history]
                chart.options["series"][1]["data"] = [item[2] for item in history]
                chart.update()

        ui.timer(1.0, refresh)

    app.on_shutdown(controller.stop)
    controller.start()
    ui.run(
        host=config.application.host,
        port=config.application.port,
        title=config.application.title,
        reload=False,
    )
