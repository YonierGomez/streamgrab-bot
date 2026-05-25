# 📥 StreamGrab Bot

![Python](https://img.shields.io/badge/python-3.12+-blue?style=flat-square)
![Docker](https://img.shields.io/badge/docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white)
![yt-dlp](https://img.shields.io/badge/yt--dlp-2026.3.17-red?style=flat-square)
![Telegram](https://img.shields.io/badge/telegram-bot-26A5E4?style=flat-square&logo=telegram&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

> Bot de Telegram que descarga videos de YouTube, Facebook, Instagram y TikTok en múltiples formatos con solo pegar la URL.

---

## ✨ Plataformas soportadas

| Plataforma | Tipos de contenido |
|---|---|
| ▶️ YouTube | Videos, Shorts |
| 📘 Facebook | Videos públicos |
| 📸 Instagram | Posts, Reels, IGTV |
| 🎵 TikTok | Videos |

## 🎬 Formatos de descarga

| Formato | Descripción |
|---|---|
| 🎬 1080p MP4 | Full HD con audio |
| 🎬 720p MP4 | HD con audio |
| 🎬 480p MP4 | SD con audio |
| 🎬 360p MP4 | Baja calidad con audio |
| 🎵 MP3 Audio | Solo audio en 192kbps |

> ⚠️ Límite de Telegram: 50MB por archivo.

---

## 🚀 Inicio rápido

### Con Docker (recomendado)

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
python bot.py
```

---

## ⚙️ Requisitos

- Python 3.12+
- [ffmpeg](https://ffmpeg.org/download.html) instalado en el PATH
- Token de bot de Telegram ([@BotFather](https://t.me/BotFather))
- Node.js (para yt-dlp-ejs, incluido en Docker)

## 🔧 Configuración

| Variable | Descripción |
|---|---|
| `BOT_TOKEN` | Token del bot obtenido desde [@BotFather](https://t.me/BotFather) |

---

## 🐳 Docker

```bash
# Build
docker build -t streamgrab-bot .

# Run
docker run -d --name streamgrab-bot --env-file .env streamgrab-bot

# Logs
docker logs -f streamgrab-bot

# Stop
docker rm -f streamgrab-bot
```

---

## 📖 Uso

1. Abre tu bot en Telegram y envía `/start`
2. Pega la URL del video
3. Selecciona el formato deseado
4. Recibe el archivo directamente en el chat

---

## 🛠️ Stack

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) 22.7
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) 2026.3.17
- [ffmpeg](https://ffmpeg.org/)
- Docker + Python 3.12 slim

## 📄 Licencia

MIT
