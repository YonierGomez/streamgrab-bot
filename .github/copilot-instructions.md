# StreamGrab Bot — Copilot Instructions

## Descripción del proyecto
Bot de Telegram (`bot.py`) que descarga videos de YouTube, Facebook, Instagram y TikTok usando `yt-dlp` y los envía como archivo. Corre en Docker.

## Stack
- **Python 3.12** + `python-telegram-bot` 22.x
- **yt-dlp** para extracción y descarga
- **ffmpeg / ffprobe** para post-procesamiento de video
- **Docker** (imagen `python:3.12-slim` con ffmpeg y nodejs)

## Arquitectura de bot.py

### Flujo principal
1. Usuario envía URL → `handle_url()` → `get_video_info()` detecta resoluciones disponibles → muestra teclado inline
2. Usuario elige formato → `handle_format_selection()` → `download_video()` → `normalize_video()` → `reply_video()`

### Almacenamiento de URLs
Las URLs se guardan en `context.bot_data` con un hash MD5 de 12 chars como clave (porque `callback_data` de Telegram tiene límite de 64 bytes). Nunca guardes la URL directamente en `callback_data`.

```python
url_key = hashlib.md5(url.encode()).hexdigest()[:12]
context.bot_data[url_key] = {"url": url, "formats": formats}
```

### Detección de formatos (get_video_info)
Consulta yt-dlp con `download=False`, extrae los `height` disponibles de `info["formats"]`, y construye la lista de opciones dinámicamente contra `RESOLUTION_PRESETS`. Siempre agrega MP3 al final.

### Post-procesamiento (normalize_video)
Orden de operaciones con ffmpeg:
1. `ffprobe` → detecta `codec_name`, `sample_aspect_ratio`, `rotate`
2. `ffmpeg cropdetect` → detecta barras negras (300 frames)
3. Si `codec != h264` → re-encodifica a `libx264 -crf 18 -preset fast`
4. Si SAR != 1:1 → aplica `scale=iw*sar:ih,setsar=1`
5. Si hay crop → aplica `crop=W:H:X:Y`
6. Si nada aplica → devuelve el filepath original sin tocar

### Descarga (download_video)
Usa `yt-dlp` con `merge_output_format: mp4`. Para MP3 usa `FFmpegExtractAudio`. Para video usa `FFmpegVideoRemuxer` con `-c copy` (el re-encodificado lo hace `normalize_video` después).

## Convenciones
- Toda operación bloqueante (yt-dlp, ffmpeg) se ejecuta en executor: `await loop.run_in_executor(None, func, args)`
- Los archivos temporales usan `tempfile.TemporaryDirectory()` (se limpian solos)
- Errores se loguean con `logger.error/warning` y se responde al usuario con mensaje amigable
- El tamaño máximo de archivo para Telegram es **50MB** (verificado antes de enviar)

## Build y despliegue
```bash
# Build
docker build -t yt-bot .

# Correr (requiere .env con BOT_TOKEN)
docker run -d --name yt-bot --env-file .env yt-bot

# Ver logs
docker logs -f yt-bot

# Rebuild y recrear (flujo habitual de desarrollo)
docker build -t yt-bot . && docker stop yt-bot && docker rm yt-bot && docker run -d --name yt-bot --env-file .env yt-bot
```

> ⚠️ Siempre hacer `docker build` antes de `docker restart` — el código está copiado en la imagen, no montado como volumen.

## Variables de entorno
| Variable | Descripción |
|---|---|
| `BOT_TOKEN` | Token del bot de Telegram ([@BotFather](https://t.me/BotFather)) |
