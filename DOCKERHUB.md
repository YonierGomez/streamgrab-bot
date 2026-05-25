## 🤖 StreamGrab Bot

Bot de Telegram para descargar videos de YouTube, Facebook, Instagram, TikTok, Twitter/X, Vimeo, Reddit y Twitch.

## 🚀 Inicio rápido

```bash
docker run -d --name streamgrab \
  -e BOT_TOKEN=your_token_here \
  yoniergomez/streamgrab
```

Con Docker Compose:

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

## 📦 Registros disponibles

| Registro | Imagen |
|---|---|
| Docker Hub | `docker pull yoniergomez/streamgrab` |
| GitHub (GHCR) | `docker pull ghcr.io/yoniergomez/streamgrab` |

## 🏗 Arquitecturas soportadas

| Arquitectura | Dispositivos |
|---|---|
| `linux/amd64` | PCs, servidores, VMs |
| `linux/arm64` | Raspberry Pi 4/5, Orange Pi 5, Apple Silicon |

## ✨ Funcionalidades

- 🎬 Descarga en múltiples calidades (240p → 8K)
- 🖼 Preview de miniatura antes de descargar
- 📝 Extracción de subtítulos (.srt)
- ✂️ Recorte de fragmentos (`MM:SS MM:SS`)
- 🎵 Extracción de audio en MP3
- ⬇️ Progreso de descarga en tiempo real
- 📋 Historial de descargas (`/history`)
- 🔄 Reintentos automáticos ante fallos
- 📊 Estadísticas de uso (`/stats`)

## ⚙️ Variables de entorno

| Variable | Descripción | Requerido |
|---|---|---|
| `BOT_TOKEN` | Token de [@BotFather](https://t.me/BotFather) | ✅ |
| `ADMIN_ID` | Tu Telegram user ID para `/admin` | ❌ |

## 🔗 Links

- [GitHub](https://github.com/YonierGomez/streamgrab-bot)
- [Releases](https://github.com/YonierGomez/streamgrab-bot/releases)
