"""
Guards on the deployed configuration.

These are the checks that stop a bad deploy rather than a bad label. Each one
corresponds to a way this API could have been reachable by someone who should
not reach it, or unreachable by someone who should.

They manipulate module-level configuration, so each test reloads the modules it
touches and restores the environment afterwards.
"""
import importlib
import os

import pytest

import api_server
import auth_service
import db_service


@pytest.fixture
def prod(monkeypatch):
    """
    Reload the config modules as if running on AWS.

    AUTH_REQUIRED is set explicitly rather than left to default: api_server loads
    the developer's .env at import, and a local AUTH_REQUIRED=0 would otherwise
    make these tests pass for the wrong reason.
    """
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    importlib.reload(auth_service)
    yield auth_service
    monkeypatch.delenv("APP_ENV", raising=False)
    importlib.reload(auth_service)


# ── fail closed, never fail open ────────────────────────────────────────────
# enforced() returns False when the database is unreachable. That is right on a
# laptop and catastrophic in production: a missing DATABASE_URL or an RDS
# failover would have dropped authentication from every endpoint at once.

def test_production_without_a_database_refuses_requests(prod, monkeypatch):
    monkeypatch.setattr(prod, "is_available", lambda: False)
    assert prod.enforced() is False          # it genuinely cannot authenticate…
    assert prod.fail_closed() is True        # …so the request must be refused


def test_a_working_production_setup_does_not_fail_closed(prod, monkeypatch):
    monkeypatch.setattr(prod, "is_available", lambda: True)
    assert prod.fail_closed() is False


def test_local_development_without_a_database_stays_open(monkeypatch):
    """A developer with no Postgres must still get a usable API."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    mod = importlib.reload(auth_service)
    monkeypatch.setattr(mod, "is_available", lambda: False)
    assert mod.fail_closed() is False


# ── refuse to boot a deployment that cannot authenticate ────────────────────

def test_production_flags_a_missing_database(prod, monkeypatch):
    monkeypatch.setattr(prod.db_service, "is_configured", lambda: False)
    assert any("DATABASE_URL" in p for p in prod.startup_report())


def test_production_flags_auth_being_switched_off(prod, monkeypatch):
    monkeypatch.setattr(prod, "AUTH_REQUIRED", False)
    assert any("AUTH_REQUIRED" in p for p in prod.startup_report())


def test_production_flags_missing_cors_origins(prod, monkeypatch):
    """Not a security hole — the UI simply cannot call the API without it."""
    monkeypatch.setenv("CORS_ORIGINS", "")
    monkeypatch.setattr(prod.db_service, "is_configured", lambda: True)
    assert any("CORS_ORIGINS" in p for p in prod.startup_report())


def test_development_never_blocks_startup(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert importlib.reload(auth_service).startup_report() == []


# ── transport security ──────────────────────────────────────────────────────

def test_production_forces_tls_to_rds(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    url = db_service._require_tls("postgresql://u:p@db.abc.eu-west-1.rds.amazonaws.com:5432/x")
    assert url.endswith("?sslmode=require")


def test_an_explicit_sslmode_is_respected(monkeypatch):
    """Never downgrade someone who asked for stricter verification."""
    monkeypatch.setenv("APP_ENV", "production")
    url = "postgresql://u:p@host/x?sslmode=verify-full"
    assert db_service._require_tls(url) == url


def test_local_postgres_is_not_forced_to_use_tls(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    url = "postgresql://u:p@localhost:5432/x"
    assert db_service._require_tls(url) == url


def test_development_is_left_alone(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    url = "postgresql://u:p@host/x"
    assert db_service._require_tls(url) == url


# ── CORS ────────────────────────────────────────────────────────────────────

def test_cors_does_not_send_credentials():
    """
    Sessions travel in the Authorization header. Allowing credentialed
    cross-origin requests would let a mis-listed origin ride a browser session.
    """
    cors = _cors_middleware()
    assert cors.kwargs["allow_credentials"] is False


def test_cors_does_not_accept_arbitrary_headers_or_verbs():
    cors = _cors_middleware()
    assert set(cors.kwargs["allow_methods"]) <= {"GET", "POST", "OPTIONS"}
    assert "*" not in cors.kwargs["allow_headers"]


def _cors_middleware():
    from fastapi.middleware.cors import CORSMiddleware
    found = [m for m in api_server.app.user_middleware if m.cls is CORSMiddleware]
    assert found, "CORS middleware is not installed"
    return found[0]


# ── every route that touches data is authenticated ──────────────────────────

#: Open by design. /api/health is the load-balancer probe and says nothing about
#: the deployment; the auth routes are how a caller obtains a session at all.
PUBLIC_ROUTES = {
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/google",
    "/api/auth/logout",
}


def test_no_route_is_accidentally_public():
    """
    A new endpoint added without Depends(current_user) is invisible in review
    and fully public in production. This makes it a failing test instead.
    """
    unguarded = []
    for route in api_server.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api") or path in PUBLIC_ROUTES:
            continue
        params = getattr(getattr(route, "dependant", None), "dependencies", [])
        names = {getattr(d.call, "__name__", "") for d in params}
        if "current_user" not in names:
            unguarded.append(f"{sorted(route.methods)} {path}")
    assert not unguarded, f"Unauthenticated API routes: {unguarded}"


def test_the_public_list_has_not_quietly_grown():
    """Adding a route to PUBLIC_ROUTES should be a deliberate, reviewed act."""
    assert len(PUBLIC_ROUTES) == 5
