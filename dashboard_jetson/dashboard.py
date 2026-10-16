import asyncio
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
import paho.mqtt.client as mqtt

# Configuración
MQTT_BROKER = "127.0.0.1"  # Corre localmente en la Jetson
MQTT_PORT = 1883
TOPICS = [("surveillance/detections", 0), ("surveillance/telemetry", 0)]

app = FastAPI(title="Multisensor Surveillance Dashboard")
templates = Jinja2Templates(directory="templates")

# Estado Global en Memoria
tracked_objects = {}
recent_events = []
active_websockets = []
mqtt_status = "Desconectado"

# --- MOTOR DE TRACKING Y FUSIÓN ---
def process_message(payload):
    global tracked_objects, recent_events
    
    obj_id = payload.get("object_id")
    if not obj_id: return

    # Inicializar nuevo objeto si no existe
    if obj_id not in tracked_objects:
        tracked_objects[obj_id] = {
            "object_id": obj_id,
            "classification": "unknown",
            "position": None,
            "trajectory": [],
            "last_seen": None,
            "sensors": {},
            "measurements": {}
        }

    obj = tracked_objects[obj_id]
    obj["last_seen"] = payload.get("timestamp", obj["last_seen"])

    # Actualizar Clasificación (No degradar a "unknown" si ya sabemos qué es)
    new_class = payload.get("classification")
    if new_class and new_class != "unknown":
        obj["classification"] = new_class

    # Actualizar Posición (x = lon, y = lat)
    pos = payload.get("position")
    if pos and "x" in pos and "y" in pos:
        lat, lon = pos["y"], pos["x"]
        obj["position"] = {"lat": lat, "lon": lon, "alt": pos.get("z", 0)}
        
        # Guardar trayectoria (límite de 40 puntos para no saturar memoria)
        if not obj["trajectory"] or obj["trajectory"][-1] != [lat, lon]:
            obj["trajectory"].append([lat, lon])
            if len(obj["trajectory"]) > 40:
                obj["trajectory"].pop(0)

    # Extraer fuente y mediciones
    source = payload.get("source", {})
    sensor_type = source.get("type", "unknown")
    obj["sensors"][sensor_type] = source.get("sensor_id")

    if "rf_data" in payload: obj["measurements"]["rf"] = payload["rf_data"]
    if "acoustic_data" in payload: obj["measurements"]["acoustic"] = payload["acoustic_data"]

    # Almacenar evento en historial (límite 30)
    event_record = {
        "timestamp": obj["last_seen"],
        "object_id": obj_id,
        "classification": payload.get("classification", "unknown"),
        "sensor": sensor_type
    }
    recent_events.insert(0, event_record)
    if len(recent_events) > 30:
        recent_events.pop()

# --- CLIENTE MQTT ---
def on_connect(client, userdata, flags, rc, properties=None):
    global mqtt_status
    if rc == 0:
        mqtt_status = "Conectado"
        client.subscribe(TOPICS)
        print(f"MQTT Conectado. Escuchando en {TOPICS}")
    else:
        mqtt_status = f"Error {rc}"

def on_disconnect(client, userdata, rc, properties=None):
    global mqtt_status
    mqtt_status = "Desconectado"
    print("MQTT Desconectado. Reintentando...")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        process_message(payload)
    except Exception as e:
        print(f"Error parseando MQTT: {e}")

try:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
except AttributeError:
    client = mqtt.Client()

client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message

# Conectar e iniciar loop en un hilo de fondo (sin bloquear FastAPI)
try:
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
except Exception as e:
    print(f"No se pudo iniciar MQTT: {e}")

# --- API Y WEBSOCKETS ---
@app.get("/")
def get_dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

async def broadcast_state():
    while True:
        if active_websockets:
            state = {
                "mqtt_status": mqtt_status,
                "objects": tracked_objects,
                "events": recent_events
            }
            message = json.dumps(state)
            disconnected = []
            for ws in active_websockets:
                try:
                    await ws.send_text(message)
                except Exception:
                    disconnected.append(ws)
            for ws in disconnected:
                active_websockets.remove(ws)
        # Tasa de refresco de 2 Hz (0.5s)
        await asyncio.sleep(0.5)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.append(websocket)
    try:
        while True:
            await websocket.receive_text() # Mantiene la conexión abierta
    except WebSocketDisconnect:
        active_websockets.remove(websocket)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(broadcast_state())
