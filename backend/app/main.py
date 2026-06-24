import os
import logging
import threading
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.rate_limit import limiter
from dotenv import load_dotenv

load_dotenv()

# Suppress noisy httpx/supabase request logs — only show warnings+
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from app.api import topics, feed, users, analytics, quiz, game

app = FastAPI(title="LearnReel API", version="0.1.0")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — read exact origins from env (custom domains), AND allow a regex that
# covers every one of this project's Vercel deploys (prod + each preview/branch
# URL like curio-git-<branch>-<scope>.vercel.app) plus local dev. Vercel mints a
# new hostname per preview deploy, so without the regex a preflight from any URL
# other than the single one hardcoded in ALLOWED_ORIGINS is rejected with 400 and
# the browser blocks the request (the empty-feed symptom). The matched origin is
# reflected back (never "*"), so allow_credentials stays spec-compliant.
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "https://curio-eta.vercel.app")
_extra_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"(https://curio[a-z0-9-]*\.vercel\.app"
        r"|http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+):\d+)"
    ),
    allow_origins=_extra_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(topics.router)
app.include_router(feed.router)
app.include_router(users.router)
app.include_router(analytics.router)
app.include_router(quiz.router)
app.include_router(game.router)


@app.on_event("startup")
async def _warmup():
    """Preload the sentence-transformers model in a background thread so the first
    request doesn't block while ~80MB loads off disk."""
    def _load():
        from app.services.embeddings import get_model
        get_model()
    threading.Thread(target=_load, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "ok"}
