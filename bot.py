import os
import re
import tempfile
import logging
import asyncio
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
URL_REGEX = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/watch\?v=|youtu\.be/|"
    r"facebook\.com/.+/videos/|fb\.watch/|fb\.com/|"
    r"instagram\.com/(p|reel|tv)/|"
    r"tiktok\.com/@.+/video/|vm\.tiktok\.com/)"
    r"[\w\-\?=&%/.]+"
)

FORMATS = [
    {"label": "🎬 1080p MP4", "format": "bestvideo[height<=1080][vcodec^=avc]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]", "ext": "mp4"},
    {"label": "🎬 720p MP4",  "format": "bestvideo[height<=720][vcodec^=avc]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]",   "ext": "mp4"},
    {"label": "🎬 480p MP4",  "format": "bestvideo[height<=480][vcodec^=avc]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]",   "ext": "mp4"},
    {"label": "🎬 360p MP4",  "format": "bestvideo[height<=360][vcodec^=avc]+bestaudio[ext=m4a]/bestvideo[height<=360]+bestaudio/best[height<=360]",   "ext": "mp4"},
    {"label": "🎵 MP3 Audio", "format": "bestaudio/best", "ext": "mp3"},
]


def is_valid_url(text: str) -> bool:
    return bool(URL_REGEX.search(text))


def get_video_title(url: str) -> str:
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get("title", "video")


def build_keyboard(url: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(f["label"], callback_data=f"{i}|{url}")]
        for i, f in enumerate(FORMATS)
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
        # Re-encode to H.264 if needed so Telegram can play it inline
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }]
        ydl_opts["postprocessor_args"] = {
            "ffmpegvideoconvertor": ["-vcodec", "libx264", "-acodec", "aac", "-preset", "fast"]
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
        await update.message.reply_text("❌ Eso no parece una URL de YouTube válida.")
        return

    msg = await update.message.reply_text("🔍 Obteniendo información del video...")
    try:
        title = await asyncio.get_event_loop().run_in_executor(None, get_video_title, url)
        await msg.edit_text(
            f"📹 *{title}*\n\nElige el formato de descarga:",
            reply_markup=build_keyboard(url),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error fetching info: {e}")
        await msg.edit_text("❌ No pude obtener información del video. Verifica el enlace.")


async def handle_format_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        idx, url = query.data.split("|", 1)
        fmt = FORMATS[int(idx)]

        await query.edit_message_text(f"⬇️ Descargando en {fmt['label']}... esto puede tardar un momento.")

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = await asyncio.get_event_loop().run_in_executor(
                None, download_video, url, fmt, tmpdir
            )
            if not filepath or not os.path.exists(filepath):
                await query.edit_message_text("❌ Error al descargar el video.")
                return

            logger.info(f"Downloaded file: {filepath}, size: {os.path.getsize(filepath)}")

            if os.path.getsize(filepath) > 50 * 1024 * 1024:
                await query.edit_message_text(
                    "⚠️ El archivo supera el límite de 50MB de Telegram.\n"
                    "Intenta con una calidad menor."
                )
                return

            await query.edit_message_text("📤 Enviando archivo...")
            with open(filepath, "rb") as f:
                if fmt["ext"] == "mp3":
                    await query.message.reply_audio(audio=f)
                else:
                    await query.message.reply_video(video=f)

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
