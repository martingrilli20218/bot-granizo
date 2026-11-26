import os
import math
import requests
from io import BytesIO
from PIL import Image, ImageDraw
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL", "")
PORT = int(os.environ.get("PORT", 10000))

BBOX_MENDOZA = {
    "lat_max": -32.0,
    "lat_min": -36.6,
    "lon_min": -70.6,
    "lon_max": -66.4
}

RADARES_SMN = [
    {"nombre": "Radar SMN Córdoba", "codigo": "RMA1", "lat": -31.44, "lon": -64.19, "radio_km": 240},
    {"nombre": "Radar SMN Ezeiza (Bs. As.)", "codigo": "RMA2", "lat": -34.82, "lon": -58.53, "radio_km": 240},
    {"nombre": "Radar SMN Las Lajitas (Salta)", "codigo": "RMA3", "lat": -24.73, "lon": -64.20, "radio_km": 240},
    {"nombre": "Radar SMN Resistencia (Chaco)", "codigo": "RMA4", "lat": -27.45, "lon": -59.05, "radio_km": 240},
    {"nombre": "Radar SMN Mar del Plata", "codigo": "RMA6", "lat": -37.93, "lon": -57.58, "radio_km": 240},
    {"nombre": "Radar SMN Neuquén", "codigo": "RMA7", "lat": -38.95, "lon": -68.14, "radio_km": 240},
    {"nombre": "Radar SMN Paraná (Entre Ríos)", "codigo": "PAR", "lat": -31.85, "lon": -60.53, "radio_km": 240},
    {"nombre": "Radar SMN Pergamino (Bs. As.)", "codigo": "PER", "lat": -33.94, "lon": -60.56, "radio_km": 240},
    {"nombre": "Radar SMN Anguil (La Pampa)", "codigo": "ANG", "lat": -36.54, "lon": -63.99, "radio_km": 240},
]

def distancia_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def esta_en_mendoza(lat, lon):
    return (BBOX_MENDOZA["lat_min"] <= lat <= BBOX_MENDOZA["lat_max"] and
            BBOX_MENDOZA["lon_min"] <= lon <= BBOX_MENDOZA["lon_max"])

def gps_a_pixel(lat, lon, bbox, ancho, alto):
    x = int((lon - bbox["lon_min"]) / (bbox["lon_max"] - bbox["lon_min"]) * ancho)
    y = int((bbox["lat_max"] - lat) / (bbox["lat_max"] - bbox["lat_min"]) * alto)
    return max(0, min(ancho - 1, x)), max(0, min(alto - 1, y))

def analizar_pixeles(img_rgb, box):
    recorte = img_rgb.crop(box)
    pixeles = list(recorte.getdata())
    granizo = 0
    tormenta = 0
    lluvia = 0

    for r, g, b in pixeles:
        if r == g == b:
            continue
        if r > 200 and g < 85 and b < 85:
            granizo += 1
        elif r > 215 and 110 <= g <= 215 and b < 70:
            tormenta += 1
        elif g > 140 and r < 150 and b < 120:
            lluvia += 1

    if granizo >= 4:
        return "🔴 *ALTA* (Núcleos >55 dBZ — posible granizo)"
    elif tormenta >= 10:
        return "🟠 *MEDIA-ALTA* (Tormenta en formación / 45–55 dBZ)"
    elif lluvia >= 20 or tormenta >= 2:
        return "🟡 *BAJA* (Lluvia moderada / celdas aisladas)"
    return "🟢 *NULA* (Sin ecos significativos en radar)"

def obtener_imagen_radar(lat, lon):
    headers = {"User-Agent": "Mozilla/5.0"}

    if esta_en_mendoza(lat, lon):
        urls_mendoza = [
            "https://www2.contingencias.mendoza.gov.ar/radar/web/img/mosaico.png",
            "https://www2.contingencias.mendoza.gov.ar/radar/web/img/mosaico_g.png"
        ]
        for url in urls_mendoza:
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 200 and len(r.content) > 2000:
                    return Image.open(BytesIO(r.content)).convert("RGB"), "Red MRL5 Contingencias Mendoza", BBOX_MENDOZA
            except Exception:
                pass

    radar_cercano = None
    menor_dist = 9999
    for rad in RADARES_SMN:
        d = distancia_km(lat, lon, rad["lat"], rad["lon"])
        if d < rad["radio_km"] and d < menor_dist:
            menor_dist = d
            radar_cercano = rad

    if radar_cercano:
        url_smn = f"https://www.smn.gob.ar/sites/default/files/radares/{radar_cercano['codigo']}_CMAX.png"
        try:
            r = requests.get(url_smn, headers=headers, timeout=10)
            if r.status_code == 200 and len(r.content) > 2000:
                bbox_rad = {
                    "lat_max": radar_cercano["lat"] + 2.2,
                    "lat_min": radar_cercano["lat"] - 2.2,
                    "lon_min": radar_cercano["lon"] - 2.6,
                    "lon_max": radar_cercano["lon"] + 2.6,
                }
                return Image.open(BytesIO(r.content)).convert("RGB"), f"{radar_cercano['nombre']} (a {int(menor_dist)} km)", bbox_rad
        except Exception:
            pass

    url_goes = "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/SECTOR/ssa/13/1200x1200.jpg"
    r = requests.get(url_goes, headers=headers, timeout=15)
    bbox_ssa = {"lat_max": -18.0, "lat_min": -56.0, "lon_min": -78.0, "lon_max": -48.0}
    return Image.open(BytesIO(r.content)).convert("RGB"), "Satélite GOES-19 (Respaldo Infrarrojo)", bbox_ssa

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    boton = KeyboardButton(text="📍 Consultar granizo en mi ubicación", request_location=True)
    teclado = ReplyKeyboardMarkup([[boton]], resize_keyboard=True)
    await update.message.reply_text(
        "👋 *Radar de Tormentas y Granizo listo.*\n\n"
        "Tocá el botón de abajo para enviarme tu ubicación GPS y analizar los radares disponibles en tu zona.",
        parse_mode="Markdown",
        reply_markup=teclado
    )

async def recibir_ubicacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lat = update.message.location.latitude
    lon = update.message.location.longitude
    msg_espera = await update.message.reply_text("🛰️ Escaneando radares y satélite en tu zona...")

    try:
        img, fuente, bbox = obtener_imagen_radar(lat, lon)
        ancho, alto = img.size
        px, py = gps_a_pixel(lat, lon, bbox, ancho, alto)

        radio_px = max(25, int(ancho * 0.045))
        box_entorno = (
            max(0, px - radio_px),
            max(0, py - radio_px),
            min(ancho, px + radio_px),
            min(alto, py + radio_px)
        )

        estado_entorno = analizar_pixeles(img, box_entorno)

        detalle_zonas = ""
        if esta_en_mendoza(lat, lon) and fuente.startswith("Red MRL5"):
            zonas = {
                "Oasis Norte y Este": (int(ancho*0.50), int(alto*0.15), int(ancho*0.80), int(alto*0.35)),
                "Valle de Uco": (int(ancho*0.38), int(alto*0.35), int(ancho*0.60), int(alto*0.52)),
                "Oasis Sur": (int(ancho*0.45), int(alto*0.55), int(ancho*0.82), int(alto*0.85)),
            }
            lineas = [f"• *{nombre}:* {analizar_pixeles(img, z_box)}" for nombre, z_box in zonas.items()]
            detalle_zonas = "\n\n🗺️ *Actividad por zonas en Mendoza:*\n" + "\n".join(lineas)

        draw = ImageDraw.Draw(img)
        draw.ellipse((px - radio_px, py - radio_px, px + radio_px, py + radio_px), outline="#00FFFF", width=3)
        draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill="#00FFFF")

        if "GOES-19" in fuente:
            zoom = 180
            img = img.crop((max(0, px-zoom), max(0, py-zoom), min(ancho, px+zoom), min(alto, py+zoom)))

        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        caption = (
            f"📡 *Fuente:* {fuente}\n"
            f"📍 *Tu entorno (~25 km):*\n{estado_entorno}"
            f"{detalle_zonas}"
        )
        await msg_espera.delete()
        await update.message.reply_photo(photo=buf, caption=caption, parse_mode="Markdown")

    except Exception as e:
        await msg_espera.edit_text(f"⚠️ Error al consultar radar:\n`{e}`", parse_mode="Markdown")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.LOCATION, recibir_ubicacion))

    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
        )
    else:
        app.run_polling()
