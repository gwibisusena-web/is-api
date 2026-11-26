"""Audit logging untuk request API.

Log ditulis dengan Session terpisah dari session request agar:
- tidak ikut rollback() saat handler error membatalkan transaksi bisnis;
- kegagalan insert log tidak pernah menggagalkan respons API.
"""

import json
import time
import uuid

from flask import current_app, g, request
from sqlalchemy.orm import Session

from models import ApiLog, db

REDACTED = "***"
SENSITIVE_PARTS = (
    "password",
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "credential",
)


def _is_sensitive(key):
    key = str(key).lower()
    return any(part in key for part in SENSITIVE_PARTS)


def sanitize(value):
    """Saring field sensitif secara rekursif (dict/list) sebelum disimpan."""
    if isinstance(value, dict):
        return {
            str(k): REDACTED if _is_sensitive(k) else sanitize(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _truncate(text, max_bytes):
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore") + "... [truncated]"


def _prepare_body(raw, max_bytes):
    """Body JSON disaring; body non-JSON/invalid tidak disimpan apa adanya."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return f"<body tidak valid, {len(raw)} byte>"
    text = json.dumps(sanitize(parsed), ensure_ascii=False, separators=(",", ":"))
    return _truncate(text, max_bytes)


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:45]
    return (request.remote_addr or "")[:45]


def start_request():
    """before_request: jalankan sebelum authenticate agar 401 ikut tercatat."""
    g.request_id = str(uuid.uuid4())
    g.request_started = time.perf_counter()


def write_log(**fields):
    """Insert log dalam transaksi tersendiri."""
    session = Session(bind=db.engine)
    try:
        session.add(ApiLog(**fields))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def finish_request(response):
    """after_request: tulis satu baris log untuk setiap respons."""
    request_id = getattr(g, "request_id", None)
    if request_id:
        response.headers["X-Request-ID"] = request_id

    if not current_app.config.get("LOG_ENABLED", True) or request.endpoint == "static":
        return response

    started = getattr(g, "request_started", None)
    duration_ms = (
        int((time.perf_counter() - started) * 1000) if started is not None else 0
    )
    status = response.status_code
    level = "ERROR" if status >= 500 else "WARN" if status >= 400 else "INFO"
    max_bytes = int(current_app.config.get("LOG_MAX_BODY_BYTES", 8192))

    query_params = None
    if request.args:
        query_params = _truncate(
            json.dumps(sanitize(request.args.to_dict()), ensure_ascii=False,
                       separators=(",", ":")),
            max_bytes,
        )

    raw_request = request.get_data(cache=True)
    response_body = None
    if status >= 400:
        try:
            response_body = _prepare_body(response.get_data(), max_bytes)
        except Exception:
            response_body = "<response body tidak tersedia>"

    user = getattr(g, "user", None)
    fields = dict(
        request_id=request_id or "",
        method=request.method,
        path=request.path[:500],
        endpoint=request.endpoint,
        query_params=query_params,
        status_code=status,
        level=level,
        duration_ms=duration_ms,
        user_id=user.id if user else None,
        user_email=user.email if user else None,
        user_role=user.role if user else None,
        ip_address=_client_ip(),
        user_agent=(request.headers.get("User-Agent") or "")[:500],
        request_body=_prepare_body(raw_request, max_bytes),
        response_body=response_body,
        error_message=getattr(g, "log_error_message", None),
    )

    try:
        write_log(**fields)
    except Exception:
        current_app.logger.exception("Gagal menulis api_log untuk %s %s",
                                     request.method, request.path)
    return response
