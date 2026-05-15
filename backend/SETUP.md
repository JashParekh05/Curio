# Backend Setup

## One-time setup

```bash
cd backend
pip install -r requirements.txt
```

## YouTube cookies (required for audio downloads)

YouTube rate-limits unauthenticated requests. The pipeline uses cookies from a logged-in browser to bypass this.

1. In Chrome (logged into YouTube), install the extension **Get cookies.txt LOCALLY**
2. Visit `youtube.com`
3. Click the extension → **Export** → save the file as `backend/cookies.txt`

The pipeline auto-detects `cookies.txt` if present. To override:
```bash
export YOUTUBE_COOKIES_PATH=/path/to/cookies.txt
```

Cookies expire after a few months — re-export when audio downloads start failing.

## Whisper model

`faster-whisper` downloads the model weights on first run (cached to `~/.cache/huggingface/`). Default model is `base` (~150 MB, ~5x faster than realtime on M-series CPUs).

To use a different size:
```bash
export WHISPER_MODEL_SIZE=tiny    # smallest, fastest, less accurate
export WHISPER_MODEL_SIZE=base    # default (recommended)
export WHISPER_MODEL_SIZE=small   # better accuracy, slower
```

## Seed the curated content library

Run once after installing dependencies and creating `cookies.txt`:

```bash
python -m scripts.seed_clips
```

This processes ~20 hand-picked educational videos and inserts the resulting clips into Supabase. It's idempotent — safe to re-run; skips topics that already have clips.

To seed only specific topics:
```bash
python -m scripts.seed_clips neural-networks-basics binary-search
```

Total seeding time: ~15-30 minutes (depends on video lengths and Whisper speed).

## Run the API

```bash
uvicorn app.main:app --reload --port 8000
```

## How content is served

| Scenario | Behavior |
|---|---|
| User query matches a curated topic slug | Clips returned instantly from DB |
| User query is novel | Background pipeline runs: KA video search → audio download → Whisper → Groq segmentation. Clips appear in 30-90 seconds. |
