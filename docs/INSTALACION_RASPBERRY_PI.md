# Instalación en Raspberry Pi 3 B+

Entorno identificado:

- Raspberry Pi OS 13 `trixie`, 32 bits.
- Python 3.13.5.
- Kernel 6.18 para Raspberry Pi, arquitectura `armv7l`.
- Adaptador BLE USB Cypress CYW20704A2 (`04b4:f901`).
- El adaptador USB aparece actualmente como `hci0`, dirección
  `00:16:A4:D7:2E:DF`, y es el controlador predeterminado.
- El Bluetooth interno aparece como `hci1`, dirección
  `B8:27:EB:9E:8B:BE`.
- Sensor autorizado: `GDX-TCA 1C1002R9`.
- Dirección BLE observada: `3C:2E:F5:62:94:79`.

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

## Bluetooth Vernier

El adaptador Cypress es un controlador BLE genérico administrado por BlueZ. Debe
usarse:

```toml
[sensor]
connection = "ble"
ble_backend = "native"
device_name = "GDX-TCA 1C1002R9"
```

No instalar `vernierpygatt`: ese paquete solo corresponde al dongle Bluegiga
heredado.

En el diagnóstico inicial, tanto `hci0` como `hci1` aparecieron bloqueados por
software. Antes de buscar el GDX-TCA:

```bash
sudo rfkill unblock bluetooth
bluetoothctl list
bluetoothctl show 00:16:A4:D7:2E:DF
rfkill list bluetooth
```

El adaptador USB debe mostrar `Powered: yes` y `Soft blocked: no`. Si permanece
apagado:

```bash
bluetoothctl
select 00:16:A4:D7:2E:DF
power on
quit
```

El backend actual de `godirect` utiliza Bleak/BlueZ y no expone una opción para
fijar explícitamente `hci0`. En este equipo no es necesario porque el Cypress es
`hci0` y está marcado como controlador predeterminado. Debe volver a verificarse
después de reinicios o cambios de adaptadores USB.

La aplicación no utiliza emparejamiento por proximidad cuando `device_name` está
configurado. Escanea los Go Direct disponibles y solo abre el nombre exacto
`GDX-TCA 1C1002R9`; si no aparece, mantiene el PWM apagado y reporta un fallo.

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
2. Probar el sensor de forma aislada, sin crear GPIO:

   ```bash
   source .venv/bin/activate
   python scripts/probar_sensor_vernier.py --samples 10
   ```

3. Cambiar solo `sensor.backend` a `vernier` y comprobar lecturas por BLE en la
   interfaz.
4. Mantener la hornilla desconectada y cambiar solo `pwm.backend` a `gpiozero`.
5. Medir con multímetro u osciloscopio la salida filtrada para 0, 25, 50, 75 y
   100 %.
6. Confirmar que 0 % de demanda térmica produce aproximadamente 3.3 V, que 100 %
   produce aproximadamente 0 V y que al cerrar la aplicación la señal vuelve a
   3.3 V, correspondiente al apagado de la etapa invertida.
   Valores ideales intermedios: 25 % lógico ≈ 1.98 V, 50 % ≈ 1.32 V y 75 %
   ≈ 0.66 V.
7. Conectar el control de fase y limitar inicialmente el duty máximo.
8. Caracterizar temperatura máxima y ajustar PID con supervisión.

GPIO18 admite PWM hardware en Raspberry Pi. La implementación inicial usa
`PWMOutputDevice` de GPIO Zero; como la señal pasa por un filtro RC de 5.9 Hz,
el PWM de 1 kHz no exige la misma precisión temporal que un disparo de TRIAC
directo. No se instalará `pigpio` desde fuentes salvo que una medición demuestre
que el backend incluido no es suficientemente estable.

Debido a que 0 V ordena máxima potencia, se requiere un estado físico de fallo
seguro en `SP` que mantenga el apagado durante arranque, reinicio, pérdida de
alimentación o liberación del GPIO. El apagado por software no sustituye fusible
térmico, termostato o interbloqueo independiente.
