"""
auth_service.py  —  Accounts and server-side sessions

Design decisions worth knowing:

* **Argon2id** for passwords (the `argon2-cffi` package). Plaintext passwords
  are never stored, logged, or returned — they exist only inside `verify()`.
* **Server-side sessions, not self-contained tokens.** The bearer token is
  random; only its SHA-256 is stored. That means a database leak yields no
  usable session, and signing out revokes access immediately, which a stateless
  JWT cannot do without extra machinery.
* **Bearer tokens rather than cookies.** The UI and API are on different origins
  in production (Amplify + App Runner), so a same-site cookie will not travel.
  The trade-off is that the token lives in browser storage and is therefore
  reachable by XSS — acceptable for an internal tool, and revocable server-side.

Everything degrades gracefully: with no DATABASE_URL, ``is_available()`` is
False and the API runs unauthenticated exactly as before, so local development
does not require a database.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import db_service

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
    _hasher: Optional[PasswordHasher] = PasswordHasher()
except ImportError:  # pragma: no cover
    _hasher = None
    VerifyMismatchError = VerificationError = InvalidHashError = Exception  # type: ignore

# How long a sign-in lasts before the user must authenticate again.
SESSION_TTL_HOURS = max(1, int(os.environ.get("SESSION_TTL_HOURS", "12")))

# Set AUTH_REQUIRED=0 to run the API open (local development only).
AUTH_REQUIRED = os.environ.get("AUTH_REQUIRED", "1").strip() not in ("0", "false", "no")


def is_available() -> bool:
    """Auth needs both a database to store accounts in and a hasher."""
    return bool(db_service.is_configured() and _hasher is not None)


def enforced() -> bool:
    """True when requests must carry a valid session."""
    return AUTH_REQUIRED and is_available()


def _token_hash(token: str) -> str:
    """Sessions are looked up by hash, so the raw token is never at rest."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── accounts ────────────────────────────────────────────────────────────────

def create_user(email: str, password: str, name: str = "", role: str = "analyst") -> Dict[str, Any]:
    """
    Create an account. The password is hashed here and immediately discarded —
    it is never written to the database, a log, or the return value.
    """
    if _hasher is None:
        raise RuntimeError("argon2-cffi is not installed — cannot hash passwords.")
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("A valid email address is required.")
    if len(password or "") < 10:
        raise ValueError("Password must be at least 10 characters.")

    digest = _hasher.hash(password)
    with db_service._connection() as conn, conn.cursor(row_factory=db_service.dict_row) as cur:
        cur.execute(
            """
            INSERT INTO app_user (email, name, password_hash, role)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (lower(email)) DO UPDATE
                SET password_hash = EXCLUDED.password_hash,
                    name          = COALESCE(NULLIF(EXCLUDED.name, ''), app_user.name),
                    role          = EXCLUDED.role,
                    is_active     = TRUE
            RETURNING id, email, name, role
            """,
            (email, (name or "").strip(), digest, role),
        )
        return dict(cur.fetchone())


def authenticate(email: str, password: str) -> Optional[Dict[str, Any]]:
    """Return the user on success, None on any failure. Never says which failed."""
    if not is_available():
        return None
    email = (email or "").strip().lower()
    try:
        with db_service._connection() as conn, conn.cursor(row_factory=db_service.dict_row) as cur:
            cur.execute(
                "SELECT id, email, name, role, password_hash, is_active "
                "FROM app_user WHERE lower(email) = %s", (email,))
            row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        print(f"  [AUTH] lookup failed: {exc.__class__.__name__}")
        return None

    if not row or not row["is_active"]:
        # Hash anyway so a missing account and a wrong password take the same
        # time — otherwise response timing reveals which emails are registered.
        if _hasher is not None:
            try:
                _hasher.hash(password or "x")
            except Exception:  # noqa: BLE001
                pass
        return None

    try:
        _hasher.verify(row["password_hash"], password or "")
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"  [AUTH] verify error: {exc.__class__.__name__}")
        return None

    try:
        with db_service._connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE app_user SET last_login_at = now() WHERE id = %s", (row["id"],))
    except Exception:  # noqa: BLE001 — a stamp failure must not block sign-in
        pass
    return {"id": row["id"], "email": row["email"], "name": row["name"], "role": row["role"]}


# ── sessions ────────────────────────────────────────────────────────────────

def start_session(user_id: int, user_agent: str = "") -> Dict[str, Any]:
    """Issue a bearer token. Only its hash is persisted."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    with db_service._connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO user_session (token_hash, user_id, user_agent, expires_at) "
            "VALUES (%s, %s, %s, %s)",
            (_token_hash(token), user_id, (user_agent or "")[:300], expires))
    return {"token": token, "expires_at": expires.isoformat()}


def user_for_token(token: str) -> Optional[Dict[str, Any]]:
    """Resolve a bearer token to its user, or None if invalid/expired."""
    if not token or not is_available():
        return None
    try:
        with db_service._connection() as conn, conn.cursor(row_factory=db_service.dict_row) as cur:
            cur.execute(
                """
                SELECT u.id, u.email, u.name, u.role
                FROM user_session s JOIN app_user u ON u.id = s.user_id
                WHERE s.token_hash = %s AND s.expires_at > now() AND u.is_active
                """, (_token_hash(token),))
            row = cur.fetchone()
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        print(f"  [AUTH] session lookup failed: {exc.__class__.__name__}")
        return None


def end_session(token: str) -> None:
    """Sign out. Deleting the row makes the token dead immediately."""
    if not token or not is_available():
        return
    try:
        with db_service._connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM user_session WHERE token_hash = %s", (_token_hash(token),))
    except Exception as exc:  # noqa: BLE001
        print(f"  [AUTH] sign-out failed: {exc.__class__.__name__}")


def purge_expired_sessions() -> int:
    """Housekeeping at startup — expired rows serve no purpose."""
    if not is_available():
        return 0
    try:
        with db_service._connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM user_session WHERE expires_at < now()")
            return cur.rowcount or 0
    except Exception:  # noqa: BLE001
        return 0


# ── Google sign-in ──────────────────────────────────────────────────────────
#
# The browser runs Google Identity Services and hands us an ID token. We verify
# that token against Google's public keys server-side — never trust the email a
# client claims — then issue our own session. Google authenticates the person;
# this application still decides who is allowed in.

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()

# Who may sign in with Google. Comma-separated domains, e.g.
# "listenfirstmedia.com". Leave empty to allow only addresses that already exist
# as accounts — the safest default, since an OAuth client alone would otherwise
# let anyone with a Google account in.
GOOGLE_ALLOWED_DOMAINS = [
    d.strip().lower().lstrip("@")
    for d in os.environ.get("GOOGLE_ALLOWED_DOMAINS", "").split(",")
    if d.strip()
]


def google_enabled() -> bool:
    return bool(GOOGLE_CLIENT_ID) and is_available()


def _verify_google_token(id_token_str: str) -> Optional[Dict[str, Any]]:
    """Validate the ID token with Google and return its claims, or None."""
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token
    except ImportError:
        print("  [AUTH] google-auth is not installed — Google sign-in unavailable.")
        return None
    try:
        claims = google_id_token.verify_oauth2_token(
            id_token_str, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except Exception as exc:  # noqa: BLE001 — any failure is a failed sign-in
        print(f"  [AUTH] Google token rejected: {exc.__class__.__name__}")
        return None
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        return None
    if not claims.get("email") or not claims.get("email_verified"):
        return None
    return claims


def _existing_user(email: str) -> Optional[Dict[str, Any]]:
    try:
        with db_service._connection() as conn, conn.cursor(row_factory=db_service.dict_row) as cur:
            cur.execute("SELECT id, email, name, role, is_active FROM app_user "
                        "WHERE lower(email) = %s", (email,))
            row = cur.fetchone()
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        print(f"  [AUTH] lookup failed: {exc.__class__.__name__}")
        return None


def google_sign_in(id_token_str: str) -> tuple[Optional[Dict[str, Any]], str]:
    """
    Exchange a verified Google ID token for an application user.

    Returns ``(user, reason)``. ``user`` is None when sign-in is refused, and
    ``reason`` explains why in terms safe to show the person trying to sign in.
    """
    if not google_enabled():
        return None, "Google sign-in is not configured on this server."

    claims = _verify_google_token(id_token_str)
    if not claims:
        return None, "Google could not verify that sign-in. Please try again."

    email = claims["email"].strip().lower()
    name = (claims.get("name") or "").strip()
    domain = email.rsplit("@", 1)[-1]
    existing = _existing_user(email)

    if existing and not existing["is_active"]:
        return None, "That account has been deactivated."

    # An allowed domain provisions on first sign-in; otherwise the account must
    # already exist. Without this, any Google account in the world could sign in.
    if not existing and domain not in GOOGLE_ALLOWED_DOMAINS:
        return None, (f"{email} is not authorised for this tool. Ask an administrator "
                      f"to add you, or sign in with your work account.")

    try:
        with db_service._connection() as conn, conn.cursor(row_factory=db_service.dict_row) as cur:
            cur.execute(
                """
                INSERT INTO app_user (email, name, password_hash, role)
                VALUES (%s, %s, '', 'analyst')
                ON CONFLICT (lower(email)) DO UPDATE
                    SET name          = COALESCE(NULLIF(EXCLUDED.name, ''), app_user.name),
                        last_login_at = now()
                RETURNING id, email, name, role
                """,
                (email, name),
            )
            user = dict(cur.fetchone())
    except Exception as exc:  # noqa: BLE001
        print(f"  [AUTH] Google provisioning failed: {exc.__class__.__name__}: {exc}")
        return None, "Could not complete sign-in. Please try again."

    # password_hash is '' for Google-only accounts. authenticate() runs the hash
    # through Argon2 verify, which rejects an empty hash — so a Google account
    # cannot be signed into with a password. That is intentional.
    return user, ""


def user_count() -> int:
    """Used to tell 'auth not set up yet' apart from 'wrong credentials'."""
    if not is_available():
        return 0
    try:
        with db_service._connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM app_user WHERE is_active")
            return int(cur.fetchone()[0])
    except Exception:  # noqa: BLE001
        return 0
