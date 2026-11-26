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

def esta_en_mendoza(lat, lon):
    return (BBOX_MENDOZA["lat_min"] <= lat <= BBOX_MENDOZA["lat_max"] and
            BBOX_MENDOZA["lon_min"] <= lon <= BBOX_MENDOZA["lon_max"])

def gps_a_pixel(lat, lon, bbox, ancho, alto):
    x = int((lon - bbox["lon_min"]) / (bbox["lon_max"] - bbox["lon_min"]) * ancho)
    y = int((bbox["lat_max"] - lat) / (bbox["lat_max"] - bbox["lat_min"]) * alto)
    return max(0, min(ancho - 1, x)), max(0, min(alto - 1, y))

def descargar_imagen_valida(url, timeout=12):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "image/png,image/jpeg,image/*,*/*;q=0.8"
    }
    r = requests.get(url, headers=headers, timeout=timeout, verify=False)
    if r.status_code == 200 and len(r.content) > 1500:
        img = Image.open(BytesIO(r.content))
        img.load()
        return img.convert("RGB")
    return None

def analizar_pixeles(img_rgb, box):
    recorte = img_rgb.crop(box)
    pixeles = list(recorte.getdata())
    granizo = 0
    tormenta = 0
    lluvia = 0

    for r, g, b in pixeles:
        if r == g == b:
            continue
        if r > 190 and g < 85 and b < 85:
            granizo += 1
        elif r > 210 and 100 <= g <= 215 and b < 70:
            tormenta += 1
        elif (g > 135 and r < 150 and b < 130) or (b > 160 and r < 100):
            lluvia += 1

    if granizo >= 4:
        return "🔴 *ALTA* (Núcleos >55 dBZ — posible granizo)"
    elif tormenta >= 8:
        return "🟠 *MEDIA-ALTA* (Tormenta en formación / 45–55 dBZ)"
    elif lluvia >= 15 or tormenta >= 2:
        return "🟡 *BAJA* (Lluvia moderada / celdas aisladas)"
    return "🟢 *NULA* (Sin ecos significativos en radar)"

def obtener_tile_rainviewer(lat, lon):
    api = requests.get("https://api.rainviewer.com/public/weather-maps.json", timeout=10).json()
    host = api["host"]
    ultimo_radar = api["radar"]["past"][-1]["path"]
    url_tile = f"{host}{ultimo_radar}/512/6/{lat}/{lon}/2/1_1.png"
    img = descargar_imagen_valida(url_tile, timeout=12)
    return img

def obtener_imagen_radar(lat, lon):
    if esta_en_mendoza(lat, lon):
        urls_mendoza = [
            "https://www2.contingencias.mendoza.gov.ar/radar/web/img/mosaico.png",
            "https://www2.contingencias.mendoza.gov.ar/radar/web/img/mosaico_g.png"
        ]
        for url in urls_mendoza:
            try:
                img = descargar_imagen_valida(url, timeout=8)
                if img:
                    return img, "Red MRL5 Contingencias Mendoza", BBOX_MENDOZA, True
            except Exception:
                pass

    try:
        img = obtener_tile_rainviewer(lat, lon)
        if img:
            return img, "Radar Meteorológico Global (RainViewer)", None, False
    except Exception:
        pass

    urls_goes = [
        "https://cdn.star.nesdis.noaa.gov/GOES16/ABI/SECTOR/ssa/13/1200x1200.jpg",
        "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/SECTOR/ssa/13/1200x1200.jpg",
        "https://cdn.star.nesdis.noaa.gov/GOES16/ABI/SECTOR/ssa/GEOCOLOR/1200x1200.jpg"
    ]
    bbox_ssa = {"lat_max": -18.0, "lat_min": -56.0, "lon_min": -78.0, "lon_max": -48.0}
    for url_goes in urls_goes:
        try:
            img = descargar_imagen_valida(url_goes, timeout=12)
            if img:
                return img, "Satélite GOES (Infrarrojo / GeoColor)", bbox_ssa, True
        except Exception:
            pass

    raise RuntimeError("No se pudo conectar con ninguno de los servidores meteorológicos en este momento.")

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
        img, fuente, bbox, usar_bbox = obtener_imagen_radar(lat, lon)
        ancho, alto = img.size

        if usar_bbox and bbox:
            px, py = gps_a_pixel(lat, lon, bbox, ancho, alto)
        else:
            px, py = ancho // 2, alto // 2

        radio_px = max(35, int(ancho * 0.08))
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

        if "GOES" in fuente:
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
