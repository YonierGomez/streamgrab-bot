# 📥 StreamGrab Bot

[![Docker Hub](https://img.shields.io/docker/pulls/yoniergomez/streamgrab?logo=docker&label=Docker%20Pulls)](https://hub.docker.com/r/yoniergomez/streamgrab)
[![Docker Image Size](https://img.shields.io/docker/image-size/yoniergomez/streamgrab/latest?logo=docker&label=Image%20Size)](https://hub.docker.com/r/yoniergomez/streamgrab)
[![GitHub Release](https://img.shields.io/github/v/release/YonierGomez/streamgrab-bot?logo=github&label=Release)](https://github.com/YonierGomez/streamgrab-bot/releases)
[![CI Status](https://img.shields.io/github/actions/workflow/status/YonierGomez/streamgrab-bot/docker-image.yml?logo=githubactions&label=CI)](https://github.com/YonierGomez/streamgrab-bot/actions)
[![GitHub Stars](https://img.shields.io/github/stars/YonierGomez/streamgrab-bot?style=flat&logo=github&label=Stars)](https://github.com/YonierGomez/streamgrab-bot/stargazers)
[![GitHub License](https://img.shields.io/github/license/YonierGomez/streamgrab-bot?logo=opensourceinitiative&label=License)](https://github.com/YonierGomez/streamgrab-bot/blob/main/LICENSE)

### Tecnologías

![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-26A5E4?logo=telegram&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-2088FF?logo=githubactions&logoColor=white)
![ffmpeg](https://img.shields.io/badge/ffmpeg-007808?logo=ffmpeg&logoColor=white)
![ARM](https://img.shields.io/badge/ARM-0091BD?logo=arm&logoColor=white)
![x86-64](https://img.shields.io/badge/x86--64-blue)

> Bot de Telegram que descarga videos de YouTube, Facebook, Instagram, TikTok, Twitter/X, Vimeo, Reddit, Twitch, SoundCloud, Dailymotion, Rumble, Streamable, Bilibili, Pinterest, LinkedIn, Snapchat, Likee, Tumblr, Kick, Triller y más en múltiples formatos con solo pegar la URL.

---

## ✨ Plataformas soportadas

| Plataforma | Tipos de contenido |
|---|---|
| ▶️ YouTube | Videos, Shorts, Playlists |
| 🎵 YouTube Music | Canciones y álbumes |
| 📘 Facebook | Videos públicos, Reels |
| 📸 Instagram | Posts, Reels, IGTV |
| 🎵 TikTok | Videos |
| 🐦 Twitter/X | Videos y GIFs |
| 📹 Vimeo | Videos |
| 👾 Reddit | Videos |
| 🎮 Twitch | Clips, VODs, streams |
| 🎵 SoundCloud | Tracks y playlists |
| 📺 Dailymotion | Videos |
| 🎬 Rumble | Videos |
| 📡 Streamable | Videos |
| 🎥 Bilibili | Videos |
| 📌 Pinterest | Videos |
| 💼 LinkedIn | Videos |
| 👻 Snapchat | Spotlight |
| 👍 Likee | Videos |
| 🎬 Tumblr | Videos |
| 🟣 Kick | Clips, VODs, streams |
| 🎵 Triller | Videos |

## 🎬 Formatos de descarga

Los formatos disponibles se detectan **automáticamente** según el video. El bot muestra solo las resoluciones que realmente existen:

| Formato | Descripción |
|---|---|
| 🎬 8K (4320p) | Si está disponible |
| 🎬 4K (2160p) | Si está disponible |
| 🎬 1440p | Si está disponible |
| 🎬 1080p | Full HD con audio |
| 🎬 720p | HD con audio |
| 🎬 480p | SD con audio |
| 🎬 360p | Baja calidad con audio |
| 🎬 240p | Mínima calidad |
| 🎵 MP3 Audio | Solo audio en 192kbps |

> ⚠️ Límite de Telegram: 50MB por archivo.

## 🔧 Post-procesamiento automático

Antes de enviar cada video, el bot aplica automáticamente con ffmpeg:

- **Codec H.264**: Re-encodifica VP9/AV1 a H.264 para compatibilidad con Telegram
- **Barras negras**: Detecta y recorta letterboxing/pillarboxing embebido (`cropdetect`)
- **SAR/PAR**: Corrige píxeles no cuadrados para evitar imágenes estiradas
- **Rotación**: Aplica metadatos de rotación correctamente

---

## 🚀 Inicio rápido

### Desde Docker Hub (recomendado)

```bash
docker run -d --name streamgrab \
  -e BOT_TOKEN=your_token_here \
  yoniergomez/streamgrab
```

### Con Docker Compose

```yaml
services:
  streamgrab:
    image: yoniergomez/streamgrab
    container_name: streamgrab
    restart: always
    environment:
      - BOT_TOKEN=your_token_here
      - ADMIN_ID=your_telegram_id  # opcional
```

### Desde el código fuente

```bash
# 1. Clonar el repositorio
git clone https://github.com/YonierGomez/streamgrab-bot.git
cd streamgrab-bot

# 2. Configurar el token
cp .env.example .env
# Edita .env con tu BOT_TOKEN

# 3. Construir y correr
docker build -t streamgrab-bot .
docker run -d --name streamgrab-bot --env-file .env streamgrab-bot
```

### Sin Docker

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edita .env con tu BOT_TOKEN
python downloader.py
```

---

## ⚙️ Requisitos

- Python 3.12+
- [ffmpeg](https://ffmpeg.org/download.html) instalado en el PATH
- Token de bot de Telegram ([@BotFather](https://t.me/BotFather))

## 🔧 Configuración

| Variable | Descripción | Requerido |
|---|---|---|
| `BOT_TOKEN` | Token del bot desde [@BotFather](https://t.me/BotFather) | ✅ |
| `ADMIN_ID` | Tu Telegram user ID para acceder a `/admin` | ❌ |

---

## 🐳 Docker Hub

```bash
# Pull
docker pull yoniergomez/streamgrab

# Run
docker run -d --name streamgrab -e BOT_TOKEN=tu_token yoniergomez/streamgrab

# Logs
docker logs -f streamgrab
```

### Arquitecturas soportadas

| Arquitectura | Dispositivos |
|---|---|
| linux/amd64 | PCs, servidores, VMs |
| linux/arm64 | Raspberry Pi 4/5, Apple Silicon, Orange Pi 5 |

---

## 📋 Comandos

| Comando | Descripción |
|---|---|
| `/start` | Mensaje de bienvenida |
| `/help` | Lista completa de funcionalidades |
| `/history` | Últimas 10 descargas con botones para repetir |
| `/stats` | Tus estadísticas de descarga |
| `/cancel` | Cancela la descarga en curso |
| `/admin` | Panel global de uso _(requiere `ADMIN_ID`)_ |

## 📖 Uso

1. Envía `/start` o `/help` para ver las opciones
2. Pega la URL del video
3. El bot muestra la miniatura y los formatos disponibles
4. Selecciona el formato — el bot informa el progreso en tiempo real
5. Recibe el archivo directamente en el chat

**Opciones adicionales al descargar:**
- 🖼 **Portada** — descarga solo la miniatura como imagen
- 📝 **Subtítulos** — descarga el `.srt` si está disponible
- ✂️ **Recortar** — elige un fragmento con formato `MM:SS MM:SS`

---

## 🛠️ Stack

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) 22.7
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [ffmpeg](https://ffmpeg.org/)
- Docker + Python 3.12 slim

## 🔗 Links

- [Docker Hub](https://hub.docker.com/r/yoniergomez/streamgrab)
- [GitHub](https://github.com/YonierGomez/streamgrab-bot)
- [Releases](https://github.com/YonierGomez/streamgrab-bot/releases)

## Apoya el proyecto

[![Buy Me A Coffee](https://img.shields.io/badge/Buy_Me_A_Coffee-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/yoniergomez)
[![GitHub Sponsors](https://img.shields.io/badge/GitHub_Sponsors-EA4AAA?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/YonierGomez)

## 📄 Licencia

MIT
