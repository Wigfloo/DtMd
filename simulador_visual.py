from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import math
import time
import random

app = FastAPI()

# --- CONFIGURACIÓN DEL ESCENARIO ---
LAT_CENTRO = 7.128606
LON_CENTRO = -73.181549

LAT_INICIO = 7.127000
LON_INICIO = -73.183000
LAT_FIN = 7.130000
LON_FIN = -73.180000

DURACION_VUELO = 30.0

# Memoria de estado del servidor
estado = {
    "tiempo_inicio": 0,
    "tipo_dron": "RF",
    "volando": False,
    "ultimo_check": 0,
    "radar_angulo": 0,
    "radar_memoria": {"detectado": False, "distancia": 0, "azimut": 0, "timestamp": 0}
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

@app.post("/iniciar/{tipo}")
def iniciar_vuelo(tipo: str):
    estado["tipo_dron"] = tipo
    estado["tiempo_inicio"] = time.time()
    estado["ultimo_check"] = time.time()
    estado["volando"] = True
    estado["radar_memoria"]["detectado"] = False
    return {"status": "ok"}

@app.get("/estado")
def obtener_estado():
    if not estado["volando"]:
        return {"volando": False}
    
    t_actual = time.time()
    progreso = (t_actual - estado["tiempo_inicio"]) / DURACION_VUELO
    
    if progreso >= 1.0:
        estado["volando"] = False
        return {"volando": False}
        
    # 1. Cinemática
    lat_actual = LAT_INICIO + (LAT_FIN - LAT_INICIO) * progreso
    lon_actual = LON_INICIO + (LON_FIN - LON_INICIO) * progreso
    dist_m = max(calcular_distancia(LAT_CENTRO, LON_CENTRO, lat_actual, lon_actual), 1.0)
    azimut_dron = calcular_azimut(LAT_CENTRO, LON_CENTRO, lat_actual, lon_actual)
    
    # 2. Modelo RF (FSPL 2.4GHz)
    emite_rf = estado["tipo_dron"] == "RF"
    fspl = 20 * math.log10(dist_m) + 40.05
    rssi = round(20 - fspl + random.uniform(-1.5, 1.5), 1) if emite_rf else -120.0
    rf_detectado = rssi >= -85.0

    # 3. Modelo Radar (Barrido Rotatorio)
    dt = t_actual - estado["ultimo_check"]
    vel_radar = 120.0 # grados por segundo
    angulo_previo = estado["radar_angulo"]
    angulo_actual = (angulo_previo + dt * vel_radar) % 360
    
    cruzó = False
    if angulo_actual >= angulo_previo:
        cruzó = angulo_previo <= azimut_dron <= angulo_actual
    else:
        cruzó = azimut_dron >= angulo_previo or azimut_dron <= angulo_actual
        
    if cruzó and dist_m <= 120.0:
        estado["radar_memoria"] = {
            "detectado": True,
            "distancia": round(dist_m + random.uniform(-2, 2), 1),
            "azimut": round(azimut_dron + random.uniform(-1, 1), 1),
            "timestamp": t_actual
        }
    
    # Expirar eco de radar si tiene más de 3 segundos (1 vuelta completa)
    if t_actual - estado["radar_memoria"]["timestamp"] > 3.0:
        estado["radar_memoria"]["detectado"] = False
        
    estado["ultimo_check"] = t_actual
    estado["radar_angulo"] = angulo_actual

    # 4. Modelo Acústico (SPL Atenuación)
    ruido_ambiente = round(45.0 + random.uniform(-0.5, 0.5), 1)
    spl_fuente = 90.0
    spl_recibido = round(spl_fuente - 20 * math.log10(dist_m) + random.uniform(-1, 1), 1)
    spl_total = round(max(ruido_ambiente, spl_recibido), 1)
    audio_detectado = spl_total >= 48.0

    # 5. Modelo Cámara
    camara_detectado = dist_m <= 70.0
    
    return {
        "volando": True,
        "lat": round(lat_actual, 6),
        "lon": round(lon_actual, 6),
        "sensores": {
            "RF": {"detectado": rf_detectado, "rssi": rssi},
            "Radar": estado["radar_memoria"],
            "Audio": {"detectado": audio_detectado, "spl": spl_total, "ambiente": ruido_ambiente},
            "Camara": {"detectado": camara_detectado}
        }
    }

# --- INTERFAZ WEB ---
@app.get("/")
def pagina_principal():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Simulador de Drones - Multisensor Avanzado</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <style>
            body { font-family: Arial, sans-serif; margin: 0; padding: 0; display: flex; }
            #map { height: 100vh; width: 70%; background: #1a1a1a; }
            #panel { width: 30%; padding: 20px; background: #2c3e50; color: white; overflow-y: auto; }
            button { padding: 10px; width: 100%; margin-top: 10px; background: #27ae60; color: white; border: none; font-size: 16px; cursor: pointer;}
            select { padding: 10px; width: 100%; font-size: 16px; margin-top: 10px; }
            .sensor { margin-top: 15px; padding: 15px; border-radius: 5px; background: #34495e; font-size: 14px; }
            .detectado { background: #c0392b; font-weight: bold; }
            .datos-sensor { color: #a2e9c1; font-family: monospace; margin-top: 5px; font-size: 13px; display: block; }
        </style>
    </head>
    <body>
        <div id="map"></div>
        <div id="panel">
            <h2>Panel de Sensores</h2>
            <select id="tipoDron">
                <option value="RF">Dron Comercial (Emisión RF 2.4GHz)</option>
                <option value="FIBRA">Dron Sigiloso (Fibra Óptica)</option>
            </select>
            <button onclick="iniciarVuelo()">Iniciar Incursión Simulada</button>
            
            <div id="s_rf" class="sensor">📡 Sensor RF 
                <span id="data_rf" class="datos-sensor">Buscando señales...</span>
            </div>
            <div id="s_radar" class="sensor">🛰️ Radar Rotatorio 
                <span id="data_radar" class="datos-sensor">Escaneando espacio aéreo...</span>
            </div>
            <div id="s_audio" class="sensor">🎙️ Arreglo Acústico 
                <span id="data_audio" class="datos-sensor">Monitoreando SPL...</span>
            </div>
            <div id="s_camara" class="sensor">📷 Cámara 
                <span id="data_camara" class="datos-sensor">FOV Despejado</span>
            </div>
        </div>

        <script>
            var latCentro = 7.128606, lonCentro = -73.181549;
            var map = L.map('map').setView([latCentro, lonCentro], 17);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
            L.marker([latCentro, lonCentro]).addTo(map).bindPopup("Estación Multisensor");

            var dronMarker = null;
            var radarLine = null;
            var radarVisualAngle = 0;

            // Motor visual del radar (corre a 60fps independientemente del polling)
            function getDestinoRadar(azimut_grados, distancia_m) {
                var R = 6378137;
                var brng = azimut_grados * Math.PI / 180;
                var lat1 = latCentro * Math.PI / 180, lon1 = lonCentro * Math.PI / 180;
                var lat2 = Math.asin(Math.sin(lat1) * Math.cos(distancia_m / R) + Math.cos(lat1) * Math.sin(distancia_m / R) * Math.cos(brng));
                var lon2 = lon1 + Math.atan2(Math.sin(brng) * Math.sin(distancia_m / R) * Math.cos(lat1), Math.cos(distancia_m / R) - Math.sin(lat1) * Math.sin(lat2));
                return [lat2 * 180 / Math.PI, lon2 * 180 / Math.PI];
            }

            setInterval(() => {
                radarVisualAngle = (radarVisualAngle + (120 * 0.05)) % 360; // 120 grados por segundo
                var borde = getDestinoRadar(radarVisualAngle, 120);
                if(!radarLine) {
                    radarLine = L.polyline([[latCentro, lonCentro], borde], {color: '#00ff00', weight: 3, opacity: 0.6}).addTo(map);
                } else {
                    radarLine.setLatLngs([[latCentro, lonCentro], borde]);
                }
            }, 50);

            function iniciarVuelo() {
                var tipo = document.getElementById("tipoDron").value;
                fetch("/iniciar/" + tipo, { method: "POST" });
            }

            // Polling de telemetría (Estado y detección real del backend)
            setInterval(() => {
                fetch("/estado").then(r => r.json()).then(data => {
                    if (data.volando) {
                        if (!dronMarker) { dronMarker = L.marker([data.lat, data.lon]).addTo(map); }
                        else { dronMarker.setLatLng([data.lat, data.lon]); }
                        
                        actualizarUI("s_rf", "data_rf", data.sensores.RF.detectado, 
                            data.sensores.RF.detectado ? `ALERTA RF DETECTADA<br>RSSI Estimado: ${data.sensores.RF.rssi} dBm` : `Buscando señales...<br>RSSI de Fondo: ${data.sensores.RF.rssi} dBm`);
                        
                        actualizarUI("s_radar", "data_radar", data.sensores.Radar.detectado,
                            data.sensores.Radar.detectado ? `ECO CONFIRMADO<br>Rango: ${data.sensores.Radar.distancia}m | Azimut: ${data.sensores.Radar.azimut}°` : `Escaneando espacio aéreo...<br>Sin contactos en el barrido.`);
                            
                        actualizarUI("s_audio", "data_audio", data.sensores.Audio.detectado,
                            data.sensores.Audio.detectado ? `RUIDO DE MOTORES<br>Nivel: ${data.sensores.Audio.spl} dB | Fondo: ${data.sensores.Audio.ambiente} dB` : `Monitoreando...<br>Ambiente: ${data.sensores.Audio.ambiente} dB`);
                            
                        actualizarUI("s_camara", "data_camara", data.sensores.Camara.detectado,
                            data.sensores.Camara.detectado ? `OBJETIVO EN CAMPO DE VISIÓN` : `FOV Despejado`);
                    } else {
                        if (dronMarker) { map.removeLayer(dronMarker); dronMarker = null; }
                        actualizarUI("s_rf", "data_rf", false, "Buscando señales...");
                        actualizarUI("s_radar", "data_radar", false, "Escaneando espacio aéreo...");
                        actualizarUI("s_audio", "data_audio", false, "Monitoreando SPL...");
                        actualizarUI("s_camara", "data_camara", false, "FOV Despejado");
                    }
                });
            }, 500);

            function actualizarUI(div_id, span_id, detectado, texto) {
                let div = document.getElementById(div_id);
                document.getElementById(span_id).innerHTML = texto;
                if (detectado) { div.classList.add("detectado"); } 
                else { div.classList.remove("detectado"); }
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)