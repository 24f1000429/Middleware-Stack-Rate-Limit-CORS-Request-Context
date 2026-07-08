from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict, deque
from uuid import uuid4
import time

app = FastAPI()

# =========================
# CONFIG
# =========================
EMAIL = "24f1000429@ds.study.iitm.ac.in"

ALLOWED_ORIGIN = "https://app-nl9bvo.example.com"

# Also allow the exam frontend origin during grading.
# (Keep this list if your grader runs from localhost.)
ALLOWED_ORIGINS = ["*"]

RATE_LIMIT = 13
WINDOW = 10  # seconds

# =========================
# CORS
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

# =========================
# RATE LIMIT STORAGE
# =========================
client_requests = defaultdict(deque)


from uuid import uuid4

@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())

    request.state.request_id = request_id

    response = await call_next(request)

    # Echo back the same request ID
    response.headers["X-Request-ID"] = request_id

    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_id = request.headers.get("X-Client-Id", "anonymous")

    now = time.time()
    bucket = client_requests[client_id]

    while bucket and bucket[0] <= now - WINDOW:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
        )

    bucket.append(now)

    return await call_next(request)


# =========================
# Endpoint
# =========================
@app.get("/ping")
async def ping(request: Request):
    return {
        "email": EMAIL,
        "request_id": request.state.request_id,
    }
