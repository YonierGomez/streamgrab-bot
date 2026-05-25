import os
import re
import json
import hashlib
import tempfile
import logging
import asyncio
import subprocess
from dotenv import load_dotenv
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.request import HTTPXRequest

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
URL_REGEX = re.compile(r"https?://[^\s]+")

RESOLUTION_PRESETS = [
    (4320, "🎬 8K (4320p)"),
    (2160, "🎬 4K (2160p)"),
    (1440, "🎬 1440p"),
    (1080, "🎬 1080p"),
    (720,  "🎬 720p"),
    (480,  "🎬 480p"),
    (360,  "🎬 360p"),
    (240,  "🎬 240p"),
]


def _probe_video_stream(filepath: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", filepath],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    return {}


def get_video_dimensions(filepath: str) -> tuple[int, int]:
    try:
        stream = _probe_video_stream(filepath)
        width = stream.get("width", 0)
        height = stream.get("height", 0)
        rotate = int(stream.get("tags", {}).get("rotate", 0))
        if rotate in (90, 270):
            width, height = height, width
        return width, height
    except Exception:
        pass
    return 0, 0


def analyze_video(filepath: str, max_bytes: int = 49 * 1024 * 1024) -> dict:
    """Probe the video and determine what processing is needed."""
    stream = _probe_video_stream(filepath)
    coded_w = stream.get("width", 0)
    coded_h = stream.get("height", 0)
    sar = stream.get("sample_aspect_ratio") or "1:1"
    rotate = stream.get("tags", {}).get("rotate") or "0"
    codec = stream.get("codec_name", "")
    file_size = os.path.getsize(filepath)

    detect = subprocess.run(
        ["ffmpeg", "-i", filepath, "-vf", "cropdetect=24:16:0",
         "-frames:v", "50", "-f", "null", "-"],
        capture_output=True, text=True
    )
    crop_param = None
    for line in detect.stderr.splitlines():
        if "crop=" in line:
            crop_param = line.split("crop=")[-1].strip()

    needs_crop = False
    if crop_param:
        parts = crop_param.split(":")
        if len(parts) >= 2:
            cw, ch = int(parts[0]), int(parts[1])
            needs_crop = cw < coded_w * 0.99 or ch < coded_h * 0.99

    duration = 0.0
    if file_size > max_bytes:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", filepath],
            capture_output=True, text=True,
        )
        duration = float(probe.stdout.strip() or 0)

    return {
        "codec": codec,
        "sar": sar,
        "rotate": rotate,
        "crop_param": crop_param if needs_crop else None,
        "needs_encode": codec not in ("h264", "avc1"),
        "needs_sar_fix": sar not in ("1:1", "0:1", ""),
        "needs_rotate_fix": rotate not in ("0", ""),
        "needs_crop": needs_crop,
        "needs_compress": file_size > max_bytes,
        "file_size": file_size,
        "duration": duration,
    }


def do_process_video(filepath: str, analysis: dict, max_bytes: int = 49 * 1024 * 1024) -> str:
    """Run the actual ffmpeg processing based on analysis."""
    try:
        a = analysis
        if not any([a["needs_encode"], a["needs_sar_fix"], a["needs_rotate_fix"],
                    a["needs_crop"], a["needs_compress"]]):
            return filepath

        filters = []
        if a["needs_sar_fix"]:
            filters.append("scale=iw*sar:ih,setsar=1")
        if a["needs_crop"] and a["crop_param"]:
            filters.append(f"crop={a['crop_param']}")

        output_path = filepath.rsplit(".", 1)[0] + "_out.mp4"
        cmd = ["ffmpeg", "-i", filepath]
        if filters:
            cmd += ["-vf", ",".join(filters)]

        if a["needs_compress"]:
            safe_bytes = int(max_bytes * 0.96)
            target_bps = max(150_000, int(safe_bytes * 8 / a["duration"]) - 128_000) if a["duration"] > 0 else 500_000
            cmd += ["-c:v", "libx264", "-b:v", str(target_bps), "-c:a", "aac", "-b:a", "128k"]
        else:
            cmd += ["-c:v", "libx264", "-crf", "18", "-c:a", "copy"]

        cmd += ["-preset", "veryfast", "-movflags", "+faststart", "-y", output_path]
        subprocess.run(cmd, capture_output=True, check=True)
        result_mb = os.path.getsize(output_path) / 1024 / 1024
        logger.info(f"Processed: {result_mb:.1f}MB analysis={a}")
        return output_path
    except Exception as e:
        logger.warning(f"Video processing failed: {e}")
        return filepath


def is_valid_url(text: str) -> bool:
    return bool(URL_REGEX.search(text))


def get_video_info(url: str) -> tuple[str, list[dict]]:
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(url, download=False)

    title = info.get("title", "video")
    available_heights = {
        fmt.get("height")
        for fmt in info.get("formats", [])
        if fmt.get("height")
    }

    formats = []
    for height, label in RESOLUTION_PRESETS:
        if any(h >= height for h in available_heights):
            formats.append({
                "label": label,
                "ext": "mp4",
                "format": (
                    f"bestvideo[height<={height}][vcodec^=avc]+bestaudio"
                    f"/bestvideo[height<={height}]+bestaudio"
                    f"/best[height<={height}]/best"
                ),
            })

    # Always offer MP3
    formats.append({"label": "🎵 MP3 Audio", "format": "bestaudio/best", "ext": "mp3"})
    return title, formats


def build_keyboard(url_key: str, formats: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(f["label"], callback_data=f"{i}|{url_key}")]
        for i, f in enumerate(formats)
    ]
    return InlineKeyboardMarkup(buttons)


def download_video(url: str, fmt: dict, output_dir: str) -> str | None:
    ydl_opts = {
        "format": fmt["format"],
        "outtmpl": os.path.join(output_dir, "output.%(ext)s"),
        "quiet": True,
        "noplaylist": True,
        "merge_output_format": fmt["ext"],
    }
    if fmt["ext"] == "mp3":
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        # Re-encode to H.264 only if needed (AV1, VP9, etc.)
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegVideoRemuxer",
            "preferedformat": "mp4",
        }]
        ydl_opts["postprocessor_args"] = {
            "ffmpegvideoremuxer": ["-c", "copy"]
        }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    files = os.listdir(output_dir)
    return os.path.join(output_dir, files[0]) if files else None


# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Video Downloader Bot*\n\n"
        "Envíame el enlace de un video de:\n"
        "▶️ YouTube\n📘 Facebook\n📸 Instagram\n🎵 TikTok",
        parse_mode="Markdown"
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not is_valid_url(url):
        await update.message.reply_text("❌ Envíame una URL válida (debe comenzar con http:// o https://).")
        return

    msg = await update.message.reply_text("🔍 Obteniendo información del video...")
    try:
        title, formats = await asyncio.get_event_loop().run_in_executor(None, get_video_info, url)
        url_key = hashlib.md5(url.encode()).hexdigest()[:12]
        context.bot_data[url_key] = {"url": url, "formats": formats}
        await msg.edit_text(
            f"📹 *{title}*\n\nElige el formato de descarga:",
            reply_markup=build_keyboard(url_key, formats),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error fetching info: {e}")
        await msg.edit_text("❌ No pude obtener información del video. Verifica el enlace.")


async def handle_format_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        idx, url_key = query.data.split("|", 1)
        entry = context.bot_data.get(url_key)
        if not entry:
            await query.edit_message_text("❌ URL expirada. Envía el enlace de nuevo.")
            return
        url = entry["url"]
        fmt = entry["formats"][int(idx)]

        await query.edit_message_text(f"⬇️ Descargando en {fmt['label']}... esto puede tardar un momento.")

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = await asyncio.get_event_loop().run_in_executor(
                None, download_video, url, fmt, tmpdir
            )
            if not filepath or not os.path.exists(filepath):
                await query.edit_message_text("❌ Error al descargar el video.")
                return

            logger.info(f"Downloaded file: {filepath}, size: {os.path.getsize(filepath)}")

            with open(filepath, "rb") as f:
                if fmt["ext"] == "mp3":
                    await query.edit_message_text("📤 Enviando audio...")
                    await query.message.reply_audio(audio=f)
                else:
                    size_mb = os.path.getsize(filepath) / 1024 / 1024
                    await query.edit_message_text(
                        f"🔍 *Analizando video* ({size_mb:.0f} MB)...",
                        parse_mode="Markdown"
                    )
                    loop = asyncio.get_event_loop()
                    analysis = await loop.run_in_executor(None, analyze_video, filepath)

                    # Build step-by-step status
                    steps = []
                    if analysis["needs_encode"]:
                        steps.append(f"🔄 Convertir codec `{analysis['codec']}` → H.264")
                    if analysis["needs_crop"]:
                        steps.append("✂️ Recortar barras negras")
                    if analysis["needs_sar_fix"]:
                        steps.append("📐 Corregir proporción de píxeles")
                    if analysis["needs_compress"]:
                        steps.append(f"📦 Comprimir {size_mb:.0f}MB → <50MB")
                    if not steps:
                        steps.append("✅ Sin cambios necesarios, enviando tal cual")

                    eta = "~1 minuto" if analysis["needs_compress"] else "~segundos"
                    status = (
                        f"⚙️ *Procesando video*\n\n"
                        + "\n".join(f"  • {s}" for s in steps)
                        + f"\n\n⏳ Tiempo estimado: {eta}"
                    )
                    await query.edit_message_text(status, parse_mode="Markdown")

                    filepath = await loop.run_in_executor(None, do_process_video, filepath, analysis)

                    await query.edit_message_text("📤 *Enviando video...*", parse_mode="Markdown")
                    width, height = get_video_dimensions(filepath)
                    with open(filepath, "rb") as fv:
                        await query.message.reply_video(
                            video=fv,
                            width=width or None,
                            height=height or None,
                            supports_streaming=True,
                        )

        await query.delete_message()

    except Exception as e:
        logger.error(f"Download error: {e}")
        await query.edit_message_text("❌ Ocurrió un error durante la descarga.")


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN no está configurado en el archivo .env")

    request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=60,
        write_timeout=60,
        pool_timeout=30,
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(handle_format_selection))

    logger.info("Bot iniciado...")
    app.run_polling()


if __name__ == "__main__":
    main()
