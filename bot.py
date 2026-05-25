import io
import os
import re
import json
import uuid
import shutil
import hashlib
import logging
import asyncio
import subprocess
import urllib.request
from collections import deque
from datetime import datetime, timedelta

from dotenv import load_dotenv
import yt_dlp
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.request import HTTPXRequest

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else None
USE_LOCAL_API = os.getenv("TELEGRAM_LOCAL", "").lower() in ("1", "true", "yes")
LOCAL_API_URL = "http://telegram-bot-api:8081/bot"
COOKIES_FILE = os.getenv("COOKIES_FILE", "")
if COOKIES_FILE and not os.path.isfile(COOKIES_FILE):
    logger.warning("COOKIES_FILE '%s' no existe — se ignorará", COOKIES_FILE)
    COOKIES_FILE = ""
URL_REGEX = re.compile(r"https?://[^\s]+")
TRIM_REGEX = re.compile(r"^\d+:\d+\s+\d+:\d+$")
WORK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot_work")
SUPPORTED_DOMAINS = {
    "youtube.com", "youtu.be", "facebook.com", "fb.watch", "instagram.com",
    "tiktok.com", "twitter.com", "x.com", "vimeo.com", "reddit.com", "twitch.tv",
}
DOWNLOAD_PROGRESS = {}

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
            cmd += ["-c:v", "libx264", "-crf", "16", "-c:a", "copy"]

        cmd += ["-preset", "fast", "-movflags", "+faststart", "-y", output_path]
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
    bot_data.setdefault("cancellation_flags", {})
    stats = bot_data.setdefault("stats", {})
    stats.setdefault("total", 0)
    stats.setdefault("by_user", {})
    stats.setdefault("by_platform", {})
    stats.setdefault("by_day", {})
    stats.setdefault("by_user_platform", {})


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


class UserCancelledError(Exception):
    pass


def get_cancellation_event(user_id: int, bot_data: dict) -> asyncio.Event:
    ensure_runtime_state(bot_data)
    event = bot_data["cancellation_flags"].get(user_id)
    if event is None:
        event = asyncio.Event()
        bot_data["cancellation_flags"][user_id] = event
    return event


def detect_platform(url: str) -> str:
    lowered = url.lower()
    if any(domain in lowered for domain in ("youtube.com", "youtu.be")):
        return "YouTube"
    if any(domain in lowered for domain in ("instagram.com",)):
        return "Instagram"
    if any(domain in lowered for domain in ("tiktok.com",)):
        return "TikTok"
    if any(domain in lowered for domain in ("facebook.com", "fb.watch")):
        return "Facebook"
    if any(domain in lowered for domain in ("twitter.com", "x.com")):
        return "Twitter"
    if "vimeo.com" in lowered:
        return "Vimeo"
    if "reddit.com" in lowered:
        return "Reddit"
    if "twitch.tv" in lowered:
        return "Twitch"
    return "Otro"


def record_stat(bot_data: dict, user_id: int, url: str, fmt_label: str):
    ensure_runtime_state(bot_data)
    stats = bot_data["stats"]
    platform = detect_platform(url)
    today = datetime.utcnow().strftime("%Y-%m-%d")

    stats["total"] += 1
    stats["by_user"][user_id] = stats["by_user"].get(user_id, 0) + 1
    stats["by_platform"][platform] = stats["by_platform"].get(platform, 0) + 1
    stats["by_day"][today] = stats["by_day"].get(today, 0) + 1
    user_platforms = stats["by_user_platform"].setdefault(user_id, {})
    user_platforms[platform] = user_platforms.get(platform, 0) + 1


def ensure_not_cancelled(cancel_event: asyncio.Event | None):
    if cancel_event and cancel_event.is_set():
        raise asyncio.CancelledError


async def run_in_executor_cancellable(loop, cancel_event, func, *args):
    """Run a blocking function in executor, but raise CancelledError immediately if cancel_event is set."""
    fut = loop.run_in_executor(None, func, *args)
    while not fut.done():
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError
        await asyncio.sleep(0.3)
    return await fut


def format_speed(speed: float | None) -> str:
    if not speed:
        return "—"
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    value = float(speed)
    unit_idx = 0
    while value >= 1024 and unit_idx < len(units) - 1:
        value /= 1024
        unit_idx += 1
    decimals = 0 if unit_idx == 0 else 1
    return f"{value:.{decimals}f} {units[unit_idx]}"


def format_eta(eta: int | None) -> str:
    if eta is None:
        return "—"
    eta = max(0, int(eta))
    if eta < 60:
        return f"{eta}s"
    minutes, seconds = divmod(eta, 60)
    return f"{minutes}m {seconds}s"


def build_progress_bar(percent: float, width: int = 8) -> str:
    percent = max(0.0, min(percent, 100.0))
    filled = round((percent / 100) * width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def format_progress_text(percent: float, speed: float | None, eta: int | None) -> str:
    return (
        f"⬇️ Descargando: {build_progress_bar(percent)} {percent:.0f}%"
        f" • {format_speed(speed)} • ETA: {format_eta(eta)}"
    )


def is_retryable_download_error(exc: Exception) -> bool:
    message = str(exc).lower()
    user_error_markers = (
        "unsupported url",
        "video unavailable",
        "requested format is not available",
        "private video",
        "login required",
        "sign in",
        "not available in your country",
        "this live event will begin",
    )
    return not any(marker in message for marker in user_error_markers)


async def safe_edit_text(message, text: str, **kwargs):
    try:
        await message.edit_text(text, **kwargs)
    except Exception as exc:
        if "Message is not modified" not in str(exc):
            raise


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
    opts = {"quiet": True, "extract_flat": "in_playlist"}
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") == "playlist":
        entries = [entry for entry in (info.get("entries") or []) if entry]
        return {
            "is_playlist": True,
            "title": info.get("title", "playlist"),
            "count": len(entries),
        }

    title = info.get("title", "video")
    thumbnail = info.get("thumbnail")
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
    return {"is_playlist": False, "title": title, "thumbnail": thumbnail, "formats": formats}


def get_playlist_entries(url: str, count: int) -> tuple[str, list[dict]]:
    ydl_opts = {"quiet": True, "noplaylist": False}
    if COOKIES_FILE:
        ydl_opts["cookiefile"] = COOKIES_FILE
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
    buttons.append([
        InlineKeyboardButton("📝 Subtítulos", callback_data=f"subs:{url_key}"),
        InlineKeyboardButton("🖼 Portada", callback_data=f"thumb:{url_key}"),
    ])
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


def clear_workdir(path: str):
    for name in os.listdir(path):
        full_path = os.path.join(path, name)
        if os.path.isdir(full_path):
            shutil.rmtree(full_path, ignore_errors=True)
        else:
            try:
                os.remove(full_path)
            except FileNotFoundError:
                pass


def fetch_remote_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def download_subtitles(url: str, output_dir: str) -> list[str]:
    ydl_opts = {
        "quiet": True,
        "noplaylist": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["es", "en"],
        "skip_download": True,
        "outtmpl": os.path.join(output_dir, "subtitle.%(ext)s"),
    }
    if COOKIES_FILE:
        ydl_opts["cookiefile"] = COOKIES_FILE
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    subtitle_files = []
    for name in sorted(os.listdir(output_dir)):
        if name.lower().endswith((".srt", ".vtt")):
            subtitle_files.append(os.path.join(output_dir, name))
    return subtitle_files


async def progress_message_worker(status_message, progress_queue: asyncio.Queue, stop_event: asyncio.Event):
    loop = asyncio.get_running_loop()
    latest = None
    last_text = None
    last_edit_at = 0.0
    while True:
        if stop_event.is_set() and progress_queue.empty():
            break
        try:
            item = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
            if item is None:
                break
            latest = item
        except asyncio.TimeoutError:
            pass

        if latest and loop.time() - last_edit_at >= 2:
            text = format_progress_text(*latest)
            if text != last_text:
                await safe_edit_text(status_message, text)
                last_text = text
            last_edit_at = loop.time()

    if latest:
        text = format_progress_text(*latest)
        if text != last_text:
            await safe_edit_text(status_message, text)


def download_video(
    url: str,
    fmt: dict,
    output_dir: str,
    progress_callback=None,
    cancellation_event: asyncio.Event | None = None,
) -> str | None:
    def progress_hook(data: dict):
        if cancellation_event and cancellation_event.is_set():
            raise UserCancelledError("Download cancelled by user")

        status = data.get("status")
        if status == "downloading":
            total_bytes = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded_bytes = data.get("downloaded_bytes") or 0
            percent = (downloaded_bytes / total_bytes * 100) if total_bytes else 0.0
            speed = data.get("speed")
            eta = data.get("eta")
            DOWNLOAD_PROGRESS[output_dir] = {
                "percent": percent,
                "speed": speed,
                "eta": eta,
            }
            if progress_callback:
                progress_callback(percent, speed, eta)
        elif status == "finished":
            DOWNLOAD_PROGRESS[output_dir] = {"percent": 100.0, "speed": None, "eta": 0}
            if progress_callback:
                progress_callback(100.0, None, 0)

    ydl_opts = {
        "format": fmt["format"],
        "outtmpl": os.path.join(output_dir, "output.%(ext)s"),
        "quiet": True,
        "noplaylist": True,
        "merge_output_format": fmt["ext"],
        "progress_hooks": [progress_hook],
    }
    if COOKIES_FILE:
        ydl_opts["cookiefile"] = COOKIES_FILE
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


# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Video Downloader Bot*\n\n"
        "Envíame el enlace de un video de:\n"
        "▶️ YouTube  📘 Facebook  📸 Instagram  🎵 TikTok\n"
        "🐦 Twitter/X  📹 Vimeo  👾 Reddit  🎮 Twitch",
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


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *¿Qué puedo hacer?*\n\n"
        "Envíame el enlace de un video y te mostraré opciones de descarga.\n\n"
        "🌐 *Plataformas soportadas*\n"
        "▶️ YouTube  📘 Facebook  📸 Instagram  🎵 TikTok\n"
        "🐦 Twitter/X  📹 Vimeo  👾 Reddit  🎮 Twitch\n\n"
        "🎛 *Al descargar puedes:*\n"
        "• Elegir calidad (240p → 8K)\n"
        "• 🖼 Descargar solo la portada\n"
        "• 📝 Obtener subtítulos (.srt)\n"
        "• ✂️ Recortar un fragmento (`MM:SS MM:SS`)\n"
        "• 🎵 Extraer solo el audio (MP3)\n\n"
        "📋 *Comandos disponibles*\n"
        "/history — tus últimas 10 descargas\n"
        "/stats — tus estadísticas de uso\n"
        "/cancel — cancelar descarga en curso\n"
        "/admin — panel de administración _(solo admin)_\n"
        "/help — este mensaje",
        parse_mode="Markdown"
    )



async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    event = get_cancellation_event(update.effective_user.id, context.bot_data)
    event.set()
    await update.message.reply_text("🛑 Cancelando descarga...")


async def handle_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_runtime_state(context.bot_data)
    user_id = update.effective_user.id
    stats = context.bot_data["stats"]
    total_user = stats["by_user"].get(user_id, 0)
    user_platforms = stats["by_user_platform"].get(user_id, {})
    if user_platforms:
        top_platform, top_count = max(user_platforms.items(), key=lambda item: item[1])
        top_text = f"{top_platform} ({top_count})"
    else:
        top_text = "Sin datos aún"

    await update.message.reply_text(
        f"📊 Tus estadísticas\n\nDescargas totales: {total_user}\nPlataforma favorita: {top_text}"
    )


async def handle_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_ID is None or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ No tienes permisos.")
        return

    ensure_runtime_state(context.bot_data)
    stats = context.bot_data["stats"]
    top_users = sorted(stats["by_user"].items(), key=lambda item: item[1], reverse=True)[:5]
    top_platforms = sorted(stats["by_platform"].items(), key=lambda item: item[1], reverse=True)

    top_users_text = "\n".join(f"• {uid}: {count}" for uid, count in top_users) or "• Sin datos"
    top_platforms_text = "\n".join(f"• {platform}: {count}" for platform, count in top_platforms) or "• Sin datos"

    last_days = []
    for days_ago in range(6, -1, -1):
        day = (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        last_days.append(f"• {day}: {stats['by_day'].get(day, 0)}")

    await update.message.reply_text(
        "🛠 *Panel admin*\n\n"
        f"Total descargas: {stats['total']}\n\n"
        f"*Top 5 usuarios*\n{top_users_text}\n\n"
        f"*Plataformas*\n{top_platforms_text}\n\n"
        f"*Últimos 7 días*\n{chr(10).join(last_days)}",
        parse_mode="Markdown"
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
            "thumbnail": info.get("thumbnail"),
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
            if result in {"success", "cached"}:
                await query.delete_message()
        finally:
            lock.release()
    except Exception as e:
        logger.error(f"Download error: {e}")
        await query.edit_message_text("❌ Ocurrió un error durante la descarga.")


async def handle_subtitles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, url_key = query.data.split(":", 1)
    entry = context.bot_data.get(url_key)
    if not entry:
        await query.edit_message_text("❌ URL expirada. Envía el enlace de nuevo.")
        return

    lock = await try_acquire_user_lock(query.from_user.id, context.bot_data)
    if lock is None:
        await query.message.reply_text("⏳ Ya hay una descarga en curso. Espera un momento...")
        return

    status_message = await query.message.reply_text("📝 Buscando subtítulos...")
    workdir = make_work_dir()
    try:
        loop = asyncio.get_running_loop()
        subtitles = await loop.run_in_executor(None, download_subtitles, entry["url"], workdir)
        if not subtitles:
            await safe_edit_text(status_message, "❌ Este video no tiene subtítulos disponibles.")
            return

        for subtitle_path in subtitles:
            with open(subtitle_path, "rb") as subtitle_file:
                await query.message.reply_document(document=subtitle_file)
        await safe_edit_text(status_message, "✅ Subtítulos enviados.")
    except Exception as e:
        logger.error(f"Subtitle download error: {e}")
        await safe_edit_text(status_message, "❌ No pude descargar los subtítulos.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        lock.release()


async def handle_thumbnail_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, url_key = query.data.split(":", 1)
    entry = context.bot_data.get(url_key)
    if not entry:
        await query.edit_message_text("❌ URL expirada. Envía el enlace de nuevo.")
        return

    thumbnail_url = entry.get("thumbnail")
    if not thumbnail_url:
        await query.message.reply_text("❌ Este video no tiene miniatura disponible.")
        return

    status_message = await query.message.reply_text("🖼 Descargando miniatura...")
    try:
        loop = asyncio.get_running_loop()
        image_bytes = await loop.run_in_executor(None, fetch_remote_bytes, thumbnail_url)
        image_file = io.BytesIO(image_bytes)
        image_file.name = "thumbnail.jpg"
        await query.message.reply_photo(photo=image_file)
        await safe_edit_text(status_message, "✅ Portada enviada.")
    except Exception as e:
        logger.error(f"Thumbnail download error: {e}")
        await safe_edit_text(status_message, "❌ No pude descargar la miniatura.")


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
            if result in {"success", "cached"}:
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
                cancel_event = get_cancellation_event(query.from_user.id, context.bot_data)
                if cancel_event.is_set():
                    await safe_edit_text(query.message, "🛑 Descarga de playlist cancelada.")
                    return

                item_url = item.get("webpage_url") or item.get("url")
                if not item_url:
                    continue

                item_title = item.get("title") or f"{playlist_title} #{position}"
                item_url_key = hashlib.md5(item_url.encode()).hexdigest()[:12]
                context.bot_data[item_url_key] = {
                    "url": item_url,
                    "title": item_title,
                    "thumbnail": item.get("thumbnail"),
                    "formats": PLAYLIST_FORMATS,
                    "is_playlist": False,
                }
                await safe_edit_text(query.message, f"⬇️ Descargando video {position}/{total}...")
                result = await perform_download(
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
                if result == "cancelled":
                    await safe_edit_text(query.message, "🛑 Descarga de playlist cancelada.")
                    return
                if result not in {"success", "cached"}:
                    return

            await safe_edit_text(query.message, "✅ Descarga de playlist completada.")
        finally:
            lock.release()
    except Exception as e:
        logger.error(f"Playlist download error: {e}")
        await query.edit_message_text("❌ Ocurrió un error al descargar la playlist.")


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
    cancel_event = get_cancellation_event(user_id, context.bot_data)
    cancel_event.clear()

    if fmt["ext"] != "mp3" and not is_trimmed:
        cached_file_id = context.bot_data["video_cache"].get(cache_key)
        if cached_file_id:
            await safe_edit_text(status_message, "📦 Usando caché...")
            await reply_target.reply_video(video=cached_file_id)
            add_history_entry(context.bot_data, user_id, title, url_key, fmt_idx, fmt["label"])
            record_stat(context.bot_data, user_id, url, fmt["label"])
            return "cached"

    workdir = make_work_dir()
    loop = asyncio.get_running_loop()
    progress_queue = None
    progress_stop = None
    progress_task = None

    async def stop_progress_worker():
        nonlocal progress_queue, progress_stop, progress_task
        if progress_task:
            progress_stop.set()
            await progress_queue.put(None)
            await progress_task
            progress_queue = None
            progress_stop = None
            progress_task = None

    try:
        ensure_not_cancelled(cancel_event)
        filepath = None
        for attempt in range(1, 4):
            ensure_not_cancelled(cancel_event)
            clear_workdir(workdir)
            progress_queue = asyncio.Queue()
            progress_stop = asyncio.Event()
            progress_task = asyncio.create_task(progress_message_worker(status_message, progress_queue, progress_stop))

            def progress_callback(percent, speed, eta):
                if progress_stop.is_set():
                    return
                DOWNLOAD_PROGRESS[workdir] = {"percent": percent, "speed": speed, "eta": eta}
                asyncio.run_coroutine_threadsafe(progress_queue.put((percent, speed, eta)), loop)

            if attempt > 1:
                await safe_edit_text(status_message, f"⚠️ Reintentando descarga (intento {attempt}/3)...")
            try:
                filepath = await loop.run_in_executor(None, download_video, url, fmt, workdir, progress_callback, cancel_event)
                await stop_progress_worker()
                break
            except UserCancelledError:
                await stop_progress_worker()
                raise asyncio.CancelledError
            except asyncio.CancelledError:
                await stop_progress_worker()
                raise
            except Exception as exc:
                await stop_progress_worker()
                logger.warning(f"Download attempt {attempt}/3 failed: {exc}")
                if attempt == 3 or not is_retryable_download_error(exc):
                    raise
                await safe_edit_text(status_message, f"⚠️ Reintentando descarga (intento {attempt + 1}/3)...")
                await asyncio.sleep(2 ** attempt)

        ensure_not_cancelled(cancel_event)
        if not filepath or not os.path.exists(filepath):
            await safe_edit_text(status_message, "❌ Error al descargar el video.")
            return "error"

        logger.info(f"Downloaded file: {filepath}, size: {os.path.getsize(filepath)}")

        if trim_range:
            if fmt["ext"] == "mp3":
                await safe_edit_text(status_message, "❌ El recorte solo está disponible para video.")
                return "error"
            ensure_not_cancelled(cancel_event)
            await safe_edit_text(status_message, "✂️ Recortando video...")
            filepath = await loop.run_in_executor(None, trim_video, filepath, trim_range[0], trim_range[1])
            ensure_not_cancelled(cancel_event)

        with open(filepath, "rb") as media_file:
            if fmt["ext"] == "mp3":
                ensure_not_cancelled(cancel_event)
                await safe_edit_text(status_message, "📤 Enviando audio...")
                await reply_target.reply_audio(audio=media_file)
                add_history_entry(context.bot_data, user_id, title, url_key, fmt_idx, fmt["label"])
                record_stat(context.bot_data, user_id, url, fmt["label"])
                return "success"

        size_mb = os.path.getsize(filepath) / 1024 / 1024
        await safe_edit_text(
            status_message,
            f"🔍 *Analizando video* ({size_mb:.0f} MB)...",
            parse_mode="Markdown"
        )
        ensure_not_cancelled(cancel_event)
        max_bytes = 2000 * 1024 * 1024 if USE_LOCAL_API else 49 * 1024 * 1024
        analysis = await run_in_executor_cancellable(loop, cancel_event, analyze_video, filepath, max_bytes)
        ensure_not_cancelled(cancel_event)

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
        await safe_edit_text(status_message, status, parse_mode="Markdown")

        filepath = await run_in_executor_cancellable(loop, cancel_event, do_process_video, filepath, analysis)
        ensure_not_cancelled(cancel_event)

        await safe_edit_text(status_message, "📤 *Enviando video...*", parse_mode="Markdown")
        width, height = await loop.run_in_executor(None, get_video_dimensions, filepath)
        ensure_not_cancelled(cancel_event)
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
        record_stat(context.bot_data, user_id, url, fmt["label"])
        return "success"
    except asyncio.CancelledError:
        await stop_progress_worker()
        await safe_edit_text(status_message, "🛑 Descarga cancelada.")
        return "cancelled"
    except Exception as exc:
        await stop_progress_worker()
        logger.error(f"perform_download error: {exc}")
        await safe_edit_text(status_message, "❌ Ocurrió un error durante la descarga.")
        return "error"
    finally:
        DOWNLOAD_PROGRESS.pop(workdir, None)
        cancel_event.clear()
        shutil.rmtree(workdir, ignore_errors=True)


async def set_bot_commands(application):
    await application.bot.set_my_commands([
        BotCommand("start",   "Mensaje de bienvenida"),
        BotCommand("help",    "Ver todos los comandos y funciones"),
        BotCommand("history", "Tus últimas 10 descargas"),
        BotCommand("stats",   "Tus estadísticas de uso"),
        BotCommand("cancel",  "Cancelar la descarga en curso"),
        BotCommand("admin",   "Panel de administración (solo admin)"),
    ])


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN no está configurado en el archivo .env")

    request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=60,
        write_timeout=300,
        pool_timeout=30,
    )

    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .post_init(set_bot_commands)
    )
    if USE_LOCAL_API:
        builder = builder.base_url(LOCAL_API_URL).local_mode(True)
        logger.info("Usando servidor local de Telegram Bot API (sin límite de 50MB)")

    app = builder.build()
    ensure_runtime_state(app.bot_data)



    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("cancel", handle_cancel))
    app.add_handler(CommandHandler("stats", handle_stats))
    app.add_handler(CommandHandler("admin", handle_admin))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(handle_trim_menu, pattern=r"^trim_menu:"))
    app.add_handler(CallbackQueryHandler(handle_trim_format_selection, pattern=r"^trim:"))
    app.add_handler(CallbackQueryHandler(handle_subtitles, pattern=r"^subs:"))
    app.add_handler(CallbackQueryHandler(handle_thumbnail_download, pattern=r"^thumb:"))
    app.add_handler(CallbackQueryHandler(handle_history_redownload, pattern=r"^hist:"))
    app.add_handler(CallbackQueryHandler(handle_playlist_count, pattern=r"^plist:"))
    app.add_handler(CallbackQueryHandler(handle_playlist_fmt, pattern=r"^pfmt:"))
    app.add_handler(CallbackQueryHandler(handle_format_selection, pattern=r"^\d+\|"))

    logger.info("Bot iniciado...")
    app.run_polling()


if __name__ == "__main__":
    main()
