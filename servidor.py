"""
servidor.py — Backend unificado: HTTP + WebSocket + Telegram

Puerto único 8080:
  GET  /          → sirve detector.html
  GET  /ws        → WebSocket de datos en tiempo real
  POST /alerta    → envía mensaje de ALERTA a Telegram
  POST /calma     → envía mensaje de CALMA a Telegram

Uso:
  1. Completa TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID abajo
  2. python servidor.py
  3. ngrok http 8080   →  copia la URL https://xxx.ngrok.io
  4. Abre esa URL en el celular
"""

import numpy as np
import subprocess
import time
import asyncio
import json
import os
from pathlib import Path
from aiohttp import web
import aiohttp

# ══════════════════════════════════════════════════════════════
#  ⚙️  CONFIGURACIÓN TELEGRAM  — rellena estos dos valores
# ══════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = "8989449997:AAENGhRQn-kKQGkXy4oR8KW_EiFVFbMmFIs"          # @BotFather te lo da
TELEGRAM_CHAT_ID   = "6740203125"            # @userinfobot te lo da

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# ══════════════════════════════════════════════════════════════
#  CONFIGURACIÓN RF  (sin cambios respecto al original)
# ══════════════════════════════════════════════════════════════
SAMPLE_RATE        = 10_000_000
FRECUENCIA_CENTRAL = 2_399_045_000
TAMANO_BLOQUE      = 131072
FFT_SIZE           = 1024

MULTIPLICADOR_UMBRAL    = 3.5
BINS_ACTIVOS_MIN        = 3
PUNTOS_POR_IMPACTO      = 2.0
DECAY_RATE              = 3.0
UMBRAL_SCORE_ACTIVACION = 3.0
UMBRAL_SCORE_LIBERACION = 0.8
SCORE_MAXIMO            = 10.0

# ══════════════════════════════════════════════════════════════
#  ESTADO GLOBAL
# ══════════════════════════════════════════════════════════════
estado_actual = {
    "score": 0.0, "score_max": SCORE_MAXIMO,
    "alerta_activa": False, "bins_activos": 0,
    "bins_min": BINS_ACTIVOS_MIN, "piso_ruido": 0.0,
    "umbral_local": 0.0, "fft_norm": [], "n_bloques": 0,
}
clientes_ws: set = set()


# ══════════════════════════════════════════════════════════════
#  LÓGICA DE DETECCIÓN  (idéntica al original)
# ══════════════════════════════════════════════════════════════
def procesar_espectro(raw: bytes):
    if not raw: return None
    datos = np.frombuffer(raw, dtype=np.int8)
    datos = datos[:len(datos) & ~1]
    if len(datos) < FFT_SIZE * 2: return None
    I = datos[0::2].astype(np.float32) / 128.0
    Q = datos[1::2].astype(np.float32) / 128.0
    iq = I + 1j * Q
    segmento  = iq[:4096]
    matrix_iq = segmento.reshape(-1, FFT_SIZE)
    fft_matrix = np.abs(np.fft.fftshift(np.fft.fft(matrix_iq, axis=1), axes=1))
    fft_mag    = np.mean(fft_matrix, axis=0)
    centro = len(fft_mag) // 2
    fft_mag[centro - 8: centro + 8] = 0.0
    return fft_mag


async def bucle_deteccion():
    global estado_actual
    loop   = asyncio.get_event_loop()
    proceso = await loop.run_in_executor(None, lambda: subprocess.Popen(
        ["hackrf_transfer", "-f", str(FRECUENCIA_CENTRAL),
         "-s", str(SAMPLE_RATE), "-r", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        bufsize=TAMANO_BLOQUE * 2
    ))

    score = 0.0; alerta_activa = False
    t_ultimo = time.time(); n_bloques = 0

    try:
        while True:
            raw = await loop.run_in_executor(None, proceso.stdout.read, TAMANO_BLOQUE * 2)
            fft_mag = procesar_espectro(raw)
            if fft_mag is None: break

            t_ahora = time.time(); dt = t_ahora - t_ultimo
            t_ultimo = t_ahora; n_bloques += 1

            piso   = float(np.median(fft_mag[fft_mag > 0]))
            umbral = piso * MULTIPLICADOR_UMBRAL
            bins   = int(np.sum(fft_mag > umbral))

            score = max(0.0, score - DECAY_RATE * dt)
            if bins >= BINS_ACTIVOS_MIN:
                score = min(SCORE_MAXIMO, score + PUNTOS_POR_IMPACTO)

            if not alerta_activa and score >= UMBRAL_SCORE_ACTIVACION:
                alerta_activa = True
            elif alerta_activa and score < UMBRAL_SCORE_LIBERACION:
                alerta_activa = False

            fft_r = fft_mag[::4].tolist()
            mx    = max(fft_r) if fft_r else 1.0
            fft_n = [round(v / mx, 4) for v in fft_r]

            estado_actual = {
                "score": round(score, 3), "score_max": SCORE_MAXIMO,
                "alerta_activa": alerta_activa, "bins_activos": bins,
                "bins_min": BINS_ACTIVOS_MIN, "piso_ruido": round(piso, 5),
                "umbral_local": round(umbral, 5), "fft_norm": fft_n,
                "n_bloques": n_bloques,
            }

            if clientes_ws:
                msg = json.dumps(estado_actual)
                await asyncio.gather(
                    *[ws.send_str(msg) for ws in clientes_ws],
                    return_exceptions=True
                )
            await asyncio.sleep(0)

    except asyncio.CancelledError:
        pass
    finally:
        proceso.terminate(); proceso.wait()


# ══════════════════════════════════════════════════════════════
#  RUTAS HTTP
# ══════════════════════════════════════════════════════════════
async def ruta_index(request):
    html_path = Path(__file__).parent / "detector.html"
    return web.FileResponse(html_path)


async def ruta_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    clientes_ws.add(ws)
    print(f"  ✅ WS conectado  — clientes: {len(clientes_ws)}")
    try:
        async for _ in ws:
            pass   # no esperamos mensajes del cliente
    finally:
        clientes_ws.discard(ws)
        print(f"  ❌ WS cerrado    — clientes: {len(clientes_ws)}")
    return ws


async def enviar_telegram(session: aiohttp.ClientSession, texto: str) -> bool:
    try:
        async with session.post(TELEGRAM_URL, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": texto,
            "parse_mode": "HTML"
        }, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"  ⚠️  Telegram error: {e}")
        return False


async def ruta_alerta(request):
    ts = time.strftime("%H:%M:%S")
    texto = (
        "🚨 <b>ALERTA COMUNITARIA — DRON DETECTADO</b>\n\n"
        f"⏰ Hora: {ts}\n"
        f"📡 Frecuencia: {FRECUENCIA_CENTRAL/1e6:.3f} MHz\n"
        f"📊 Score actual: {estado_actual['score']:.2f} / {SCORE_MAXIMO}\n"
        f"📶 Líneas activas: {estado_actual['bins_activos']}\n\n"
        "⚠️ Manténgase alerta. Mensaje enviado desde el sistema SDR."
    )
    async with aiohttp.ClientSession() as session:
        ok = await enviar_telegram(session, texto)
    status = "enviado" if ok else "error_telegram"
    return web.json_response({"status": status})


async def ruta_calma(request):
    ts = time.strftime("%H:%M:%S")
    texto = (
        "✅ <b>SITUACIÓN NORMALIZADA</b>\n\n"
        f"⏰ Hora: {ts}\n"
        f"📡 Espectro limpio en {FRECUENCIA_CENTRAL/1e6:.3f} MHz\n"
        f"📊 Score actual: {estado_actual['score']:.2f} / {SCORE_MAXIMO}\n\n"
        "🟢 El dron ya no está activo. Puede continuar con normalidad."
    )
    async with aiohttp.ClientSession() as session:
        ok = await enviar_telegram(session, texto)
    status = "enviado" if ok else "error_telegram"
    return web.json_response({"status": status})


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════
async def main():
    app = web.Application()
    app.router.add_get('/',        ruta_index)
    app.router.add_get('/ws',      ruta_ws)
    app.router.add_post('/alerta', ruta_alerta)
    app.router.add_post('/calma',  ruta_calma)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()

    print("\n╔══════════════════════════════════════════════════════╗")
    print("║   📡   Servidor SDR — HTTP + WebSocket + Telegram    ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"\n  Local  →  http://localhost:8080")
    print(f"  Ngrok  →  ngrok http 8080  (pega la URL https en el cel)")
    print(f"  WS     →  /ws")
    print(f"  Alerta →  POST /alerta")
    print(f"  Calma  →  POST /calma\n")

    await bucle_deteccion()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
