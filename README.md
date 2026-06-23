# 🎙️ Podcast Pro AI Studio

Backend que convierte una grabación larga de podcast en clips verticales listos para
publicar (TikTok / Reels / Shorts). Marca **CriptoLú**.

Stack: **FastAPI** (Python). Proyecto en Linear: *CriptoLú Growth OS* → épicas FP/PP-MVP.

## Roadmap MVP

| Épica | Qué |
|---|---|
| **FP-MVP-01** Backend Base | API FastAPI que responde en localhost (`/`, `/health`) ✅ |
| FP-MVP-02 Archivos | Subida y registro de archivos |
| FP-MVP-03 Whisper | Transcripción con timestamps |
| PP-MVP-01 Detección | LLM elige los mejores momentos |
| PP-MVP-02 Clips | FFmpeg corta los segmentos |
| PP-MVP-03 Export | Carpetas shorts/reels/tiktok + naming |

## Correr en local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- Raíz:    http://127.0.0.1:8000/         → `{"name":"Podcast Pro AI Studio",...}`
- Health:  http://127.0.0.1:8000/health   → `{"status":"healthy"}`
- Docs:    http://127.0.0.1:8000/docs      (Swagger autogenerado)

## Estructura

```
app/
  main.py            # FastAPI app + lifespan
  config.py          # metadata + rutas de datos
  routers/
    health.py        # / y /health
data/                # gitignored
  uploads/  transcripts/  clips/
  exports/{shorts,reels,tiktok}/
```
