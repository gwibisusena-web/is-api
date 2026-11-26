"""Autentikasi & otorisasi.

- Password disimpan sebagai hash (Werkzeug, scrypt) — tidak pernah plaintext.
- Login menghasilkan JWT (HS256) berisi id user dan role.
- authenticate() dipasang sebagai before_request: semua endpoint wajib token,
  kecuali endpoint publik. Otorisasi berbasis method:
      viewer -> hanya GET/HEAD/OPTIONS
      admin  -> semua method
"""

from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import current_app, g, jsonify, request
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from werkzeug.security import check_password_hash, generate_password_hash

from models import User, db

# Endpoint yang boleh diakses tanpa token.
PUBLIC_ENDPOINTS = frozenset({"auth_login", "static"})

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

VALID_ROLES = ("admin", "viewer")


def admin_required(view):
    """Batasi endpoint hanya untuk role admin (guard utama hanya cek method)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = getattr(g, "user", None)
        if user is None or user.role != "admin":
            return jsonify(error="Hanya admin yang boleh mengakses log"), 403
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------
# Password
# ---------------------------------------------------------------
def hash_password(password: str) -> str:
    """Hash password dengan scrypt (default Werkzeug 3.x)."""
    return generate_password_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)


# ---------------------------------------------------------------
# JWT
# ---------------------------------------------------------------
def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        # PyJWT 2.10+ mewajibkan klaim `sub` berupa string.
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=current_app.config["JWT_EXPIRES_MINUTES"]),
    }
    return jwt.encode(
        payload,
        current_app.config["JWT_SECRET_KEY"],
        algorithm=current_app.config["JWT_ALGORITHM"],
    )


def decode_token(token: str) -> dict:
    return jwt.decode(
        token,
        current_app.config["JWT_SECRET_KEY"],
        algorithms=[current_app.config["JWT_ALGORITHM"]],
    )


# ---------------------------------------------------------------
# Guard
# ---------------------------------------------------------------
def authenticate():
    """before_request hook: validasi token + cek hak berdasarkan method."""
    # Endpoint tak dikenal (404) dibiarkan lewat agar handler 404 tetap jalan.
    if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
        return None

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return jsonify(
            error="Token tidak ada. Kirim header: Authorization: Bearer <token>"
        ), 401

    token = header[len("Bearer "):].strip()
    if not token:
        return jsonify(error="Token tidak ada"), 401

    try:
        payload = decode_token(token)
    except ExpiredSignatureError:
        return jsonify(error="Token kedaluwarsa, silakan login ulang"), 401
    except InvalidTokenError:
        return jsonify(error="Token tidak valid"), 401

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Token tidak valid"), 401

    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        return jsonify(error="User tidak aktif atau tidak ditemukan"), 401

    g.user = user

    if request.method not in SAFE_METHODS and user.role != "admin":
        return jsonify(error="Hanya admin yang boleh mengubah data"), 403

    return None
