import os
import re
import json
import uuid
import shutil
import hashlib
import logging
import asyncio
import subprocess
from collections import deque
from datetime import datetime

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
TRIM_REGEX = re.compile(r"^\d+:\d+\s+\d+:\d+$")
WORK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot_work")

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

PLAYLIST_FORMATS = [
    {
        "label": "🎬 1080p",
        "ext": "mp4",
        "format": "bestvideo[height<=1080][vcodec^=avc]+bestaudio/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    },
    {
        "label": "🎬 720p",
        "ext": "mp4",
        "format": "bestvideo[height<=720][vcodec^=avc]+bestaudio/bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    },
    {
        "label": "🎬 480p",
        "ext": "mp4",
        "format": "bestvideo[height<=480][vcodec^=avc]+bestaudio/bestvideo[height<=480]+bestaudio/best[height<=480]/best",
    },
    {
        "label": "🎬 360p",
        "ext": "mp4",
        "format": "bestvideo[height<=360][vcodec^=avc]+bestaudio/bestvideo[height<=360]+bestaudio/best[height<=360]/best",
    },
    {
        "label": "🎵 MP3 Audio",
        "ext": "mp3",
        "format": "bestaudio/best",
    },
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


def ensure_runtime_state(bot_data: dict):
    bot_data.setdefault("video_cache", {})
    bot_data.setdefault("user_locks", {})
    bot_data.setdefault("history", {})


def get_user_lock(user_id: int, bot_data: dict) -> asyncio.Lock:
    ensure_runtime_state(bot_data)
    lock = bot_data["user_locks"].get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        bot_data["user_locks"][user_id] = lock
    return lock


def get_user_history(user_id: int, bot_data: dict) -> deque:
    ensure_runtime_state(bot_data)
    history = bot_data["history"].get(user_id)
    if history is None:
        history = deque(maxlen=10)
        bot_data["history"][user_id] = history
    return history


def add_history_entry(bot_data: dict, user_id: int, title: str, url_key: str, fmt_idx: int, fmt_label: str):
    history = get_user_history(user_id, bot_data)
    history.appendleft({
        "title": title,
        "url_key": url_key,
        "fmt_idx": fmt_idx,
        "fmt_label": fmt_label,
        "ts": datetime.utcnow().isoformat(timespec="seconds"),
    })


def is_valid_url(text: str) -> bool:
    return bool(URL_REGEX.search(text))


def parse_time(value: str) -> str:
    minutes, seconds = value.split(":", 1)
    total_seconds = int(minutes) * 60 + int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    mins, secs = divmod(remainder, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}"


def parse_time_seconds(value: str) -> int:
    minutes, seconds = value.split(":", 1)
    return int(minutes) * 60 + int(seconds)


def trim_video(filepath: str, start_time: str, end_time: str) -> str:
    output_path = filepath.rsplit(".", 1)[0] + "_trimmed.mp4"
    subprocess.run(
        [
            "ffmpeg", "-i", filepath, "-ss", start_time, "-to", end_time,
            "-c", "copy", "-y", output_path,
        ],
        capture_output=True,
        check=True,
    )
    return output_path


def make_work_dir() -> str:
    os.makedirs(WORK_DIR, exist_ok=True)
    path = os.path.join(WORK_DIR, uuid.uuid4().hex)
    os.makedirs(path, exist_ok=False)
    return path


def get_video_info(url: str) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": "in_playlist"}) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") == "playlist":
        entries = [entry for entry in (info.get("entries") or []) if entry]
        return {
            "is_playlist": True,
            "title": info.get("title", "playlist"),
            "count": len(entries),
        }

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

    formats.append({"label": "🎵 MP3 Audio", "format": "bestaudio/best", "ext": "mp3"})
    return {"is_playlist": False, "title": title, "formats": formats}


def get_playlist_entries(url: str, count: int) -> tuple[str, list[dict]]:
    ydl_opts = {"quiet": True, "noplaylist": False}
    if count > 0:
        ydl_opts["playlistend"] = count
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = [entry for entry in (info.get("entries") or []) if entry]
    return info.get("title", "playlist"), entries


def build_keyboard(url_key: str, formats: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(fmt["label"], callback_data=f"{i}|{url_key}")]
        for i, fmt in enumerate(formats)
    ]
    buttons.append([InlineKeyboardButton("✂️ Recortar video", callback_data=f"trim_menu:{url_key}")])
    return InlineKeyboardMarkup(buttons)


def build_trim_keyboard(url_key: str, formats: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(fmt["label"], callback_data=f"trim:{i}:{url_key}")]
        for i, fmt in enumerate(formats)
    ]
    return InlineKeyboardMarkup(buttons)


def build_playlist_count_keyboard(url_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Primeros 5", callback_data=f"plist:5:{url_key}")],
        [InlineKeyboardButton("Primeros 10", callback_data=f"plist:10:{url_key}")],
        [InlineKeyboardButton("Primeros 20", callback_data=f"plist:20:{url_key}")],
        [InlineKeyboardButton("Todos", callback_data=f"plist:0:{url_key}")],
    ])


def build_playlist_format_keyboard(url_key: str, count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(fmt["label"], callback_data=f"pfmt:{count}:{i}:{url_key}")]
        for i, fmt in enumerate(PLAYLIST_FORMATS)
    ])


def build_history_keyboard(history: deque) -> InlineKeyboardMarkup:
    buttons = []
    for item in list(history)[:10]:
        label = f"{item['title']} ({item['fmt_label']})"
        buttons.append([
            InlineKeyboardButton(label[:64], callback_data=f"hist:{item['url_key']}:{item['fmt_idx']}")
        ])
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
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegVideoRemuxer",
            "preferedformat": "mp4",
        }]
        ydl_opts["postprocessor_args"] = {
            "ffmpegvideoremuxer": ["-c", "copy"]
        }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    files = sorted(os.listdir(output_dir))
    return os.path.join(output_dir, files[0]) if files else None


async def try_acquire_user_lock(user_id: int, bot_data: dict) -> asyncio.Lock | None:
    lock = get_user_lock(user_id, bot_data)
    if lock.locked():
        return None
    await lock.acquire()
    return lock


async def perform_download(
    context: ContextTypes.DEFAULT_TYPE,
    status_message,
    reply_target,
    *,
    user_id: int,
    url: str,
    url_key: str,
    fmt_idx: int,
    fmt: dict,
    title: str,
    trim_range: tuple[str, str] | None = None,
) -> str:
    ensure_runtime_state(context.bot_data)
    cache_key = hashlib.md5(f"{url_key}:{fmt['label']}".encode()).hexdigest()
    is_trimmed = trim_range is not None

    if fmt["ext"] != "mp3" and not is_trimmed:
        cached_file_id = context.bot_data["video_cache"].get(cache_key)
        if cached_file_id:
            await status_message.edit_text("📦 Usando caché...")
            await reply_target.reply_video(video=cached_file_id)
            add_history_entry(context.bot_data, user_id, title, url_key, fmt_idx, fmt["label"])
            return "cached"

    workdir = make_work_dir()
    try:
        await status_message.edit_text(f"⬇️ Descargando en {fmt['label']}... esto puede tardar un momento.")
        loop = asyncio.get_running_loop()
        filepath = await loop.run_in_executor(None, download_video, url, fmt, workdir)
        if not filepath or not os.path.exists(filepath):
            await status_message.edit_text("❌ Error al descargar el video.")
            return "error"

        logger.info(f"Downloaded file: {filepath}, size: {os.path.getsize(filepath)}")

        if trim_range:
            if fmt["ext"] == "mp3":
                await status_message.edit_text("❌ El recorte solo está disponible para video.")
                return "error"
            await status_message.edit_text("✂️ Recortando video...")
            filepath = await loop.run_in_executor(None, trim_video, filepath, trim_range[0], trim_range[1])

        with open(filepath, "rb") as media_file:
            if fmt["ext"] == "mp3":
                await status_message.edit_text("📤 Enviando audio...")
                await reply_target.reply_audio(audio=media_file)
                add_history_entry(context.bot_data, user_id, title, url_key, fmt_idx, fmt["label"])
                return "success"

        size_mb = os.path.getsize(filepath) / 1024 / 1024
        await status_message.edit_text(
            f"🔍 *Analizando video* ({size_mb:.0f} MB)...",
            parse_mode="Markdown"
        )
        analysis = await loop.run_in_executor(None, analyze_video, filepath)

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
            "⚙️ *Procesando video*\n\n"
            + "\n".join(f"  • {step}" for step in steps)
            + f"\n\n⏳ Tiempo estimado: {eta}"
        )
        await status_message.edit_text(status, parse_mode="Markdown")

        filepath = await loop.run_in_executor(None, do_process_video, filepath, analysis)

        await status_message.edit_text("📤 *Enviando video...*", parse_mode="Markdown")
        width, height = await loop.run_in_executor(None, get_video_dimensions, filepath)
        with open(filepath, "rb") as video_file:
            sent_message = await reply_target.reply_video(
                video=video_file,
                width=width or None,
                height=height or None,
                supports_streaming=True,
            )
        if sent_message.video and not is_trimmed:
            context.bot_data["video_cache"][cache_key] = sent_message.video.file_id
        add_history_entry(context.bot_data, user_id, title, url_key, fmt_idx, fmt["label"])
        return "success"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Video Downloader Bot*\n\n"
        "Envíame el enlace de un video de:\n"
        "▶️ YouTube\n📘 Facebook\n📸 Instagram\n🎵 TikTok",
        parse_mode="Markdown"
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    history = get_user_history(user_id, context.bot_data)
    if not history:
        await update.message.reply_text("📭 No tienes descargas recientes.")
        return

    await update.message.reply_text(
        "🕘 Tus últimas descargas:",
        reply_markup=build_history_keyboard(history),
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_runtime_state(context.bot_data)
    text = update.message.text.strip()
    trim_pending = context.user_data.get("trim_pending")

    if trim_pending:
        if not TRIM_REGEX.match(text):
            await update.message.reply_text(
                "✂️ Escribe inicio y fin en formato `MM:SS MM:SS`\nEjemplo: `0:30 2:15`",
                parse_mode="Markdown"
            )
            return

        start_raw, end_raw = text.split()
        start_seconds = parse_time_seconds(start_raw)
        end_seconds = parse_time_seconds(end_raw)
        if end_seconds <= start_seconds:
            await update.message.reply_text("❌ El tiempo final debe ser mayor que el inicial.")
            return

        pending = context.user_data.pop("trim_pending")
        entry = context.bot_data.get(pending["url_key"])
        if not entry:
            await update.message.reply_text("❌ URL expirada. Envía el enlace de nuevo.")
            return

        fmt_idx = pending["fmt_idx"]
        fmt = entry["formats"][fmt_idx]
        lock = await try_acquire_user_lock(update.effective_user.id, context.bot_data)
        if lock is None:
            await update.message.reply_text("⏳ Ya hay una descarga en curso. Espera un momento...")
            return

        status_message = await update.message.reply_text("✂️ Preparando recorte...")
        try:
            await perform_download(
                context,
                status_message,
                update.message,
                user_id=update.effective_user.id,
                url=entry["url"],
                url_key=pending["url_key"],
                fmt_idx=fmt_idx,
                fmt=fmt,
                title=entry.get("title", "video"),
                trim_range=(parse_time(start_raw), parse_time(end_raw)),
            )
        except Exception as e:
            logger.error(f"Trim download error: {e}")
            await status_message.edit_text("❌ Ocurrió un error durante el recorte.")
        finally:
            lock.release()
        return

    if not is_valid_url(text):
        await update.message.reply_text("❌ Envíame una URL válida (debe comenzar con http:// o https://).")
        return

    msg = await update.message.reply_text("🔍 Obteniendo información del video...")
    try:
        info = await asyncio.get_running_loop().run_in_executor(None, get_video_info, text)
        url_key = hashlib.md5(text.encode()).hexdigest()[:12]

        if info["is_playlist"]:
            context.bot_data[url_key] = {
                "url": text,
                "title": info["title"],
                "is_playlist": True,
            }
            await msg.edit_text(
                f"🎬 *{info['title']}*\n{info['count']} videos\n\nCuántos descargar?",
                reply_markup=build_playlist_count_keyboard(url_key),
                parse_mode="Markdown"
            )
            return

        context.bot_data[url_key] = {
            "url": text,
            "title": info["title"],
            "formats": info["formats"],
            "is_playlist": False,
        }
        await msg.edit_text(
            f"📹 *{info['title']}*\n\nElige el formato de descarga:",
            reply_markup=build_keyboard(url_key, info["formats"]),
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

        lock = await try_acquire_user_lock(query.from_user.id, context.bot_data)
        if lock is None:
            await query.edit_message_text("⏳ Ya hay una descarga en curso. Espera un momento...")
            return

        try:
            result = await perform_download(
                context,
                query.message,
                query.message,
                user_id=query.from_user.id,
                url=entry["url"],
                url_key=url_key,
                fmt_idx=int(idx),
                fmt=entry["formats"][int(idx)],
                title=entry.get("title", "video"),
            )
            if result == "success":
                await query.delete_message()
        finally:
            lock.release()
    except Exception as e:
        logger.error(f"Download error: {e}")
        await query.edit_message_text("❌ Ocurrió un error durante la descarga.")


async def handle_trim_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, url_key = query.data.split(":", 1)
    entry = context.bot_data.get(url_key)
    if not entry:
        await query.edit_message_text("❌ URL expirada. Envía el enlace de nuevo.")
        return

    await query.edit_message_text(
        "✂️ Elige el formato y luego dime los tiempos.",
        reply_markup=build_trim_keyboard(url_key, entry["formats"]),
    )


async def handle_trim_format_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        _, idx, url_key = query.data.split(":", 2)
        entry = context.bot_data.get(url_key)
        if not entry:
            await query.edit_message_text("❌ URL expirada. Envía el enlace de nuevo.")
            return

        context.user_data["trim_pending"] = {"url_key": url_key, "fmt_idx": int(idx)}
        await query.edit_message_text(
            "✂️ Escribe inicio y fin en formato `MM:SS MM:SS`\nEjemplo: `0:30 2:15`",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Trim selection error: {e}")
        await query.edit_message_text("❌ No pude preparar el recorte.")


async def handle_history_redownload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        _, url_key, fmt_idx = query.data.split(":", 2)
        entry = context.bot_data.get(url_key)
        if not entry:
            await query.edit_message_text("❌ Ese elemento del historial ya expiró. Envía el enlace de nuevo.")
            return

        lock = await try_acquire_user_lock(query.from_user.id, context.bot_data)
        if lock is None:
            await query.edit_message_text("⏳ Ya hay una descarga en curso. Espera un momento...")
            return

        try:
            result = await perform_download(
                context,
                query.message,
                query.message,
                user_id=query.from_user.id,
                url=entry["url"],
                url_key=url_key,
                fmt_idx=int(fmt_idx),
                fmt=entry["formats"][int(fmt_idx)],
                title=entry.get("title", "video"),
            )
            if result == "success":
                await query.delete_message()
        finally:
            lock.release()
    except Exception as e:
        logger.error(f"History redownload error: {e}")
        await query.edit_message_text("❌ No pude repetir esa descarga.")


async def handle_playlist_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        _, count, url_key = query.data.split(":", 2)
        entry = context.bot_data.get(url_key)
        if not entry:
            await query.edit_message_text("❌ URL expirada. Envía el enlace de nuevo.")
            return

        count_value = int(count)
        count_label = "Todos" if count_value == 0 else f"Primeros {count_value}"
        await query.edit_message_text(
            f"🎬 *{entry['title']}*\n\n{count_label}. Elige el formato:",
            reply_markup=build_playlist_format_keyboard(url_key, count_value),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Playlist count error: {e}")
        await query.edit_message_text("❌ No pude preparar la playlist.")


async def handle_playlist_fmt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        _, count, idx, url_key = query.data.split(":", 3)
        entry = context.bot_data.get(url_key)
        if not entry:
            await query.edit_message_text("❌ URL expirada. Envía el enlace de nuevo.")
            return

        lock = await try_acquire_user_lock(query.from_user.id, context.bot_data)
        if lock is None:
            await query.edit_message_text("⏳ Ya hay una descarga en curso. Espera un momento...")
            return

        try:
            count_value = int(count)
            fmt_idx = int(idx)
            fmt = PLAYLIST_FORMATS[fmt_idx]
            loop = asyncio.get_running_loop()
            playlist_title, entries = await loop.run_in_executor(None, get_playlist_entries, entry["url"], count_value)
            total = len(entries)
            if total == 0:
                await query.edit_message_text("❌ No pude obtener videos de la playlist.")
                return

            for position, item in enumerate(entries, start=1):
                item_url = item.get("webpage_url") or item.get("url")
                if not item_url:
                    continue

                item_title = item.get("title") or f"{playlist_title} #{position}"
                item_url_key = hashlib.md5(item_url.encode()).hexdigest()[:12]
                context.bot_data[item_url_key] = {
                    "url": item_url,
                    "title": item_title,
                    "formats": PLAYLIST_FORMATS,
                    "is_playlist": False,
                }
                await query.message.edit_text(f"⬇️ Descargando video {position}/{total}...")
                await perform_download(
                    context,
                    query.message,
                    query.message,
                    user_id=query.from_user.id,
                    url=item_url,
                    url_key=item_url_key,
                    fmt_idx=fmt_idx,
                    fmt=fmt,
                    title=item_title,
                )

            await query.message.edit_text("✅ Descarga de playlist completada.")
        finally:
            lock.release()
    except Exception as e:
        logger.error(f"Playlist download error: {e}")
        await query.edit_message_text("❌ Ocurrió un error al descargar la playlist.")


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN no está configurado en el archivo .env")

    request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=60,
        write_timeout=300,
        pool_timeout=30,
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .build()
    )
    ensure_runtime_state(app.bot_data)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(handle_trim_menu, pattern=r"^trim_menu:"))
    app.add_handler(CallbackQueryHandler(handle_trim_format_selection, pattern=r"^trim:"))
    app.add_handler(CallbackQueryHandler(handle_history_redownload, pattern=r"^hist:"))
    app.add_handler(CallbackQueryHandler(handle_playlist_count, pattern=r"^plist:"))
    app.add_handler(CallbackQueryHandler(handle_playlist_fmt, pattern=r"^pfmt:"))
    app.add_handler(CallbackQueryHandler(handle_format_selection, pattern=r"^\d+\|"))

    logger.info("Bot iniciado...")
    app.run_polling()


if __name__ == "__main__":
    main()
