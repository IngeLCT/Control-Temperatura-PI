# Control Temperatura PI

Control PID de temperatura para Raspberry Pi 3 Model B+, un amplificador de
termopar Vernier Go Direct GDX-TCA y una etapa de potencia gobernada por PWM.
Incluye una interfaz web con slider de temperatura objetivo, gráfica, estado del
sensor y porcentaje de salida.

## Estado de esta primera versión

- Modo simulado activado de fábrica.
- Control PID con salida limitada entre 0 y 100 %.
- Salida apagada al iniciar, al detener el control, ante pérdida de lectura y por
  sobretemperatura.
- Slider desde la temperatura ambiente inicial hasta 300 °C.
- En el extremo de temperatura ambiente, la referencia fuerza una salida de 0 %.
- Corte lógico provisional a 320 °C, pendiente de la caracterización térmica.
- Backend Vernier por USB o Bluetooth Low Energy.
- Backend PWM mediante `gpiozero`; GPIO BCM 18 y 1 kHz por defecto.
- Interfaz web disponible en el puerto 8080.

## Recursos oficiales de Vernier

- Manual GDX-TCA: https://www.vernier.com/manuals/gdx-tca/
- Guía Python Go Direct:
  https://vernierst.github.io/godirect-examples/python/
- Ejemplos oficiales: https://github.com/VernierST/godirect-examples
- Ayuda específica para Raspberry Pi:
  https://www.vernier.com/til/10072

Vernier especifica un rango de -200 a 1400 °C para termopar tipo K y una
precisión típica de ±2.2 °C o 0.75 % de la lectura, la que sea mayor. El GDX-TCA
admite termopares K, J y T, pero son canales mutuamente excluyentes; esta versión
usa el canal predeterminado tipo K.

Vernier clasifica estos productos para uso educativo, no para procesos
industriales, médicos o comerciales. Este software tampoco sustituye protecciones
físicas independientes.

## Preparación de Raspberry Pi OS

El equipo objetivo usa Raspberry Pi OS 13 Trixie de 32 bits, Python 3.13.5 y
kernel 6.18. La guía detallada está en
`docs/INSTALACION_RASPBERRY_PI.md`.

Resumen; estos comandos todavía no se han ejecutado:

```bash
sudo apt install python3-venv python3-dev python3-hid python3-gpiozero python3-rpi.gpio libhidapi-dev libusb-1.0-0 libusb-1.0-0-dev libudev-dev bluetooth bluez build-essential
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Para USB en Linux, Vernier requiere una regla `udev` para el identificador de
fabricante USB `08f7`. Debe usarse la regla oficial publicada en su repositorio de
ejemplos y reconectar después el sensor.

## Primera ejecución segura

Con `sensor.backend = "simulated"` y `pwm.backend = "simulated"`:

```bash
source .venv/bin/activate
control-temperatura-pi --config config.toml
```

Abrir `http://IP_DE_LA_RASPBERRY:8080`. El control permanece detenido hasta
accionar el interruptor de la interfaz.

Las pruebas no requieren dependencias adicionales:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Activación de hardware

Después de probar el circuito y confirmar que un estado bajo apaga físicamente la
etapa de potencia, cambiar:

```toml
[sensor]
backend = "vernier"
connection = "ble"
ble_backend = "native"

[pwm]
backend = "gpiozero"
bcm_pin = 18
```

No conectar una resistencia calefactora directamente al GPIO. Se necesita una
etapa de potencia apropiada (MOSFET o SSR según la carga), fuente independiente,
masa común cuando corresponda, fusible y corte térmico físico. Antes de elegir la
frecuencia PWM y la etapa hay que conocer tensión, corriente, potencia y tipo de
carga.

## Interfaz con el control de fase existente

La salida GPIO18 no gobierna directamente el BTA08-600B. Genera PWM de 1 kHz y
el filtro de 27 kΩ + 1 µF lo convierte en una referencia aproximada de 0 a 3.3 V
aplicada entre `SP` y `GND` del controlador de fase ya probado.

- Constante de tiempo RC: 27 ms.
- Frecuencia de corte aproximada: 5.9 Hz.
- Tiempo de establecimiento aproximado a 1 %: 124 ms.
- 0 % PWM equivale a 0 V y hornilla apagada.
- 100 % PWM equivale aproximadamente a 3.3 V y máxima potencia.
- Carga informada: hornilla de 120 VAC y 5 A.
- Dispositivo de potencia: BTA08-600B en M1, M2 y G.

La potencia de un control por ángulo de fase no necesariamente es lineal respecto
al voltaje `SP`. Por ello el PID mandará una orden normalizada de 0 a 100 %, pero
el comportamiento real deberá caracterizarse y ajustarse en el equipo.

El Bluetooth USB genérico administrado por BlueZ usa `ble_backend = "native"`.
Solo debe elegirse `bluegiga` si el adaptador es específicamente un dongle
Bluegiga compatible con el backend heredado de Vernier.

El equipo objetivo ya fue identificado con un Cypress CYW20704A2 USB
(`04b4:f901`), administrado por BlueZ como `hci0`. La guía de instalación incluye
los pasos para retirar el bloqueo de software antes de buscar el sensor.

El sensor autorizado es `GDX-TCA 1C1002R9`, observado en la dirección BLE
`3C:2E:F5:62:94:79`. La aplicación exige coincidencia exacta del nombre y no se
conecta automáticamente a otro Go Direct más cercano.

## Ajuste PID

Los valores iniciales de `kp`, `ki` y `kd` son únicamente conservadores para el
simulador. No deben darse por calibrados para el equipo real. El ajuste debe
realizarse con límites bajos, observación continua y protección térmica
independiente.
