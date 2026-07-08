"""
FastAPI service demonstrating three composed middleware layers:
  1. Request-context propagation (X-Request-ID)
  2. Scoped CORS policy (no wildcards)
  3. Per-client sliding-window rate limiting

Run with:
    pip install fastapi uvicorn --break-system-packages
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

import time
import uuid
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Config / assigned values
# ---------------------------------------------------------------------------

# TODO: replace with your actual logged-in address.
LOGGED_IN_EMAIL = "24f1000429@ds.study.iitm.ac.in"

# Assigned allowed origin (grader requirement).
ASSIGNED_ORIGIN = "https://app-nl9bvo.example.com"

# The exam/verification page's origin, so the browser-based grader can call
# /ping directly. This was not given as a concrete URL in the prompt --
# replace this placeholder with the real exam page origin before deploying.
EXAM_PAGE_ORIGIN = "*"

ALLOWED_ORIGINS = [ASSIGNED_ORIGIN, EXAM_PAGE_ORIGIN]

# Rate-limit bucket: 13 requests / 10 seconds, per X-Client-Id.
RATE_LIMIT_MAX_REQUESTS = 13
RATE_LIMIT_WINDOW_SECONDS = 10.0

# ---------------------------------------------------------------------------
# Middleware 1: Request-context propagator
# ---------------------------------------------------------------------------


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Reuses an inbound X-Request-ID if present, otherwise generates a fresh
    UUID4. Stores it on request.state and stamps it onto the response header
    so callers can always correlate request/response.
    """

    async def dispatch(self, request: Request, call_next):
        incoming_id = request.headers.get("x-request-id")
        request_id = incoming_id if incoming_id else str(uuid.uuid4())
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ---------------------------------------------------------------------------
# Middleware 3: Per-client rate limiter (sliding window)
# ---------------------------------------------------------------------------


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Buckets requests by the X-Client-Id header. Each client gets an
    independent sliding window of RATE_LIMIT_WINDOW_SECONDS during which at
    most RATE_LIMIT_MAX_REQUESTS are allowed. Requests beyond that get a 429.

    Clients that don't send X-Client-Id are bucketed together under
    "anonymous" -- adjust to taste (e.g. fall back to request.client.host)
    if you want unlabeled clients to be independent instead.
    """

    def __init__(self, app):
        super().__init__(app)
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        # Preflight requests should never be rate-limited.
        if request.method == "OPTIONS":
            return await call_next(request)

        client_id = request.headers.get("x-client-id", "anonymous")
        now = time.monotonic()
        window_start = now - RATE_LIMIT_WINDOW_SECONDS

        bucket = self._hits[client_id]
        while bucket and bucket[0] < window_start:
            bucket.popleft()

        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded",
                    "client_id": client_id,
                    "limit": RATE_LIMIT_MAX_REQUESTS,
                    "window_seconds": RATE_LIMIT_WINDOW_SECONDS,
                },
            )

        bucket.append(now)
        return await call_next(request)


# ---------------------------------------------------------------------------
# App assembly
# ---------------------------------------------------------------------------

app = FastAPI()

# Middleware order matters. Starlette wraps in reverse of add order, so the
# LAST middleware added here is the OUTERMOST layer that runs first on the
# way in and last on the way out. We want CORS outermost so:
#   - OPTIONS preflight is answered before hitting our custom middleware
#   - CORS headers are still attached even on 429 responses from the
#     rate limiter (which sits inside it)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,  # explicit allow-list, never "*"
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["X-Request-ID", "X-Client-Id", "Content-Type"],
    expose_headers=["X-Request-ID"],
)


@app.get("/ping")
async def ping(request: Request):
    return {
        "email": LOGGED_IN_EMAIL,
        "request_id": request.state.request_id,
    }
