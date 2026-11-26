"""Optional password protection for the dashboard and API.

* With no password set, the dashboard is open (fine on ``127.0.0.1``).
* With a password, browsers log in and get a signed session cookie, and scripts can use an
  API token (``Authorization: Bearer <token>``, or ``?token=`` on GET requests such as the
  MJPEG stream).

Secrets live in ``<data dir>/auth.json``: an scrypt password hash, the cookie signing key
and a SHA-256 hash of the API token. Changing the password rotates the signing key, which
logs out every session.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path

COOKIE = "ppe_session"
SESSION_SECONDS = 12 * 3600
REMEMBER_SECONDS = 30 * 86400
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt, dk = encoded.split("$")
        if algo != "scrypt":
            return False
        test = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(_unb64(dk)))
        return hmac.compare_digest(test, _unb64(dk))
    except (ValueError, TypeError):
        return False


class LoginLimiter:
    """At most ``attempts`` failed logins per client in ``window`` seconds."""

    def __init__(self, attempts: int = 10, window: float = 600.0) -> None:
        self.attempts, self.window = attempts, window
        self._fails: dict[str, deque] = {}
        self._lock = threading.Lock()

    def blocked(self, client: str, now: float | None = None) -> bool:
        now = now or time.time()
        with self._lock:
            q = self._fails.get(client)
            while q and q[0] < now - self.window:
                q.popleft()
            return bool(q) and len(q) >= self.attempts

    def fail(self, client: str, now: float | None = None) -> None:
        with self._lock:
            self._fails.setdefault(client, deque()).append(now or time.time())

    def reset(self, client: str) -> None:
        with self._lock:
            self._fails.pop(client, None)


class AuthManager:
    def __init__(self, data_dir: Path, env_password: str | None = None) -> None:
        self.path = data_dir / "auth.json"
        self._lock = threading.Lock()
        self._data: dict = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._data = {}
        if not self._data.get("secret"):
            self._data["secret"] = _b64(secrets.token_bytes(32))
            self._save()
        env_password = env_password if env_password is not None else os.environ.get("PPE_GUARD_PASSWORD")
        self.from_env = bool(env_password)
        if env_password and not self.check_password(env_password):
            self.set_password(env_password)
        self.limiter = LoginLimiter()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    # ---- password -----------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self._data.get("password"))

    @property
    def token_set(self) -> bool:
        return bool(self._data.get("token"))

    def check_password(self, password: str) -> bool:
        h = self._data.get("password")
        return bool(h) and verify_password(password, h)

    def set_password(self, password: str) -> None:
        if len(password) < 8:
            raise ValueError("use at least 8 characters")
        with self._lock:
            self._data["password"] = hash_password(password)
            self._data["secret"] = _b64(secrets.token_bytes(32))  # log out existing sessions
            self._save()

    def clear_password(self) -> None:
        with self._lock:
            self._data.pop("password", None)
            self._data.pop("token", None)
            self._data["secret"] = _b64(secrets.token_bytes(32))
            self._save()

    # ---- sessions -----------------------------------------------------------------------

    def _sign(self, payload: str) -> str:
        key = _unb64(self._data["secret"])
        return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())

    def issue_session(self, remember: bool = False, now: float | None = None) -> tuple[str, int]:
        max_age = REMEMBER_SECONDS if remember else SESSION_SECONDS
        exp = int((now or time.time()) + max_age)
        payload = f"{exp}.{secrets.token_hex(8)}"
        return f"{payload}.{self._sign(payload)}", max_age

    def check_session(self, cookie: str | None, now: float | None = None) -> bool:
        if not cookie:
            return False
        try:
            exp, nonce, sig = cookie.split(".")
            if int(exp) < (now or time.time()):
                return False
        except ValueError:
            return False
        return hmac.compare_digest(sig, self._sign(f"{exp}.{nonce}"))

    # ---- API token ----------------------------------------------------------------------

    def create_token(self) -> str:
        token = "ppe_" + secrets.token_urlsafe(24)
        with self._lock:
            self._data["token"] = hashlib.sha256(token.encode()).hexdigest()
            self._save()
        return token

    def revoke_token(self) -> None:
        with self._lock:
            self._data.pop("token", None)
            self._save()

    def check_token(self, token: str | None) -> bool:
        stored = self._data.get("token")
        if not stored or not token:
            return False
        return hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), stored)

    # ---- request check ------------------------------------------------------------------

    def allowed(self, cookie: str | None, bearer: str | None, query_token: str | None) -> bool:
        if not self.enabled:
            return True
        return self.check_session(cookie) or self.check_token(bearer) or self.check_token(query_token)
