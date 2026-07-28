# Instalación en Raspberry Pi 3 B+

Entorno identificado:

- Raspberry Pi OS 13 `trixie`, 32 bits.
- Python 3.13.5.
- Kernel 6.18 para Raspberry Pi, arquitectura `armv7l`.

## Razón del procedimiento

NiceGUI y godirect son compatibles con Python 3.13. Sin embargo, algunas
dependencias compiladas no publican siempre ruedas ARMv7. Se aprovechan los
paquetes binarios de Raspberry Pi OS y se crea un entorno virtual con acceso a
ellos para evitar compilaciones innecesarias.

## Paquetes del sistema

Revisar la lista antes de ejecutar. Estos comandos todavía no han sido
ejecutados por Claw:

```bash
sudo apt update
sudo apt install \
  bluetooth \
  bluez \
  build-essential \
  libhidapi-dev \
  libusb-1.0-0 \
  libusb-1.0-0-dev \
  libudev-dev \
  python3-dev \
  python3-gpiozero \
  python3-hid \
  python3-rpi.gpio \
  python3-venv
```

## Entorno virtual

Desde la carpeta principal del proyecto:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
```

El uso de `--system-site-packages` es intencional: permite reutilizar las
extensiones ARMv7 proporcionadas por APT, especialmente HID y GPIO.

## Diagnóstico sin activar la hornilla

```bash
source .venv/bin/activate
python scripts/diagnostico_raspberry.py
PYTHONPATH=src python -m unittest discover -s tests -v
```

El diagnóstico no crea la salida PWM ni energiza la carga.

## Prueba inicial

Mantener estos valores mientras se valida la instalación:

```toml
[sensor]
backend = "simulated"

[pwm]
backend = "simulated"
```

Después:

```bash
control-temperatura-pi --config config.toml
```

Abrir `http://IP_DE_LA_RASPBERRY:8080`.

## Activación por etapas

1. Probar la interfaz con sensor y PWM simulados.
2. Cambiar solo `sensor.backend` a `vernier` y comprobar lecturas por BLE.
3. Mantener la hornilla desconectada y cambiar solo `pwm.backend` a `gpiozero`.
4. Medir con multímetro u osciloscopio la salida filtrada para 0, 25, 50, 75 y
   100 %.
5. Confirmar que 0 % produce 0 V y que al cerrar la aplicación la señal vuelve a
   0 V.
6. Conectar el control de fase y limitar inicialmente el duty máximo.
7. Caracterizar temperatura máxima y ajustar PID con supervisión.

GPIO18 admite PWM hardware en Raspberry Pi. La implementación inicial usa
`PWMOutputDevice` de GPIO Zero; como la señal pasa por un filtro RC de 5.9 Hz,
el PWM de 1 kHz no exige la misma precisión temporal que un disparo de TRIAC
directo. No se instalará `pigpio` desde fuentes salvo que una medición demuestre
que el backend incluido no es suficientemente estable.
