# DtMd - Sistema de Alerta y Vigilancia De Drones

**DtMd** es una solución de ingeniería de telecomunicaciones diseñada para la detección pasiva, temprana y en tiempo real de Vehículos Aéreos No Tripulados (UAVs) sobre la banda ISM de 2.4 GHz. El software implementa un algoritmo adaptativo de Procesamiento Digital de Señales (DSP) asíncrono que analiza firmas espectrales en el dominio de la frecuencia mediante Radio Definido por Software (SDR).

---

## 📦 Tecnologías y librerías utilizadas

- **Python 3.11**
- **NumPy**: Manipulación analítica de vectores complejos y cálculo de la FFT.
- **Asyncio**: Programación asíncrona mediante bucles de eventos (*event loops*).
- **WebSockets**: Protocolo de red de bajo nivel para streaming de telemetría con latencia < 30ms.
- **HackRF Tools (`hackrf_transfer`)**: Utilidad nativa de Linux para captura cruda de muestras IQ a 8 Msps.
- **HTML5, JavaScript Nativo y Tailwind CSS**: Interfaz web (HMI) asíncrona estilo dashboard táctico.

---

## 🧠 Características principales

- **Detección Invariante:** Funciona sin importar si el dron se enciende antes o después de arrancar el script (inmunidad al sesgo de calibración estática).
- **Filtro de DC Offset por Software:** Anulación quirúrgica de la espícula central de continua propia del hardware Zero-IF del HackRF One.
- **Umbral Espectral Instantáneo:** Cálculo automático del piso de ruido en microsegundos utilizando la mediana de la FFT de cada bloque.
- **Filtro Anti-FHSS:** Sistema de puntuación por histéresis acumulativa y retención temporal (*Hold Time*) de 5 segundos para enganchar señales con saltos de frecuencia y transmisiones de video.

> El sistema opera de forma pasiva (no emite radiación), interceptando la firma electromagnética y el flujo de video del dispositivo antes de que inicie su fase de despegue.

---

##  Configuración y Puesta en Marcha

### 1. Requisitos previos de hardware
Conectar la antena omnidireccional compacta (monopolo SMA de 2.4 GHz) directamente a la entrada RF del **HackRF One** y este mediante cable USB al nodo de procesamiento (**Jetson Nano** o Laptop con Linux).

### 2. Dependencias del sistema operativo
Asegurarse de tener instaladas las herramientas de bajo nivel del SDR en la terminal:
```bash
sudo apt update && sudo apt install hackrf libhackrf-dev
```
##  Instrucciones de Operación y Despliegue

Siga estos pasos para validar el funcionamiento del sistema en vivo, garantizando que el hardware esté libre y el entorno virtual configurado correctamente.

### 1. Verificación Física y Conexión de Hardware
1. Enrosque la antena omnidireccional compacta de 2.4 GHz en el puerto **INPUT/OUTPUT (Entrada RF)** del HackRF One.
2. Conecte el HackRF One al puerto USB de la tarjeta **Jetson Nano** (o laptop de desarrollo) utilizando un cable blindado de alta velocidad.
3. Asegúrese de que el dron y su control remoto se encuentren inicialmente **APAGADOS** si desea verificar la transición de estados en la interfaz.

### 2. Liberación del Periférico SDR
En sistemas Linux, es común que otros procesos en segundo plano (como GQRX o instancias colgadas de capturas previas) bloqueen el acceso al puerto USB del HackRF. Antes de arrancar el script, ejecute una limpieza preventiva:

```bash
# Forzar el cierre de procesos que secuestran el SDR
sudo killall -9 gqrx 2>/dev/null
sudo killall -9 hackrf_transfer 2>/dev/null

# Validar que el hardware responde correctamente
hackrf_info
```
## 3. Lanzamiento del Servidor y Detección Asíncrona

Con el entorno virtual activo y las dependencias de Python instaladas, ejecute el script unificado que integra el procesamiento DSP y el servidor de red local:

```bash
python3 main_web.py
```

Al arrancar, el script imprimirá en la consola la confirmación del inicio del bucle de eventos asíncronos:

## 4. Acceso al Dashboard de Usuario (HMI)

1. Abra su navegador web preferido (Google Chrome o Mozilla Firefox).
2. Ingrese a la dirección de bucle local: `http://localhost:5000` (o reemplace `localhost` por la dirección IP de la Jetson Nano si está monitoreando desde otra computadora en la misma red local).
3. La interfaz web cargará de inmediato mostrando el estado inicial en color 🟢 **SEGURO** y la métrica de líneas activas fluctuando entre `0` y `1` debido al piso de ruido ambiental normal de la habitación.

## 5. Protocolo de Prueba en Vivo

- **Escenario de Encendido Posterior:** Con la página web abierta, encienda el control del dron. Observará cómo en menos de `500 milisegundos` el indicador central conmuta a color 🚨 **ALERTA**, la barra de confianza se eleva al `100%` (`10.00 / 10.0`) y el estado se mantiene fijo gracias al algoritmo de retención temporal mientras el dron transmita flujo de video.

- **Escenario de Encendido Previo:** Apague el script, deje el dron y el control encendidos en la mesa transmitiendo activamente. Vuelva a ejecutar `python3 main_web.py` y recargue la página; el sistema evadirá el sesgo de calibración gracias a la mediana instantánea, detectando la amenaza de forma inmediata desde el primer microsegundo de ejecución.
