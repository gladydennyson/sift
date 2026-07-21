"""
Sift API - FastAPI backend.

Endpoints:
  POST /interpret  - free-text requirements -> domain, rubric, candidate subreddits
  POST /score      - finalized subreddits + domain/rubric -> ranked results

Run with:
  uvicorn main:app --reload --port 8000
"""

import os
import re
import time
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from redis.exceptions import RedisError

from core import (
    get_client,
    interpret_requirements,
    fetch_reddit,
    normalize_subreddit,
    score_post,
)
from scan_store import create_scan, get_redis, load_scan, public_scan

app = FastAPI(title="Sift API")

allowed_origins = [
    origin.strip()
    for origin in os.environ.get(
        "SIFT_ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _client():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="DEEPSEEK_API_KEY is not set on the server.")
    return get_client(api_key)


class InterpretRequest(BaseModel):
    user_text: str = Field(min_length=1, max_length=5000)


class InterpretResponse(BaseModel):
    domain: str
    rubric: str
    subreddits: list[str]


class ScoreRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=3000)
    rubric: str = Field(min_length=1, max_length=5000)
    subreddits: list[str] = Field(min_length=1, max_length=5)
    post_limit_per_community: int = Field(default=5, ge=1, le=5)

    @field_validator("subreddits")
    @classmethod
    def normalize_subreddits(cls, values: list[str]) -> list[str]:
        normalized = []
        seen = set()
        for value in values:
            subreddit = normalize_subreddit(value)
            if not subreddit or not re.fullmatch(r"[A-Za-z0-9_]{2,21}", subreddit):
                raise ValueError(f"Invalid subreddit: {value!r}")
            key = subreddit.lower()
            if key not in seen:
                normalized.append(subreddit)
                seen.add(key)
        if not normalized:
            raise ValueError("At least one subreddit is required")
        return normalized


class ScoredPost(BaseModel):
    title: str
    url: str
    score: int
    reason: str
    community: str


class ScoreResponse(BaseModel):
    results: list[ScoredPost]
    warnings: list[str]


class ScanCreated(BaseModel):
    scan_id: str
    status: str


class ScanStatus(BaseModel):
    scan_id: str
    status: str
    total_posts: int
    processed_posts: int
    results: list[ScoredPost]
    warnings: list[str]
    error: str | None
    created_at: str
    updated_at: str


@app.post("/interpret", response_model=InterpretResponse)
def interpret(req: InterpretRequest):
    if not req.user_text.strip():
        raise HTTPException(status_code=400, detail="user_text cannot be empty.")
    client = _client()
    try:
        derived = interpret_requirements(client, req.user_text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to interpret requirements: {e}")
    return derived


@app.post("/score", response_model=ScoreResponse)
def score(req: ScoreRequest):
    client = _client()

    all_posts = []
    warnings = []
    for index, community in enumerate(req.subreddits):
        if index:
            time.sleep(1)
        posts, err = fetch_reddit(community, req.post_limit_per_community)
        if err:
            warnings.append(f"Could not read r/{community}: {err}")
        all_posts.extend(posts)

    # Reddit feeds can occasionally surface the same post through more than
    # one community listing. Score each URL only once.
    unique_posts = {}
    for post in all_posts:
        key = post.get("url") or f"{post['community']}:{post['title']}"
        unique_posts.setdefault(key, post)
    all_posts = list(unique_posts.values())

    # Keep concurrency deliberately small so scans finish promptly without
    # overwhelming the model provider or creating surprising costs.
    with ThreadPoolExecutor(max_workers=min(5, len(all_posts) or 1)) as executor:
        futures = {
            executor.submit(score_post, client, req.domain, req.rubric, post): post
            for post in all_posts
        }
        for future in as_completed(futures):
            post = futures[future]
            try:
                future.result()
            except Exception as exc:
                post["score"] = 0
                post["reason"] = f"error: {exc}"
                warnings.append(f"Could not score a post from r/{post['community']}")

    all_posts.sort(key=lambda p: p["score"], reverse=True)

    return {"results": all_posts, "warnings": warnings}


@app.post("/scans", response_model=ScanCreated, status_code=202)
def start_scan(req: ScoreRequest):
    """Queue slow scan work and return immediately instead of blocking HTTP."""
    try:
        redis_client = get_redis()
        redis_client.ping()
        scan = create_scan(redis_client, str(uuid4()), req.model_dump())
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Scan queue is unavailable.") from exc
    return {"scan_id": scan["scan_id"], "status": scan["status"]}


@app.get("/scans/{scan_id}", response_model=ScanStatus)
def get_scan(scan_id: str):
    """Return the latest Redis snapshot for frontend progress polling."""
    try:
        scan = load_scan(get_redis(), scan_id)
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Scan queue is unavailable.") from exc
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found or expired.")
    return public_scan(scan)


@app.get("/health")
def health():
    try:
        get_redis().ping()
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable.") from exc
    return {"status": "ok"}
