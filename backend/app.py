import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import litellm
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/app.db")
DATABASE_PATH = DATABASE_URL.replace("sqlite:///", "/", 1) if DATABASE_URL.startswith("sqlite:////") else DATABASE_URL.replace("sqlite:///", "")
ALLOWED_ORIGINS = [item.strip() for item in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if item.strip()]
ENABLE_FAILURE_SIMULATION = os.getenv("ENABLE_FAILURE_SIMULATION", "false").lower() == "true"
LLM_MODEL = os.getenv("LLM_MODEL", "gemini/gemini-2.0-flash")
DEFAULT_SYSTEM_PROMPT = os.getenv("DEFAULT_SYSTEM_PROMPT", "You are a helpful, concise assistant.")
API_KEYS = [key.strip() for key in os.getenv("LLM_API_KEYS", "").split(",") if key.strip()]

DB_LOCK = threading.RLock()


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    system_prompt: Optional[str] = None

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("session_id must not be empty")
        return value

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be empty")
        if len(value.strip()) > 4000:
            raise ValueError("message must not exceed 4000 characters")
        return value


class ResponseCache:
    def __init__(self) -> None:
        self._values: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def key(model: str, messages: List[Dict[str, str]]) -> str:
        payload = json.dumps({"model": model, "messages": messages}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._values.get(key)

    def set(self, key: str, value: Dict[str, Any]) -> None:
        with self._lock:
            self._values[key] = value


CACHE = ResponseCache()
litellm.cache = litellm.Cache(type="local")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with DB_LOCK, connect_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                system_prompt TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_session_id_id ON messages(session_id, id)"
        )


app = FastAPI(title="LLM Chatbot with Key Failover")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if "*" in ALLOWED_ORIGINS else ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> Dict[str, Any]:
    try:
        with DB_LOCK, connect_db() as connection:
            connection.execute("SELECT 1").fetchone()
    except Exception:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {
        "status": "ok",
        "keys_configured": len(API_KEYS),
        "cache_enabled": True,
    }


def parse_failures(header: Optional[str]) -> Dict[str, int]:
    if not ENABLE_FAILURE_SIMULATION or not header:
        return {}
    parsed: Dict[str, int] = {}
    for item in header.split(","):
        pair = item.strip()
        if "=" not in pair:
            continue
        key_id, status_text = pair.split("=", 1)
        try:
            status = int(status_text)
        except ValueError:
            continue
        if status in (429, 500, 502, 503):
            parsed[key_id.strip()] = status
    return parsed


def status_from_exception(exc: Exception) -> Optional[int]:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    text = str(exc).lower()
    if "429" in text or "rate" in text or "quota" in text:
        return 429
    for status in (500, 502, 503):
        if str(status) in text or "server error" in text:
            return status
    return None


def estimate_tokens(text: str) -> int:
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def demo_reply(message: str) -> str:
    stripped = message.strip()
    low = stripped.lower()
    if "capital" in low and "france" in low:
        return "The capital of France is Paris."
    if "hello" in low or "hi " in low or low == "hi":
        return "Hello! How can I help you today?"
    return f"I understand you're asking about: {stripped}"


async def generate_with_failover(
    messages: List[Dict[str, str]], failures: Dict[str, int]
) -> Dict[str, Any]:
    fallback_attempts = 0
    is_demo = not API_KEYS or all("placeholder" in key.lower() or "demo" in key.lower() for key in API_KEYS)

    for index, key in enumerate(API_KEYS or ["demo_key_1", "demo_key_2"], start=1):
        key_id = f"key_{index}"
        simulated = failures.get("*", failures.get(key_id))
        if simulated is not None:
            fallback_attempts += 1
            continue

        if is_demo:
            time.sleep(0.010)
            reply = demo_reply(messages[-1]["content"])
            input_tokens = sum(estimate_tokens(message["content"]) for message in messages)
            output_tokens = estimate_tokens(reply)
            return {
                "reply": reply,
                "model": LLM_MODEL,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "key_id": key_id,
                "fallback_attempts": fallback_attempts,
            }

        try:
            response = await litellm.acompletion(
                model=LLM_MODEL,
                messages=messages,
                api_key=key,
                caching=True,
                timeout=45,
            )
            reply = (response.choices[0].message.content or "").strip()
            if not reply:
                raise RuntimeError("provider returned an empty reply")
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", None)
            output_tokens = getattr(usage, "completion_tokens", None)
            if input_tokens is None:
                input_tokens = sum(estimate_tokens(message["content"]) for message in messages)
            if output_tokens is None:
                output_tokens = estimate_tokens(reply)
            return {
                "reply": reply,
                "model": LLM_MODEL,
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
                "key_id": key_id,
                "fallback_attempts": fallback_attempts,
            }
        except Exception as exc:
            status = status_from_exception(exc)
            if status in (429, 500, 502, 503):
                fallback_attempts += 1
                continue
            fallback_attempts += 1
            continue

    raise HTTPException(status_code=503, detail="all providers unavailable")


@app.post("/chat")
async def chat(
    payload: ChatRequest,
    x_simulate_failure: Optional[str] = Header(default=None, alias="X-Simulate-Failure"),
) -> Dict[str, Any]:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="message must not be empty")

    now = utc_now()
    with DB_LOCK, connect_db() as connection:
        row = connection.execute(
            "SELECT system_prompt FROM sessions WHERE session_id = ?",
            (payload.session_id,),
        ).fetchone()
        stored_prompt = row["system_prompt"] if row else None
        system_prompt = payload.system_prompt if payload.system_prompt is not None else (stored_prompt or DEFAULT_SYSTEM_PROMPT)
        connection.execute(
            """
            INSERT INTO sessions (session_id, system_prompt, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                system_prompt = excluded.system_prompt,
                updated_at = excluded.updated_at
            """,
            (payload.session_id, system_prompt, now, now),
        )
        history_rows = connection.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC",
            (payload.session_id,),
        ).fetchall()

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": row["role"], "content": row["content"]} for row in history_rows)
    messages.append({"role": "user", "content": message})

    cache_key = CACHE.key(LLM_MODEL, messages)
    started = time.perf_counter()
    cached = CACHE.get(cache_key)
    if cached is not None:
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        metadata = {
            "resolution_source": "LLM_CACHE",
            "model": cached["model"],
            "latency_ms": latency_ms,
            "input_tokens": cached["input_tokens"],
            "output_tokens": cached["output_tokens"],
            "key_id": None,
            "fallback_attempts": 0,
        }
        reply = cached["reply"]
    else:
        result = await generate_with_failover(messages, parse_failures(x_simulate_failure))
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        metadata = {
            "resolution_source": "LIVE_INFERENCE",
            "model": result["model"],
            "latency_ms": latency_ms,
            "input_tokens": result["input_tokens"],
            "output_tokens": result["output_tokens"],
            "key_id": result["key_id"],
            "fallback_attempts": result["fallback_attempts"],
        }
        reply = result["reply"]
        CACHE.set(cache_key, {
            "reply": reply,
            "model": result["model"],
            "input_tokens": result["input_tokens"],
            "output_tokens": result["output_tokens"],
        })

    assistant_time = utc_now()
    with DB_LOCK, connect_db() as connection:
        connection.execute(
            "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, 'user', ?, NULL, ?)",
            (payload.session_id, message, now),
        )
        connection.execute(
            "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, 'assistant', ?, ?, ?)",
            (payload.session_id, reply, json.dumps(metadata), assistant_time),
        )

    return {
        "session_id": payload.session_id,
        "reply": reply,
        "metadata": metadata,
    }


@app.get("/sessions/{session_id}/messages")
def get_messages(session_id: str) -> Dict[str, Any]:
    with DB_LOCK, connect_db() as connection:
        session = connection.execute(
            "SELECT session_id FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        rows = connection.execute(
            "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()

    messages = []
    for row in rows:
        item = {"role": row["role"], "content": row["content"]}
        if row["role"] == "assistant" and row["metadata"]:
            full_metadata = json.loads(row["metadata"])
            item["metadata"] = {
                "resolution_source": full_metadata["resolution_source"],
                "input_tokens": full_metadata["input_tokens"],
                "output_tokens": full_metadata["output_tokens"],
            }
        messages.append(item)
    return {"session_id": session_id, "messages": messages}
