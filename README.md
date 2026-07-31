# Control Temperatura PI

Control PID de temperatura para Raspberry Pi 3 Model B+, un amplificador de
termopar Vernier Go Direct GDX-TCA y una etapa de potencia gobernada por PWM.
Incluye una interfaz web con slider de temperatura objetivo, gráfica, estado del
sensor y porcentaje de salida.

## Estado de esta primera versión

- Modo simulado activado de fábrica.
- Control PID con salida limitada entre 0 y 100 %.
- Salida física al 100 % (aproximadamente 3.3 V) al iniciar, detener el control,
  perder la lectura o detectar sobretemperatura; en la etapa invertida esto
  corresponde a potencia térmica apagada.
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
active_high = true
active_duty_ceiling_percent = 80.0
```

No conectar una resistencia calefactora directamente al GPIO. Se necesita una
etapa de potencia apropiada (MOSFET o SSR según la carga), fuente independiente,
masa común cuando corresponda, fusible y corte térmico físico. Antes de elegir la
frecuencia PWM y la etapa hay que conocer tensión, corriente, potencia y tipo de
carga.

## Interfaz con el control de fase existente

La salida GPIO18 no gobierna directamente el BTA08-600B. Genera PWM de 1 kHz y
el filtro de 27 kΩ + 1 µF lo convierte en una referencia aproximada de 0 a 3.3 V
aplicada entre `SP` y `GND` del controlador de fase ya probado. La etapa medida
es inversa: menor voltaje significa mayor potencia.

- Constante de tiempo RC: 27 ms.
- Frecuencia de corte aproximada: 5.9 Hz.
- Tiempo de establecimiento aproximado a 1 %: 124 ms.
- 0 % de demanda térmica equivale a 100 % PWM físico, aproximadamente 3.3 V y
  carga apagada.
- 100 % de demanda térmica equivale a 0 % PWM físico, 0 V y máxima potencia.
- El controlador de fase apaga la carga desde aproximadamente 2.64 V, equivalente
  a 80 % PWM físico.
- Para eliminar ese rango muerto, cualquier demanda térmica positiva se escala
  desde 80 % hasta 0 % PWM físico. Por ello hay un salto deliberado: 0 % lógico
  usa 100 % físico; 1 % lógico usa aproximadamente 79.2 % físico.
- Carga informada: hornilla de 120 VAC y 5 A.
- Dispositivo de potencia: BTA08-600B en M1, M2 y G.

La potencia de un control por ángulo de fase no necesariamente es lineal respecto
al voltaje `SP`. El PID manda una demanda térmica normalizada de 0 a 100 % y la
capa GPIO realiza la inversión y compensación del rango muerto. El comportamiento
real deberá caracterizarse y ajustarse en el equipo.

### Prueba manual del control de fase con temperatura de referencia

El script aislado `scripts/probar_control_fase.py` ofrece un slider de 0 a 100 %
para controlar manualmente la potencia sin ejecutar el PID. Opcionalmente puede
mostrar la temperatura del Vernier como referencia; la lectura no modifica la
demanda térmica ni la salida PWM. El script inicia siempre con demanda térmica
0 % y PWM físico 100 %, exige habilitación manual e incluye botón de paro. La
prueba controlada se ejecuta dentro de la Raspberry y continúa aunque el
navegador pierda conexión.

Primero se puede comprobar la interfaz con PWM simulado:

```bash
python scripts/probar_control_fase.py
```

Para probar GPIO18 con la hornilla desconectada y medir `SP` respecto a `GND`:

```bash
python scripts/probar_control_fase.py --real
```

Para buscar y conectar un Vernier usando la conexión definida en la sección
`[sensor]` de `config.toml`, y mostrar su temperatura únicamente como referencia:

```bash
python scripts/probar_control_fase.py --real --sensor
```

La opción `--sensor` habilita el flujo manual del Vernier, pero no abre
automáticamente la conexión BLE al arrancar. Primero se pulsa
`ESCANEAR SENSORES GDX`; la lista muestra únicamente dispositivos cuyo nombre
comienza exactamente con `GDX`. Después se elige uno y se pulsa
`CONECTAR SENSOR SELECCIONADO`. El escaneo no abre sensores ni genera lecturas.
Si el escaneo o la conexión fallan, la interfaz muestra el error y permite
reintentar.

La aplicación consulta el nivel de batería informado por el GDX al conectarlo y
lo actualiza cada cinco minutos desde el mismo hilo Bluetooth de temperatura.
No muestra un indicador permanente. Cuando el nivel cruza de 20 % o más a un
valor menor de 20 %, presenta una sola advertencia para recargar el sensor. Si
en ese momento no hay navegador conectado, la advertencia queda pendiente y se
muestra al volver a abrir la página. Una falla al consultar la batería no
interrumpe las lecturas de temperatura, la prueba controlada ni el CSV.

La misma página consulta cada segundo el endpoint de `SensorWatts`
`http://192.168.1.211/readings` y muestra voltaje, corriente y potencia activa.
El botón `INICIAR REGISTRO CSV` comienza a registrar solo
desde ese momento. Al pulsar `DETENER Y DESCARGAR CSV`, la página descarga el
archivo. Cada muestra se escribe progresivamente en
`data/registros/` dentro de la Raspberry, de modo que el archivo no depende de
la memoria RAM ni de que el navegador permanezca conectado. Se puede cambiar
el directorio con `--csv-dir`.

El CSV contiene las columnas separadas `Fecha` (`DD-MM-YYYY`) y `Hora`
(`HH:MM:SS`), además de tiempo transcurrido, porcentaje lógico del slider,
voltaje estimado de referencia, porcentaje PWM físico, temperatura, voltaje de
red, corriente y potencia activa. Se agrega una fila cada segundo mientras el
registro está activo. Si SensorWatts entrega una lectura inválida o deja de
responder, la fila conserva tiempo, PWM y temperatura. Cada campo eléctrico se
valida por separado: por ejemplo, un voltaje inválido deja vacío `Voltaje_V`,
pero conserva `Corriente_A` y `Potencia_Activa_W` si esos valores sí son
numéricos. La Raspberry usa la última respuesta reciente recibida y el registro
continúa; los campos vuelven a completarse automáticamente cuando SensorWatts se
recupera. La prueba controlada y el reloj del CSV no requieren SensorWatts.

El campo `Tiempo por paso (minutos)` permite elegir valores decimales entre
0.1 y 60; el valor predeterminado es 3. El tiempo se convierte internamente a
segundos: por ejemplo, 0.5 minutos son 30 segundos y 1.5 minutos son 90
segundos por etapa. `Paso PWM (%)` acepta divisores enteros de 100, por ejemplo
5, 10, 20 o 25. Con `Ciclo completo` marcado, la prueba sube hasta 100 %,
baja con el mismo paso y finalmente fuerza 0 %. Con la casilla desmarcada,
solo sube hasta 100 %, mantiene ese nivel durante el tiempo configurado y
después fuerza directamente 0 %. La interfaz calcula la cantidad de etapas y
la duración total según el tiempo, el paso y el modo elegido.

La prueba controlada no inicia ni detiene automáticamente el registro CSV.
El archivo se crea únicamente al pulsar `INICIAR REGISTRO CSV` y continúa
recibiendo muestras hasta pulsar `DETENER Y DESCARGAR CSV`, incluso si la prueba
termina o el navegador se desconecta. Al reconectar, la interfaz conserva el
estado activo y permite detener el registro. Durante la prueba quedan bloqueados
los campos, el slider y el interruptor de habilitación, pero el control del CSV
permanece disponible. `CANCELAR PRUEBA CONTROLADA` y el paro detienen la
secuencia y fuerzan 0 %.
Fuera de la prueba controlada, la salida manual conserva el apagado de seguridad
cuando se desconecta el último navegador.

La pantalla agrupa temperatura, voltaje, corriente y potencia activa en una
tarjeta superior. Esa misma tarjeta muestra el paso actual respecto del total,
el tiempo restante del paso en curso y la cantidad de filas escritas en el CSV.
Los tres indicadores usan el estado de la Raspberry y se recuperan al volver a
conectar el navegador. Debajo aparecen lado a lado el control PWM y la gráfica
`Temperatura vs tiempo`. La tarjeta con los controles de sensores, tiempos,
paso PWM, prueba controlada y CSV queda debajo de ambos. La gráfica se limpia
y comienza una sesión Plotly nueva desde cero cuando inicia un registro CSV.
Iniciar después la prueba controlada no borra esa sesión: la curva continúa
creciendo hasta detener manualmente el registro. Plotly conserva el zoom y el
desplazamiento durante una misma sesión, pero restablece la vista al comenzar
un CSV nuevo. Cuando no hay una prueba ni un registro en curso, carga
automáticamente `Tiempo_s` y `Temperatura_C` del último CSV guardado y muestra
un selector dentro de la tarjeta para cambiar entre todos los CSV disponibles.
El selector se oculta mientras haya una prueba controlada o un registro activo,
y vuelve a aparecer al terminar ambos. Un CSV recién guardado se agrega a la
lista y queda seleccionado automáticamente. La gráfica muestra todas las
mediciones de temperatura del CSV, sin muestreo ni límite de puntos. Durante un
registro en vivo, Plotly recibe únicamente los puntos nuevos mediante
actualizaciones incrementales para evitar reenviar el historial completo cada
segundo. La curva de temperatura se actualiza con las mismas filas de un segundo
mientras el registro está activo y no depende de que una consulta puntual a
SensorWatts termine correctamente. Si el navegador se reconecta durante un
registro, recupera la curva completa acumulada en la Raspberry.

Si cambia la dirección del medidor, se puede indicar otro endpoint:

```bash
python scripts/probar_control_fase.py --real --sensor \
  --sensorwatts-url http://192.168.1.211/readings
```

Para guardar los CSV en otra carpeta:

```bash
python scripts/probar_control_fase.py --real --sensor \
  --csv-dir /home/lct/Documents/Proyectos/Control-Temperatura-PI/registros
```

La interfaz queda en `http://IP_DE_LA_RASPBERRY:8081`. Para limitar una primera
prueba, por ejemplo a 25 %, usar `--max-duty 25`.

Si el entorno virtual no puede usar el controlador GPIO instalado por el sistema,
existe una prueba autónoma sin NiceGUI ni gpiozero:

```bash
sudo /usr/bin/python3 scripts/probar_control_fase_sistema.py
```

Este script utiliza directamente `RPi.GPIO` y un servidor HTTP de la biblioteca
estándar de Python. También inicia y termina con demanda térmica 0 % y PWM físico
100 %, ofrece habilitación manual y botón de paro, y sirve la interfaz en el
puerto 8081.

Como 0 V representa máxima potencia, el apagado no debe depender solo del
software. La entrada `SP` debe tener un estado físico de fallo seguro que la
mantenga en el nivel de apagado si la Raspberry se reinicia, pierde alimentación
o libera GPIO18, además de fusible térmico e interbloqueo independientes.

El Bluetooth USB genérico administrado por BlueZ usa `ble_backend = "native"`.
Solo debe elegirse `bluegiga` si el adaptador es específicamente un dongle
Bluegiga compatible con el backend heredado de Vernier.

El equipo objetivo ya fue identificado con un Cypress CYW20704A2 USB
(`04b4:f901`), administrado por BlueZ como `hci0`. La guía de instalación incluye
los pasos para retirar el bloqueo de software antes de buscar el sensor.

El sensor previsto para el control principal es `GDX-TCA 1C1002R9`, observado en
la dirección BLE `3C:2E:F5:62:94:79`. El control principal conserva la
coincidencia exacta configurada. La página de prueba manual, en cambio, permite
escanear y seleccionar cualquiera de los dispositivos cuyo nombre comience con
`GDX`; nunca se conecta automáticamente al más cercano.

## Ajuste PID

Los valores iniciales de `kp`, `ki` y `kd` son únicamente conservadores para el
simulador. No deben darse por calibrados para el equipo real. El ajuste debe
realizarse con límites bajos, observación continua y protección térmica
independiente.
