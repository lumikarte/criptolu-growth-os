# 🎙️ Podcast Pro AI Studio

Backend que convierte una grabación larga de podcast en clips verticales listos para
publicar (TikTok / Reels / Shorts). Marca **CriptoLú**.

Stack: **FastAPI** (Python). Proyecto en Linear: *CriptoLú Growth OS* → épicas FP/PP-MVP.

## Roadmap MVP

| Épica | Qué |
|---|---|
| **FP-MVP-01** Backend Base | API FastAPI que responde en localhost (`/`, `/health`) ✅ |
| **FP-MVP-02** Archivos | Subida y registro de archivos ✅ |
| **FP-MVP-03** Whisper | Transcripción con timestamps (Groq) ✅ |
| **PP-MVP-01** Detección | LLM elige los mejores momentos (Groq / Claude) ✅ |
| **PP-MVP-02** Clips | FFmpeg corta los segmentos en vertical 9:16 ✅ |
| **PP-MVP-03** Export | Clips a carpetas shorts/reels/tiktok + naming ✅ |
| **W-03** Repropósito | De 1 transcripción: carrusel + feed + hilo + captions por red (LLM) ✅ |
| **W-04** Voz de marca | Perfil de marca de 1 página inyectado en cada caption ✅ |
| **W-06** Aprobación | Gate human-in-the-loop: nada se publica sin OK explícito ✅ |
| **W-07** Medición | Alcance/engagement por pieza + Daily Brief (aprobadas sin retoque) ✅ |

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

### Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/upload` | Sube un archivo de audio/video y registra su metadata |
| GET | `/uploads` | Lista las subidas |
| GET | `/uploads/{id}` | Metadata de una subida |
| POST | `/uploads/{id}/transcribe` | Transcribe con Groq (`?language=es`) |
| GET | `/uploads/{id}/transcript` | Devuelve la transcripción (json) |
| POST | `/uploads/{id}/diarize` | Etiqueta quién habla; enriquece el transcript (`?engine=assemblyai\|pyannote`) |
| POST | `/uploads/{id}/detect` | Detecta mejores momentos (`?engine=groq\|claude&n_clips=5`) |
| GET | `/uploads/{id}/moments` | Devuelve los momentos detectados (json) |
| POST | `/uploads/{id}/clips` | Corta los momentos en clips verticales 9:16 con FFmpeg, con subtítulos quemados (`?subtitles=true\|false`) |
| GET | `/uploads/{id}/clips` | Devuelve el índice de clips cortados (json) |
| POST | `/uploads/{id}/export` | Exporta los clips a carpetas por plataforma (`?platforms=shorts,reels,tiktok`) |
| GET | `/uploads/{id}/export` | Devuelve el índice de exports (json) |
| POST | `/uploads/{id}/repurpose` | Genera el paquete multi-formato desde la transcripción (`?engine=groq\|claude`) |
| GET | `/uploads/{id}/repurpose` | Devuelve el paquete multi-formato (json) |
| GET | `/brand-voice` | Devuelve el perfil de voz de marca vigente (propio o default) |
| PUT | `/brand-voice` | Guarda/actualiza el perfil de voz de marca (`{"voice": "..."}`) |
| GET | `/uploads/{id}/approvals` | Estado del gate: piezas publicables + status + resumen |
| POST | `/uploads/{id}/approvals/{piece}` | Decidir sobre una pieza (`?decision=approve\|reject\|reset`) |
| POST | `/uploads/{id}/metrics/work` | Registrar minutos de trabajo manual del episodio (`?manual_minutes=`) |
| POST | `/uploads/{id}/metrics/pieces/{piece}` | Registrar alcance/engagement de una pieza (`?reach=&engagement=`) |
| GET | `/uploads/{id}/metrics` | Reporte de medición del episodio |
| GET | `/brief` | Daily Brief: agrega los episodios (`?date=YYYY-MM-DD`) |
| POST | `/uploads/{id}/process` | **Encola** el pipeline y devuelve **202** con `job_id` (async; `?force=true` rehace) |
| GET | `/jobs/{id}` | Estado y progreso por etapa de un job encolado |
| POST | `/uploads/{id}/distribute` | Publica en Postiz las piezas **aprobadas** (modo borrador; `?networks=`) |
| GET | `/uploads/{id}/distribution` | Índice de la última distribución |

> La transcripción usa **Groq** (whisper-large-v3). Requiere `GROQ_API_KEY` en `.env`
> (ver `.env.example`). Antes de subir, **FFmpeg downsamplea el audio a 16 kHz mono Opus**
> (~11 MB/h) para que un podcast largo entre en el tope de ~100 MB de la API (el source
> de hasta 2 GB se acepta; lo que viaja es el `.ogg` chico). Si el archivo no tiene pista
> de audio, devuelve 400.
>
> La detección de momentos es **enchufable**: `groq` (Llama 3.3 70B, por defecto, reusa
> `GROQ_API_KEY`) o `claude` (Anthropic, mejor criterio editorial, requiere
> `ANTHROPIC_API_KEY`). El SDK `anthropic` se importa de forma perezosa.
>
> El corte de clips (PP-MVP-02) usa **FFmpeg/ffprobe** (binarios del sistema). Si el
> source tiene video se recorta a 1080×1920; si es solo-audio se genera un waveform sobre
> fondo de marca para que el clip siga siendo un video posteable. Salida en
> `data/clips/<id>/clip_<n>.mp4` + `clips.json`.
>
> Los clips llevan **subtítulos quemados** (PP-MVP-04) re-chunkeados desde los
> word-timestamps del transcript en líneas cortas legibles, vía `subtitles=` + libass
> (fuente DejaVu Sans). Por defecto se queman si hay transcripción; `?subtitles=true` los
> exige (400 si falta), `?subtitles=false` los desactiva. El texto va siempre en un `.srt`
> temporal (nunca inline en el filtro).
>
> El export (PP-MVP-03) copia los clips a `data/exports/{shorts,reels,tiktok}/` con nombre
> `<idcorto>_<rank>_<slug-título>.mp4` (slug ASCII saneado). Es idempotente: al re-exportar
> limpia solo los archivos de ese upload. Índice en `data/clips/<id>/export.json`.
>
> `POST /process` (PP-MVP-05 + CRI-603) encadena las 4 etapas. Es **asíncrono**: encola un
> job y devuelve **202** con `job_id`; el trabajo pesado (FFmpeg + Groq/Claude, minutos)
> corre en un **worker aparte**, así ningún proxy corta la conexión. Seguí el avance con
> `GET /jobs/{id}` (status queued/started/finished/failed + progreso por etapa; los fallos
> quedan en `failed: {stage, message, upstream}`). Hace **resume** por defecto; `?force=true`
> rehace. 409 si ya hay un job activo para esa subida.
>
> **Correr el pipeline async (dev):** la cola usa Redis/Valkey.
> ```bash
> docker compose up -d redis                 # Valkey (fork BSD de Redis)
> REDIS_URL=redis://localhost:6379/0 rq worker podcast-pipeline   # worker, en otra terminal
> ```
> Sin infra: `JOBS_EAGER=1` corre el pipeline inline en el proceso del API (dev/test).

## Tests

```bash
pip install -r requirements-dev.txt
pytest            # FFmpeg se mockea: no se ejecuta nada real
```

## Estructura

```
app/
  main.py            # FastAPI app + lifespan
  config.py          # metadata + rutas de datos
  routers/
    health.py        # / y /health
    files.py         # upload/transcribe/detect/clips
  services/
    storage.py       # subidas + metadata
    transcription.py # Groq whisper-large-v3
    detection.py     # mejores momentos (Groq/Claude)
    clipping.py      # corte 9:16 con FFmpeg
    export.py        # export a carpetas por plataforma + naming
tests/               # suite pytest (ffmpeg mockeado)
data/                # gitignored
  uploads/  transcripts/  clips/
  exports/{shorts,reels,tiktok}/
```
