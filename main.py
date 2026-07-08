"""
Middleware Stack: Rate-Limit + CORS + Request Context
------------------------------------------------------
Run with:  uvicorn main:app --host 0.0.0.0 --port 8000

Middleware execution order (outermost -> innermost):
  CORS  ->  RateLimit  ->  RequestContext  ->  route handler

This ordering matters:
  - CORS must be outermost so OPTIONS preflight is answered immediately,
    before it can ever be blocked by the rate limiter.
  - RateLimit runs before RequestContext assigns request.state.request_id
    for the *normal* path, but the 429 response still generates/echoes an
    X-Request-ID itself so the header contract holds even when throttled.
"""

import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# ---------------------------------------------------------------------------
# Config — values assigned to you
# ---------------------------------------------------------------------------

ALLOWED_ORIGINS = {
    "https://app-nl9bvo.example.com",
    # TODO: add the exam/grader page's exact origin here, e.g.:
    # "https://exam.example-platform.com",
}

RATE_LIMIT = 13          # B: max requests
WINDOW_SECONDS = 10      # per this many seconds

# TODO: replace with your actual logged-in email
EMAIL = "24f1000429@ds.study.iitm.ac.in"

app = FastAPI()

# client_id -> deque of request timestamps (monotonic clock)
_buckets: dict[str, deque] = defaultdict(deque)


def _prune(bucket: deque, now: float) -> None:
    while bucket and now - bucket[0] > WINDOW_SECONDS:
        bucket.popleft()


# ---------------------------------------------------------------------------
# Middleware 1: Request context (innermost)
# ---------------------------------------------------------------------------

class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ---------------------------------------------------------------------------
# Middleware 2: Per-client rate limiting
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Preflight requests are never rate-limited.
        if request.method == "OPTIONS":
            return await call_next(request)

        client_id = request.headers.get("X-Client-Id")

        if client_id:
            now = time.monotonic()
            bucket = _buckets[client_id]
            _prune(bucket, now)

            if len(bucket) >= RATE_LIMIT:
                request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded", "request_id": request_id},
                    headers={"X-Request-ID": request_id},
                )

            bucket.append(now)

        return await call_next(request)


# ---------------------------------------------------------------------------
# Middleware 3: Scoped CORS (outermost)
# ---------------------------------------------------------------------------

class ScopedCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")

        # Handle preflight directly.
        if request.method == "OPTIONS":
            response = JSONResponse(content={})
            if origin in ALLOWED_ORIGINS:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = (
                    "X-Request-ID, X-Client-Id, Content-Type"
                )
                response.headers["Vary"] = "Origin"
            return response

        response = await call_next(request)

        if origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        # No wildcard, and no header at all for disallowed origins.

        return response


# Starlette applies middleware in reverse order of `add_middleware` calls
# (last added wraps everything else, i.e. is outermost). We want:
#   ScopedCORSMiddleware (outermost)
#   -> RateLimitMiddleware
#   -> RequestContextMiddleware (innermost)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(ScopedCORSMiddleware)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@app.get("/ping")
async def ping(request: Request):
    return {"email": EMAIL, "request_id": request.state.request_id}
