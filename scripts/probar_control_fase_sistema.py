from __future__ import annotations

import argparse
import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prueba web autónoma del control de fase mediante RPi.GPIO"
        )
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


def logical_to_physical_duty(
    logical_duty_percent: float,
    active_duty_ceiling_percent: float,
) -> float:
    if not 0.0 < active_duty_ceiling_percent < 100.0:
        raise ValueError(
            "active_duty_ceiling_percent debe estar entre 0 y 100"
        )
    logical = min(100.0, max(0.0, float(logical_duty_percent)))
    if logical == 0.0:
        return 100.0
    return active_duty_ceiling_percent * (1.0 - logical / 100.0)


class PhaseControlPWM:
    def __init__(
        self,
        gpio: Any,
        pin: int,
        frequency_hz: float,
        active_duty_ceiling_percent: float,
    ) -> None:
        self._gpio = gpio
        self._pin = pin
        self._lock = threading.Lock()
        self._enabled = False
        self._requested_duty = 0.0
        self._applied_duty = 0.0
        self._physical_duty = 100.0
        self._active_duty_ceiling_percent = active_duty_ceiling_percent
        self._closed = False

        gpio.setwarnings(False)
        gpio.setmode(gpio.BCM)
        gpio.setup(pin, gpio.OUT, initial=gpio.LOW)
        self._pwm = gpio.PWM(pin, frequency_hz)
        self._pwm.start(100.0)

    def apply(self, enabled: bool, duty_percent: float) -> dict[str, float | bool]:
        with self._lock:
            if self._closed:
                raise RuntimeError("La salida PWM ya está cerrada")
            self._enabled = bool(enabled)
            self._requested_duty = min(100.0, max(0.0, float(duty_percent)))
            self._applied_duty = (
                self._requested_duty if self._enabled else 0.0
            )
            self._physical_duty = logical_to_physical_duty(
                self._applied_duty,
                self._active_duty_ceiling_percent,
            )
            self._pwm.ChangeDutyCycle(self._physical_duty)
            return self._state_unlocked()

    def stop(self) -> dict[str, float | bool]:
        return self.apply(False, 0.0)

    def state(self) -> dict[str, float | bool]:
        with self._lock:
            return self._state_unlocked()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._pwm.ChangeDutyCycle(100.0)
            self._pwm.stop()
            self._gpio.output(self._pin, self._gpio.HIGH)
            self._gpio.cleanup(self._pin)
            self._enabled = False
            self._requested_duty = 0.0
            self._applied_duty = 0.0
            self._physical_duty = 100.0
            self._closed = True

    def _state_unlocked(self) -> dict[str, float | bool]:
        return {
            "enabled": self._enabled,
            "requested_duty": self._requested_duty,
            "applied_duty": self._applied_duty,
            "physical_duty": self._physical_duty,
        }


def build_html(
    pin: int,
    frequency_hz: float,
    max_duty: float,
    active_duty_ceiling_percent: float,
) -> bytes:
    html = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Prueba PWM control de fase</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: system-ui, sans-serif;
      background: #f3f4f6;
      color: #111827;
    }}
    body {{ margin: 0; padding: 24px; }}
    main {{
      max-width: 680px;
      margin: auto;
      padding: 24px;
      background: white;
      border-radius: 16px;
      box-shadow: 0 8px 30px #0002;
    }}
    h1 {{ margin-top: 0; }}
    .hardware {{ color: #b91c1c; font-weight: 700; }}
    .duty {{ font-size: 3rem; font-weight: 700; margin: 12px 0 0; }}
    .voltage {{ font-size: 1.2rem; color: #4b5563; }}
    input[type="range"] {{ width: 100%; margin: 24px 0; }}
    label {{ display: flex; align-items: center; gap: 10px; font-size: 1.1rem; }}
    button {{
      width: 100%;
      margin-top: 24px;
      padding: 15px;
      border: 0;
      border-radius: 10px;
      background: #b91c1c;
      color: white;
      font-size: 1.15rem;
      font-weight: 700;
      cursor: pointer;
    }}
    .status {{ margin-top: 18px; font-weight: 700; }}
    .off {{ color: #15803d; }}
    .on {{ color: #b91c1c; }}
    .error {{ color: #b91c1c; }}
    small {{ display: block; margin-top: 22px; color: #6b7280; }}
  </style>
</head>
<body>
<main>
  <h1>Prueba manual del control de fase</h1>
  <p class="hardware">GPIO REAL · BCM{pin} · {frequency_hz:g} Hz</p>
  <p>No utiliza sensor ni PID. La salida inicia en 0 %.</p>

  <div id="duty" class="duty">0.0 %</div>
  <div id="physical" class="voltage">PWM físico: 100.0 %</div>
  <div id="voltage" class="voltage">Referencia estimada: 3.30 V</div>

  <input id="slider" type="range" min="0" max="{max_duty:g}"
         step="1" value="0" disabled>

  <label>
    <input id="enable" type="checkbox">
    Habilitar salida
  </label>

  <button id="stop" type="button">PARO · FORZAR 0 %</button>
  <div id="status" class="status off">SALIDA APAGADA</div>

  <small>
    El voltaje es una estimación ideal del PWM físico invertido.
    Confirma la señal real con un multímetro.
  </small>
</main>

<script>
  const slider = document.getElementById('slider');
  const enable = document.getElementById('enable');
  const stopButton = document.getElementById('stop');
  const duty = document.getElementById('duty');
  const physical = document.getElementById('physical');
  const voltage = document.getElementById('voltage');
  const status = document.getElementById('status');
  let pending = null;

  function updateDisplay(state) {{
    const applied = Number(state.applied_duty);
    const physicalDuty = Number(state.physical_duty);
    duty.textContent = `${{applied.toFixed(1)}} %`;
    physical.textContent = `PWM físico: ${{physicalDuty.toFixed(1)}} %`;
    voltage.textContent =
      `Referencia estimada: ${{(3.3 * physicalDuty / 100).toFixed(2)}} V`;
  }}

  function showState(state) {{
    updateDisplay(state);
    slider.disabled = !state.enabled;
    status.textContent = state.enabled
      ? 'SALIDA HABILITADA'
      : 'SALIDA APAGADA';
    status.className = `status ${{state.enabled ? 'on' : 'off'}}`;
  }}

  async function sendOutput() {{
    const response = await fetch('/api/output', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        enabled: enable.checked,
        duty: Number(slider.value),
      }}),
    }});
    if (!response.ok) throw new Error(await response.text());
    showState(await response.json());
  }}

  function scheduleOutput() {{
    clearTimeout(pending);
    pending = setTimeout(() => {{
      sendOutput().catch(showError);
    }}, 50);
  }}

  async function forceStop() {{
    clearTimeout(pending);
    enable.checked = false;
    slider.value = 0;
    const response = await fetch('/api/stop', {{method: 'POST'}});
    if (!response.ok) throw new Error(await response.text());
    showState(await response.json());
  }}

  function showError(error) {{
    status.textContent = `ERROR: ${{error.message}}`;
    status.className = 'status error';
  }}

  slider.addEventListener('input', scheduleOutput);
  enable.addEventListener('change', () => {{
    if (!enable.checked) slider.value = 0;
    sendOutput().catch(showError);
  }});
  stopButton.addEventListener('click', () => forceStop().catch(showError));

  window.addEventListener('pagehide', () => {{
    navigator.sendBeacon(
      '/api/stop',
      new Blob(['{{}}'], {{type: 'application/json'}}),
    );
  }});
</script>
</body>
</html>
"""
    return html.encode("utf-8")


def make_handler(
    controller: PhaseControlPWM,
    html: bytes,
) -> type[BaseHTTPRequestHandler]:
    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "PhaseControlTest/1.0"

        def do_GET(self) -> None:
            if self.path == "/":
                self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", html)
                return
            if self.path == "/api/state":
                self._send_json(HTTPStatus.OK, controller.state())
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Ruta no encontrada"})

        def do_POST(self) -> None:
            try:
                if self.path == "/api/output":
                    body = self._read_json()
                    enabled = bool(body.get("enabled", False))
                    duty = float(body.get("duty", 0.0))
                    self._send_json(
                        HTTPStatus.OK,
                        controller.apply(enabled, duty),
                    )
                    return
                if self.path == "/api/stop":
                    self._discard_body()
                    self._send_json(HTTPStatus.OK, controller.stop())
                    return
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "Ruta no encontrada"},
                )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": f"Solicitud inválida: {error}"},
                )
            except Exception as error:
                controller.stop()
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": f"Salida forzada a 0 %: {error}"},
                )

        def log_message(self, format: str, *args: Any) -> None:
            print(f"{self.client_address[0]} - {format % args}")

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1024:
                raise ValueError("tamaño de cuerpo no permitido")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("se esperaba un objeto JSON")
            return value

        def _discard_body(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if 0 < length <= 1024:
                self.rfile.read(length)

        def _send_json(
            self,
            status: HTTPStatus,
            value: dict[str, Any],
        ) -> None:
            self._send_bytes(
                status,
                "application/json; charset=utf-8",
                json.dumps(value).encode("utf-8"),
            )

        def _send_bytes(
            self,
            status: HTTPStatus,
            content_type: str,
            body: bytes,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return RequestHandler


def main() -> None:
    args = parse_args()
    validate_args(args)

    try:
        import RPi.GPIO as GPIO
    except ImportError as error:
        raise SystemExit(
            "No se pudo importar RPi.GPIO. Ejecuta con /usr/bin/python3 "
            "en la Raspberry Pi."
        ) from error

    controller = PhaseControlPWM(
        GPIO,
        args.pin,
        args.frequency,
        active_duty_ceiling_percent=args.active_ceiling,
    )
    html = build_html(
        args.pin,
        args.frequency,
        args.max_duty,
        active_duty_ceiling_percent=args.active_ceiling,
    )
    server = HTTPServer(
        (args.host, args.port),
        make_handler(controller, html),
    )

    print(
        f"GPIO real: BCM{args.pin}; {args.frequency:g} Hz; "
        f"máximo {args.max_duty:g} %"
    )
    print("Salida inicial: 0 %")
    print(f"Abre http://IP_DE_LA_RASPBERRY:{args.port}")
    print("Ctrl+C apaga la salida y cierra el servidor.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando prueba...")
    finally:
        server.server_close()
        controller.close()
        print("Salida forzada a 0 % y GPIO liberado.")


if __name__ == "__main__":
    main()
