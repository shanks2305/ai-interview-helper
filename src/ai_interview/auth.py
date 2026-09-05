from __future__ import annotations

import os
import secrets
from typing import Any

LOOPBACK_HOSTS = {
    "127.0.0.1",
    "localhost",
    "::1",
    "ip6-localhost",
}
WILDCARD_HOSTS = {"0.0.0.0", "::", "[::]", "*"}
TOKEN_HEADER = "x-interview-token"
CLOSE_UNAUTHORIZED = 4401


def bind_host() -> str:
    return (os.getenv("AI_INTERVIEW_HOST") or "127.0.0.1").strip() or "127.0.0.1"


def access_token() -> str:
    return (os.getenv("AI_INTERVIEW_TOKEN") or "").strip()


def is_loopback_host(host: str | None) -> bool:
    raw = (host or "").strip().lower()
    if not raw:
        return True
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    if raw in WILDCARD_HOSTS:
        return False
    if raw in LOOPBACK_HOSTS:
        return True
    if raw.startswith("127."):
        return True
    if raw.startswith("::ffff:127."):
        return True
    return False


def lan_auth_enabled(host: str | None = None) -> bool:
    return not is_loopback_host(host if host is not None else bind_host())


def client_needs_token(client_host: str | None, *, bind: str | None = None) -> bool:
    if not lan_auth_enabled(bind):
        return False
    return not is_loopback_host(client_host)


def tokens_match(provided: str | None, expected: str | None) -> bool:
    got = (provided or "").encode()
    want = (expected or "").encode()
    if not want or len(got) != len(want):
        return False
    return secrets.compare_digest(got, want)


def provided_token(*, query: Any = None, headers: Any = None) -> str:
    token = ""
    if query is not None:
        try:
            token = str(query.get("token") or "")
        except Exception:
            token = ""
    if token.strip():
        return token.strip()
    if headers is None:
        return ""
    try:
        header = headers.get(TOKEN_HEADER) or headers.get("authorization") or ""
    except Exception:
        return ""
    value = str(header).strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


def is_authorized(
    *,
    client_host: str | None,
    provided: str | None = None,
    bind: str | None = None,
    expected: str | None = None,
) -> bool:
    if not client_needs_token(client_host, bind=bind):
        return True
    return tokens_match(provided, expected if expected is not None else access_token())


def http_path_requires_token(path: str) -> bool:
    return path.startswith("/api/")


def ensure_access_token(host: str | None = None) -> str | None:
    """Require (or mint) a token when binding beyond loopback. Returns the token or None."""
    bind = host if host is not None else bind_host()
    if not lan_auth_enabled(bind):
        return None
    token = access_token()
    if not token:
        token = secrets.token_urlsafe(18)
        os.environ["AI_INTERVIEW_TOKEN"] = token
    return token


def websocket_authorized(websocket: Any) -> bool:
    client = getattr(websocket, "client", None)
    host = getattr(client, "host", "") if client is not None else ""
    query = getattr(websocket, "query_params", None)
    headers = getattr(websocket, "headers", None)
    return is_authorized(client_host=host, provided=provided_token(query=query, headers=headers))
