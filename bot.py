import os
import math
import urllib3
import requests
from io import BytesIO
from PIL import Image, ImageDraw
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL", "")
PORT = int(os.environ.get("PORT", 10000))
RADIO_KM = 50.0

def gps_a_pixel(lat, lon, bbox, ancho, alto):
    x = int((lon - bbox["lon_min"]) / (bbox["lon_max"] - bbox["lon_min"]) * ancho)
    y = int((bbox["lat_max"] - lat) / (bbox["lat_max"] - bbox["lat_min"]) * alto)
    return max(0, min(ancho - 1, x)), max(0, min(alto - 1, y))

def gps_a_tile_xy_y_offset(lat, lon, zoom):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile_float = (lon + 180.0) / 360.0 * n
    ytile_float = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    xtile = int(xtile_float)
    ytile = int(ytile_float)
    px = int((xtile_float - xtile) * 256)
    py = int((ytile_float - ytile) * 256)
    return xtile, ytile, px, py

def descargar_imagen_valida(url, timeout=12, min_bytes=50):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
    }
    r = requests.get(url, headers=headers, timeout=timeout, verify=False)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    if len(r.content) < min_bytes:
        raise RuntimeError(f"Archivo vacío ({len(r.content)} bytes)")
    img = Image.open(BytesIO(r.content))
    img.load()
    return img.convert("RGBA")

def analizar_pixeles(img_rgb, cx, cy, radio_px, modo="radar"):
    ancho, alto = img_rgb.size
    x0 = max(0, cx - radio_px)
    y0 = max(0, cy - radio_px)
    x1 = min(ancho, cx + radio_px)
    y1 = min(alto, cy + radio_px)

    granizo = 0
    tormenta = 0
    lluvia = 0
    r2 = radio_px * radio_px

    for y in range(y0, y1):
        dy = y - cy
        for x in range(x0, x1):
            dx = x - cx
            if dx * dx + dy * dy > r2:
                continue
            r, g, b = img_rgb.getpixel((x, y))
            if r == g == b:
                continue
            if modo == "satelite":
                if (r > 180 and b < 80 and g < 120) or (b > 170 and r > 130 and g < 90):
                    granizo += 1
                elif r > 200 and g > 140 and b < 70:
                    tormenta += 1
                elif g > 140 and b > 140 and r < 110:
                    lluvia += 1
            else:
                if r > 190 and g < 85 and b < 85:
                    granizo += 1
                elif r > 210 and 100 <= g <= 215 and b < 70:
                    tormenta += 1
                elif (g > 135 and r < 150 and b < 130) or (b > 160 and r < 100):
                    lluvia += 1

    if granizo >= 4:
        return "🔴 *ALTA* (Topes nubosos muy fríos / >55 dBZ — posible granizo)"
    elif tormenta >= 8:
        return "🟠 *MEDIA-ALTA* (Tormenta convectiva activa / 45–55 dBZ)"
    elif lluvia >= 15 or tormenta >= 2:
        return "🟡 *BAJA* (Nubosidad con precipitación moderada / celdas aisladas)"
    return "🟢 *NULA* (Cielo despejado o sin ecos significativos en la zona)"

def obtener_mapa_rainviewer_centrado(lat, lon):
    zoom = 7
    tx, ty, ox, oy = gps_a_tile_xy_y_offset(lat, lon, zoom)
    api = requests.get("https://api.rainviewer.com/public/weather-maps.json", timeout=10).json()
    host = api["host"]
    ultimo_radar = api["radar"]["past"][-1]["path"]

    mosaico_base = Image.new("RGBA", (768, 768))
    mosaico_radar = Image.new("RGBA", (768, 768))

    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            url_mapa = f"https://tile.openstreetmap.org/{zoom}/{tx + dx}/{ty + dy}.png"
            url_radar = f"{host}{ultimo_radar}/256/{zoom}/{tx + dx}/{ty + dy}/2/1_1.png"
            tile_m = descargar_imagen_valida(url_mapa, timeout=10, min_bytes=200)
            tile_r = descargar_imagen_valida(url_radar, timeout=10, min_bytes=50)
            mosaico_base.paste(tile_m, ((dx + 1) * 256, (dy + 1) * 256))
            mosaico_radar.paste(tile_r, ((dx + 1) * 256, (dy + 1) * 256))

    cx_total = 256 + ox
    cy_total = 256 + oy
    mitad = 256

    recorte_base = mosaico_base.crop((cx_total - mitad, cy_total - mitad, cx_total + mitad, cy_total + mitad))
    recorte_radar = mosaico_radar.crop((cx_total - mitad, cy_total - mitad, cx_total + mitad, cy_total + mitad))

    km_por_pixel = (156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)) / 1000.0
    radio_px = max(10, int(round(RADIO_KM / km_por_pixel)))

    combinada = Image.alpha_composite(recorte_base, recorte_radar)
    return combinada.convert("RGB"), recorte_radar.convert("RGB"), "Radar Meteorológico (RainViewer + Mapa)", mitad, mitad, radio_px, "radar"

def obtener_imagen_radar(lat, lon):
    errores = []

    try:
        return obtener_mapa_rainviewer_centrado(lat, lon)
    except Exception as e:
        errores.append(f"RainViewer: {e}")

    urls_goes = [
        ("https://cdn.star.nesdis.noaa.gov/GOES19/ABI/SECTOR/ssa/13/latest.jpg", "Satélite GOES-19 Infrarrojo (Topes Nubosos)"),
        ("https://cdn.star.nesdis.noaa.gov/GOES19/ABI/SECTOR/ssa/GEOCOLOR/latest.jpg", "Satélite GOES-19 Color Real")
    ]
    bbox_ssa = {"lat_max": -18.0, "lat_min": -56.0, "lon_min": -78.0, "lon_max": -48.0}
    for url_goes, nombre_goes in urls_goes:
        try:
            img = descargar_imagen_valida(url_goes, timeout=12, min_bytes=1000).convert("RGB")
            ancho, alto = img.size
            px, py = gps_a_pixel(lat, lon, bbox_ssa, ancho, alto)
            km_por_pixel = ((bbox_ssa["lat_max"] - bbox_ssa["lat_min"]) * 111.32) / alto
            radio_px = max(10, int(round(RADIO_KM / km_por_pixel)))
            zoom_crop = max(180, radio_px + 60)
            x0 = max(0, px - zoom_crop)
            y0 = max(0, py - zoom_crop)
            x1 = min(ancho, px + zoom_crop)
            y1 = min(alto, py + zoom_crop)
            recorte = img.crop((x0, y0, x1, y1))
            return recorte, recorte, nombre_goes, px - x0, py - y0, radio_px, "satelite"
        except Exception as e:
            errores.append(f"GOES-19: {e}")

    raise RuntimeError(" | ".join(errores))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    boton = KeyboardButton(text="📍 Consultar granizo en mi ubicación", request_location=True)
    teclado = ReplyKeyboardMarkup([[boton]], resize_keyboard=True)
    await update.message.reply_text(
        f"👋 *Radar de Tormentas y Granizo listo.*\n\n"
        f"Tocá el botón de abajo para enviarme tu ubicación GPS y analizar un radio exacto de *{int(RADIO_KM)} km a la redonda*.",
        parse_mode="Markdown",
        reply_markup=teclado
    )

async def recibir_ubicacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lat = update.message.location.latitude
    lon = update.message.location.longitude
    msg_espera = await update.message.reply_text(f"🛰️ Escaneando radares en un radio de {int(RADIO_KM)} km...")

    try:
        img_visual, img_analisis, fuente, px, py, radio_px, modo = obtener_imagen_radar(lat, lon)
        estado_entorno = analizar_pixeles(img_analisis, px, py, radio_px, modo=modo)

        draw = ImageDraw.Draw(img_visual)
        draw.ellipse((px - radio_px, py - radio_px, px + radio_px, py + radio_px), outline="#0077FF", width=3)
        draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill="#FF0000")

        buf = BytesIO()
        img_visual.save(buf, format="PNG")
        buf.seek(0)

        caption = (
            f"📡 *Fuente:* {fuente}\n"
            f"📍 *Radio analizado:* {int(RADIO_KM)} km a la redonda\n"
            f"⛈️ *Actividad detectada:*\n{estado_entorno}"
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
