"""Authentication routes (email/password login + register)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

import bcrypt
import jwt
from app.database import get_db
from app.config import get_settings

router = APIRouter(tags=["auth"])

JWT_ALG = "HS256"
JWT_EXP = 60 * 60 * 24 * 7  # 7 days


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

from pydantic import BaseModel


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/auth/register")
async def register(body: RegisterRequest):
    db = await get_db()
    # Check duplicate
    cur = await db.execute("SELECT id FROM users WHERE email = ?", (body.email,))
    if (await cur.fetchone()):
        raise HTTPException(409, detail="Email already registered")

    pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()

    cur = await db.execute(
        "INSERT INTO users (email, password_hash, display_name) VALUES (?, ?, ?)",
        (body.email, pw_hash, body.display_name),
    )
    await db.commit()
    user_id = cur.lastrowid

    token = _make_token(user_id, settings=get_settings())
    return {"success": True, "data": {"user_id": user_id, "token": token}}


@router.post("/auth/login")
async def login(body: LoginRequest):
    db = await get_db()
    cur = await db.execute(
        "SELECT id, email, password_hash, display_name, premium_tier FROM users WHERE email = ?",
        (body.email,),
    )
    row = await cur.fetchone()
    if not row or not bcrypt.checkpw(body.password.encode(), row["password_hash"].encode()):
        raise HTTPException(401, detail="Invalid email or password")

    token = _make_token(row["id"], settings=get_settings())
    return {
        "success": True,
        "data": {
            "user_id": row["id"],
            "display_name": row["display_name"],
            "email": row["email"],
            "premium_tier": row["premium_tier"],
            "token": token,
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_token(user_id: int, settings) -> str:
    import time
    payload = {
        "sub": str(user_id),
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXP,
    }
    return jwt.encode(payload, settings.internal_api_token, algorithm=JWT_ALG)


def decode_token(token: str) -> dict | None:
    """Used by middleware to verify JWT. Returns payload or None."""
    try:
        return jwt.decode(token, get_settings().internal_api_token, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        return None


# ---------------------------------------------------------------------------
# User Settings
# ---------------------------------------------------------------------------


class SettingsRequest(BaseModel):
    language: str = "en"
    default_song_language: str = "auto"
    default_genre: str = "pop"
    default_vocal: str = "auto"
    default_privacy: str = "public"


@router.get("/settings")
async def get_settings_user():
    """获取当前用户设置。"""
    db = await get_db()
    cur = await db.execute("SELECT * FROM user_settings WHERE user_id = 1")
    row = await cur.fetchone()
    if not row:
        return {
            "language": "en",
            "default_song_language": "auto",
            "default_genre": "pop",
            "default_vocal": "auto",
            "default_privacy": "public",
        }
    return dict(row)


@router.put("/settings")
async def update_settings(body: SettingsRequest):
    """更新用户设置。"""
    db = await get_db()
    # Upsert
    cur = await db.execute("SELECT user_id FROM user_settings WHERE user_id = 1")
    existing = await cur.fetchone()
    if existing:
        await db.execute(
            """UPDATE user_settings SET language=?, default_song_language=?,
               default_genre=?, default_vocal=?, default_privacy=?,
               updated_at=datetime('now') WHERE user_id=1""",
            (body.language, body.default_song_language, body.default_genre,
             body.default_vocal, body.default_privacy),
        )
    else:
        await db.execute(
            "INSERT INTO user_settings (user_id, language, default_song_language, default_genre, default_vocal, default_privacy) VALUES (1,?,?,?,?,?)",
            (body.language, body.default_song_language, body.default_genre,
             body.default_vocal, body.default_privacy),
        )
    await db.commit()
    return {"success": True}