from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import math
import time
import random
import json
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

app = FastAPI()

# --- CONFIGURACIÓN MQTT ROBUSTA (CON RECONEXIÓN DINÁMICA) ---
MQTT_BROKER_IP = "jetson.local"
MQTT_PORT = 1883
MQTT_TOPIC_DETECTIONS = "surveillance/detections"
MQTT_TOPIC_TELEMETRY = "surveillance/telemetry"

mqtt_conectado = False

def on_connect(client, userdata, flags, rc, properties=None):
    global mqtt_conectado
    if rc == 0:
        mqtt_conectado = True
        print(f"✅ MQTT: Conectado exitosamente al broker en {MQTT_BROKER_IP}:{MQTT_PORT}")
    else:
        mqtt_conectado = False
        print(f"❌ MQTT: Fallo de conexión con código {rc}")

def on_disconnect(client, userdata, rc, properties=None):
    global mqtt_conectado
    mqtt_conectado = False
    print("⚠️ MQTT: Desconectado del broker. Intentando reconectar automáticamente...")

# Compatibilidad para paho-mqtt v1 y v2
try:
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
except AttributeError:
    mqtt_client = mqtt.Client()

mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect

try:
    mqtt_client.connect_async(MQTT_BROKER_IP, MQTT_PORT, 60)
    mqtt_client.loop_start()
except Exception as e:
    print(f"⚠️ MQTT Error inicial con {MQTT_BROKER_IP}: {e}")

def publicar_mqtt(topic, payload):
    if mqtt_conectado:
        res = mqtt_client.publish(topic, json.dumps(payload))
        if res.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"⚠️ Error al publicar evento en {topic}: {res.rc}")

# --- CONFIGURACIÓN DEL ESCENARIO ---
LAT_CENTRO = 7.128606
LON_CENTRO = -73.181549

LAT_INICIO = 7.127000
LON_INICIO = -73.183000
LAT_FIN = 7.130000
LON_FIN = -73.180000

DURACION_VUELO = 30.0

PISO_RUIDO_RF_BASE = -95.0     # dBm
PISO_RUIDO_AUDIO_BASE = 45.0  # dB SPL

estado = {
    "tiempo_inicio": 0,
    "tipo_dron": "RF",
    "volando": False,
    "ultimo_check": 0,
    "radar_angulo": 0,
    "radar_memoria": [],
    "ave": {"activa": False, "lat": 0.0, "lon": 0.0, "siguiente_aparicion": 0}
}

def calcular_distancia(lat1, lon1, lat2, lon2):
    x = (lon2 - lon1) * 111320 * math.cos(math.radians(lat1))
    y = (lat2 - lat1) * 111320
    return math.sqrt(x**2 + y**2)

def calcular_azimut(lat1, lon1, lat2, lon2):
    dLon = math.radians(lon2 - lon1)
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    y = math.sin(dLon) * math.cos(lat2_rad)
    x = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dLon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def actualizar_posicion_ave(t_actual):
    if not estado["ave"]["activa"] and t_actual > estado["ave"]["siguiente_aparicion"]:
        angulo = random.uniform(0, 2 * math.pi)
        dist = random.uniform(20, 90)
        dx = dist * math.cos(angulo)
        dy = dist * math.sin(angulo)
        estado["ave"]["lat"] = LAT_CENTRO + (dy / 111320)
        estado["ave"]["lon"] = LON_CENTRO + (dx / (111320 * math.cos(math.radians(LAT_CENTRO))))
        estado["ave"]["activa"] = True
        estado["ave"]["siguiente_aparicion"] = t_actual + random.uniform(8, 15)
        
    elif estado["ave"]["activa"]:
        estado["ave"]["lat"] += random.uniform(-0.00005, 0.00005)
        estado["ave"]["lon"] += random.uniform(-0.00005, 0.00005)
        if random.random() < 0.1:
            estado["ave"]["activa"] = False

@app.post("/iniciar/{tipo}")
def iniciar_vuelo(tipo: str):
    t_ahora = time.time()
    estado["tipo_dron"] = tipo
    estado["tiempo_inicio"] = t_ahora
    estado["ultimo_check"] = t_ahora
    estado["volando"] = True
    estado["radar_memoria"] = []
    estado["ave"]["siguiente_aparicion"] = t_ahora + random.uniform(2, 5)
    return {"status": "ok"}

@app.get("/estado")
def obtener_estado():
    t_actual = time.time()
    actualizar_posicion_ave(t_actual)
    timestamp_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    rssi_ruido_fondo = round(PISO_RUIDO_RF_BASE + random.uniform(-2.5, 2.5), 1)
    ruido_ambiente = round(PISO_RUIDO_AUDIO_BASE + random.uniform(-0.5, 0.5), 1)
    eventos_json = []

    if not estado["volando"]:
        return {
            "volando": False,
            "ave": estado["ave"],
            "sensores": {
                "RF": {"rssi": rssi_ruido_fondo, "piso_ruido": PISO_RUIDO_RF_BASE},
                "Radar": [],
                "Audio": {"spl": ruido_ambiente, "ambiente": PISO_RUIDO_AUDIO_BASE, "detectado": False},
                "Camara": []
            },
            "eventos": []
        }
    
    progreso = (t_actual - estado["tiempo_inicio"]) / DURACION_VUELO
    if progreso >= 1.0:
        estado["volando"] = False
        return {"volando": False, "ave": estado["ave"]}
        
    # --- Cinemática ---
    lat_dron = LAT_INICIO + (LAT_FIN - LAT_INICIO) * progreso
    lon_dron = LON_INICIO + (LON_FIN - LON_INICIO) * progreso
    dist_dron = max(calcular_distancia(LAT_CENTRO, LON_CENTRO, lat_dron, lon_dron), 1.0)
    azimut_dron = calcular_azimut(LAT_CENTRO, LON_CENTRO, lat_dron, lon_dron)
    
    dist_ave = 9999.0
    azimut_ave = 0.0
    if estado["ave"]["activa"]:
        dist_ave = calcular_distancia(LAT_CENTRO, LON_CENTRO, estado["ave"]["lat"], estado["ave"]["lon"])
        azimut_ave = calcular_azimut(LAT_CENTRO, LON_CENTRO, estado["ave"]["lat"], estado["ave"]["lon"])

    # --- Sensor RF ---
    emite_rf = estado["tipo_dron"] == "RF"
    rf_data_payload = None
    if emite_rf:
        fspl = 20 * math.log10(dist_dron) + 40.05
        rssi_senal = 20 - fspl + random.uniform(-1.5, 1.5)
        rssi_total = round(10 * math.log10(10**(rssi_senal/10) + 10**(rssi_ruido_fondo/10)), 1)
    else:
        rssi_total = rssi_ruido_fondo
        
    rf_detectado = rssi_total >= -85.0
    if rf_detectado:
        rf_data_payload = {"frequency_mhz": 2437.5, "rssi_dbm": rssi_total, "detected": True}
        evento_rf = {
            "object_id": f"UAV-{estado['tipo_dron']}-01",
            "classification": "drone",
            "timestamp": timestamp_iso,
            "position": {"x": round(lon_dron, 6), "y": round(lat_dron, 6), "z": 30.0},
            "source": {"sensor_id": "RF-01", "type": "rf_receiver"},
            "rf_data": rf_data_payload
        }
        eventos_json.append(evento_rf)
        publicar_mqtt(MQTT_TOPIC_DETECTIONS, evento_rf)

    # --- Sensor Radar ---
    dt = t_actual - estado["ultimo_check"]
    vel_radar = 120.0 
    angulo_previo = estado["radar_angulo"]
    angulo_actual = (angulo_previo + dt * vel_radar) % 360
    
    def haz_cruzo_objetivo(azimut_obj):
        if angulo_actual >= angulo_previo:
            return angulo_previo <= azimut_obj <= angulo_actual
        return azimut_obj >= angulo_previo or azimut_obj <= angulo_actual

    nuevos_ecos_radar = []
    if haz_cruzo_objetivo(azimut_dron) and dist_dron <= 120.0:
        eco = {"id": f"UAV-{estado['tipo_dron']}-01", "distancia": round(dist_dron, 1), "azimut": round(azimut_dron, 1), "timestamp": t_actual}
        nuevos_ecos_radar.append(eco)
        evento_radar = {
            "object_id": eco["id"],
            "classification": "unknown",
            "timestamp": timestamp_iso,
            "position": {"x": round(lon_dron, 6), "y": round(lat_dron, 6), "z": 30.0},
            "source": {"sensor_id": "RDR-01", "type": "radar"}
        }
        eventos_json.append(evento_radar)
        publicar_mqtt(MQTT_TOPIC_DETECTIONS, evento_radar)

    if estado["ave"]["activa"] and haz_cruzo_objetivo(azimut_ave) and dist_ave <= 120.0:
        eco = {"id": "BIRD-UNKNOWN", "distancia": round(dist_ave, 1), "azimut": round(azimut_ave, 1), "timestamp": t_actual}
        nuevos_ecos_radar.append(eco)
        evento_radar = {
            "object_id": eco["id"],
            "classification": "unknown",
            "timestamp": timestamp_iso,
            "position": {"x": round(estado["ave"]["lon"], 6), "y": round(estado["ave"]["lat"], 6), "z": 15.0},
            "source": {"sensor_id": "RDR-01", "type": "radar"}
        }
        eventos_json.append(evento_radar)
        publicar_mqtt(MQTT_TOPIC_DETECTIONS, evento_radar)

    estado["radar_memoria"].extend(nuevos_ecos_radar)
    estado["radar_memoria"] = [eco for eco in estado["radar_memoria"] if t_actual - eco["timestamp"] <= 3.0]
    
    estado["ultimo_check"] = t_actual
    estado["radar_angulo"] = angulo_actual

    # --- Sensor Acústico ---
    spl_recibido = 90.0 - 20 * math.log10(dist_dron) + random.uniform(-1, 1)
    spl_total = round(max(ruido_ambiente, spl_recibido), 1)
    audio_detectado = spl_total >= 48.0
    
    if audio_detectado:
        evento_audio = {
            "object_id": f"UAV-{estado['tipo_dron']}-01",
            "classification": "drone",
            "timestamp": timestamp_iso,
            "position": {"x": round(lon_dron, 6), "y": round(lat_dron, 6), "z": 30.0},
            "source": {"sensor_id": "MIC-01", "type": "acoustic"},
            "acoustic_data": {
                "spl_db": spl_total,
                "ambient_noise_db": ruido_ambiente,
                "detected": True
            }
        }
        eventos_json.append(evento_audio)
        publicar_mqtt(MQTT_TOPIC_DETECTIONS, evento_audio)

    # --- Sensor Cámara ---
    camara_detecciones = []
    if dist_dron <= 70.0:
        det_dron = {
            "object_id": f"UAV-{estado['tipo_dron']}-01",
            "classification": "drone",
            "timestamp": timestamp_iso,
            "position": {"x": round(lon_dron, 6), "y": round(lat_dron, 6), "z": 30.0},
            "source": {"sensor_id": "CAM-01", "type": "camera"}
        }
        if rf_data_payload: det_dron["rf_data"] = rf_data_payload
        camara_detecciones.append(det_dron)
        eventos_json.append(det_dron)
        publicar_mqtt(MQTT_TOPIC_DETECTIONS, det_dron)

    if estado["ave"]["activa"] and dist_ave <= 70.0:
        det_ave = {
            "object_id": "BIRD-001",
            "classification": "bird",
            "timestamp": timestamp_iso,
            "position": {"x": round(estado["ave"]["lon"], 6), "y": round(estado["ave"]["lat"], 6), "z": 15.0},
            "source": {"sensor_id": "CAM-01", "type": "camera"}
        }
        camara_detecciones.append(det_ave)
        eventos_json.append(det_ave)
        publicar_mqtt(MQTT_TOPIC_DETECTIONS, det_ave)

    return {
        "volando": True,
        "lat": round(lat_dron, 6),
        "lon": round(lon_dron, 6),
        "ave": estado["ave"],
        "sensores": {
            "RF": {"rssi": rssi_total, "piso_ruido": PISO_RUIDO_RF_BASE},
            "Radar": estado["radar_memoria"],
            "Audio": {"spl": spl_total, "ambiente": ruido_ambiente, "detectado": audio_detectado},
            "Camara": camara_detecciones
        },
        "eventos": eventos_json
    }

# --- INTERFAZ WEB ---
@app.get("/")
def pagina_principal():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Simulador - Detección Multisensorial Restaurada</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <style>
            body { font-family: Arial, sans-serif; margin: 0; padding: 0; display: flex; }
            #map { height: 100vh; width: 70%; background: #1a1a1a; }
            #panel { width: 30%; padding: 20px; background: #2c3e50; color: white; overflow-y: auto; }
            button { padding: 10px; width: 100%; margin-top: 10px; background: #27ae60; color: white; border: none; font-size: 16px; cursor: pointer;}
            select { padding: 10px; width: 100%; font-size: 16px; margin-top: 10px; }
            .sensor { margin-top: 15px; padding: 12px; border-radius: 5px; background: #34495e; font-size: 14px; }
            .datos-estructurados { font-family: monospace; font-size: 12px; color: #a2e9c1; white-space: pre-wrap; margin-top: 5px; display: block;}
            .highlight { color: #f1c40f; }
        </style>
    </head>
    <body>
        <div id="map"></div>
        <div id="panel">
            <h2>Datos de Sensores Estructurados</h2>
            <select id="tipoDron">
                <option value="RF">Dron Comercial (RF 2.4GHz)</option>
                <option value="FIBRA">Dron Sigiloso (Fibra Óptica)</option>
            </select>
            <button onclick="iniciarVuelo()">Lanzar Dron</button>
            
            <div id="s_camara" class="sensor">📷 Clasificación de Cámara
                <span id="data_camara" class="datos-estructurados">Esperando objetos...</span>
            </div>
            <div id="s_audio" class="sensor">🎙️ Arreglo Acústico (Micrófonos)
                <span id="data_audio" class="datos-estructurados">Monitoreando...</span>
            </div>
            <div id="s_rf" class="sensor">📡 Telemetría RF 
                <span id="data_rf" class="datos-estructurados">Buscando...</span>
            </div>
            <div id="s_radar" class="sensor">🛰️ Contactos de Radar
                <span id="data_radar" class="datos-estructurados">Escaneando...</span>
            </div>
            <div id="s_eventos" class="sensor">📦 Evento JSON Transmitido (MQTT)
                <span id="data_eventos" class="datos-estructurados">Sin eventos recientes</span>
            </div>
        </div>

        <script>
            var latCentro = 7.128606, lonCentro = -73.181549;
            var map = L.map('map').setView([latCentro, lonCentro], 17);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
            L.circle([latCentro, lonCentro], {color: 'red', fillColor: '#f03', fillOpacity: 0.1, radius: 125}).addTo(map);

            var dronMarker = null;
            var aveMarker = null;
            var radarLine = null;
            var radarVisualAngle = 0;

            function getDestinoRadar(azimut_grados, distancia_m) {
                var R = 6378137;
                var brng = azimut_grados * Math.PI / 180;
                var lat1 = latCentro * Math.PI / 180, lon1 = lonCentro * Math.PI / 180;
                var lat2 = Math.asin(Math.sin(lat1) * Math.cos(distancia_m / R) + Math.cos(lat1) * Math.sin(distancia_m / R) * Math.cos(brng));
                var lon2 = lon1 + Math.atan2(Math.sin(brng) * Math.sin(distancia_m / R) * Math.cos(lat1), Math.cos(distancia_m / R) - Math.sin(lat1) * Math.sin(lat2));
                return [lat2 * 180 / Math.PI, lon2 * 180 / Math.PI];
            }

            setInterval(() => {
                radarVisualAngle = (radarVisualAngle + (120 * 0.05)) % 360;
                var borde = getDestinoRadar(radarVisualAngle, 120);
                if(!radarLine) {
                    radarLine = L.polyline([[latCentro, lonCentro], borde], {color: '#00ff00', weight: 2, opacity: 0.5}).addTo(map);
                } else {
                    radarLine.setLatLngs([[latCentro, lonCentro], borde]);
                }
            }, 50);

            function iniciarVuelo() {
                var tipo = document.getElementById("tipoDron").value;
                fetch("/iniciar/" + tipo, { method: "POST" });
            }

            setInterval(() => {
                fetch("/estado").then(r => r.json()).then(data => {
                    let s = data.sensores;

                    if (data.ave && data.ave.activa) {
                        if (!aveMarker) {
                            let birdIcon = L.divIcon({className: 'custom-div-icon', html: "<div style='background-color:#f39c12; width:10px; height:10px; border-radius:50%;'></div>", iconSize: [10, 10]});
                            aveMarker = L.marker([data.ave.lat, data.ave.lon], {icon: birdIcon}).addTo(map).bindPopup("Ave");
                        } else {
                            aveMarker.setLatLng([data.ave.lat, data.ave.lon]);
                        }
                    } else if (aveMarker) {
                        map.removeLayer(aveMarker);
                        aveMarker = null;
                    }

                    if (data.volando) {
                        if (!dronMarker) { dronMarker = L.marker([data.lat, data.lon]).addTo(map).bindPopup("Dron"); }
                        else { dronMarker.setLatLng([data.lat, data.lon]); }
                    } else if (dronMarker) {
                        map.removeLayer(dronMarker);
                        dronMarker = null;
                    }

                    let camText = s.Camara.length > 0 ? s.Camara.map(c => `Clasificación: <span class="highlight">${c.classification.toUpperCase()}</span> (ID: ${c.object_id})`).join("<br>") : "Sin objetivos en rango de visión (70m)";
                    document.getElementById("data_camara").innerHTML = camText;

                    document.getElementById("data_audio").innerText = `Nivel SPL: ${s.Audio.spl} dB SPL\\nPiso Ambiente: ${s.Audio.ambiente} dB SPL\\nEstado: ${s.Audio.detectado ? '¡Firma acústica de motor detectada!' : 'Sin firma de motor'}`;

                    document.getElementById("data_rf").innerText = `Frecuencia: 2437.5 MHz\\nRSSI Actual: ${s.RF.rssi} dBm\\nPiso Ruido : ${s.RF.piso_ruido} dBm`;
                    
                    let radarText = s.Radar.length > 0 ? s.Radar.map(e => `[ID: ${e.id}] Rango: ${e.distancia}m, Azimut: ${e.azimut}°`).join("\\n") : "Sin contactos en barrido actual.";
                    document.getElementById("data_radar").innerText = radarText;

                    let eventText = data.eventos.length > 0 ? JSON.stringify(data.eventos[0], null, 2) : "Sin eventos recientes...";
                    document.getElementById("data_eventos").innerText = eventText;

                });
            }, 500);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)