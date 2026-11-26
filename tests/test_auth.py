"""Password protection, sessions, API tokens and the login rate limit."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ppe_guard.auth import AuthManager, LoginLimiter, hash_password, verify_password
from ppe_guard.server import create_app

PW = "correct horse"


def test_password_hashing():
    h = hash_password(PW)
    assert h.startswith("scrypt$") and PW not in h
    assert verify_password(PW, h) and not verify_password("wrong", h)
    assert not verify_password(PW, "garbage") and not verify_password(PW, "md5$x$y$z$a$b")
    assert hash_password(PW) != h  # salted


def test_sessions_and_tokens(tmp_path):
    a = AuthManager(tmp_path, env_password="")
    assert not a.enabled and a.allowed(None, None, None)
    with pytest.raises(ValueError):
        a.set_password("short")
    a.set_password(PW)
    assert a.enabled and not a.allowed(None, None, None)
    cookie, max_age = a.issue_session(remember=True)
    assert max_age == 30 * 86400 and a.check_session(cookie)
    assert not a.check_session(cookie + "x") and not a.check_session("a.b") and not a.check_session("")
    assert not a.check_session(cookie, now=10**12)  # expired
    tok = a.create_token()
    assert a.check_token(tok) and not a.check_token(tok + "x") and a.allowed(None, None, tok)
    # a new password rotates the signing key: old sessions stop working
    a.set_password("another password")
    assert not a.check_session(cookie)
    # state survives a restart
    b = AuthManager(tmp_path, env_password="")
    assert b.check_password("another password") and b.check_token(tok)
    b.revoke_token()
    assert not b.check_token(tok)
    b.clear_password()
    assert not b.enabled


def test_env_password_wins(tmp_path):
    a = AuthManager(tmp_path, env_password="from env 123")
    assert a.from_env and a.check_password("from env 123")


def test_login_limiter():
    lim = LoginLimiter(attempts=3, window=60)
    for _ in range(3):
        assert not lim.blocked("ip", now=100)
        lim.fail("ip", now=100)
    assert lim.blocked("ip", now=101)
    assert not lim.blocked("ip", now=200)  # window passed
    lim.fail("ip", now=300)
    lim.reset("ip")
    assert not lim.blocked("ip", now=300)


@pytest.fixture
def locked(tmp_path):
    app = create_app(tmp_path, autostart=False, password=PW)
    with TestClient(app) as c:
        yield c


def test_everything_is_locked_without_login(locked):
    c = locked
    assert c.get("/api/health").status_code == 200
    assert c.get("/static/css/app.css").status_code == 200
    assert c.get("/login").status_code == 200
    for path in ("/api/settings", "/api/cameras", "/api/violations", "/api/stats", "/api/analytics",
                 "/snapshots/x.jpg", "/clips/x.mp4", "/api/analyze/jobs", "/openapi.json"):
        assert c.get(path).status_code == 401, path
    assert c.put("/api/settings", json={}).status_code == 401
    r = c.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    r = c.get("/docs", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.headers["location"] == "/login?next=%2Fdocs"
    with pytest.raises(WebSocketDisconnect), c.websocket_connect("/ws") as ws:
        ws.receive_text()
    assert c.get("/api/auth/status").json() == {"enabled": True, "authenticated": False,
                                                "token_set": False, "from_env": True}


def test_login_logout_and_rate_limit(locked):
    c = locked
    assert c.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    r = c.post("/api/auth/login", json={"password": PW})
    assert r.status_code == 200 and "ppe_session" in r.cookies
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    assert c.get("/api/settings").status_code == 200
    with c.websocket_connect("/ws"):
        pass
    c.post("/api/auth/logout")
    c.cookies.clear()
    assert c.get("/api/settings").status_code == 401
    for _ in range(10):
        c.post("/api/auth/login", json={"password": "nope"})
    assert c.post("/api/auth/login", json={"password": PW}).status_code == 429


def test_token_access_and_management(locked):
    c = locked
    assert c.post("/api/auth/token").status_code == 401
    c.post("/api/auth/login", json={"password": PW})
    tok = c.post("/api/auth/token").json()["token"]
    c.cookies.clear()
    assert c.get("/api/cameras", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert c.get(f"/api/cameras?token={tok}").status_code == 200
    assert c.put(f"/api/settings?token={tok}", json={}).status_code == 401  # query token: GET only
    # a token cannot create tokens or change the password
    assert c.post("/api/auth/token", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_change_and_clear_password(locked):
    c = locked
    c.post("/api/auth/login", json={"password": PW})
    assert c.post("/api/auth/password", json={"current": "bad", "new": "new password 1"}).status_code == 403
    assert c.post("/api/auth/password", json={"current": PW, "new": "short"}).status_code == 422
    assert c.post("/api/auth/password", json={"current": PW, "new": "new password 1"}).status_code == 200
    assert c.get("/api/settings").status_code == 200  # re-issued session still works
    assert c.post("/api/auth/password/clear", json={"current": PW}).status_code == 403
    assert c.post("/api/auth/password/clear", json={"current": "new password 1"}).status_code == 200
    c.cookies.clear()
    assert c.get("/api/settings").status_code == 200  # open again


def test_open_dashboard_can_set_a_password(tmp_path):
    with TestClient(create_app(tmp_path, autostart=False, password="")) as c:
        assert c.get("/api/auth/status").json()["enabled"] is False
        assert c.post("/api/auth/token").status_code == 409
        assert c.post("/api/auth/password", json={"new": "brand new pw"}).status_code == 200
        assert c.get("/api/settings").status_code == 200  # the response logged us in
        c.cookies.clear()
        assert c.get("/api/settings").status_code == 401
