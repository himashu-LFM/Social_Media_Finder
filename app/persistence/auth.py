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

from app.persistence import db as db_service

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
    _hasher: Optional[PasswordHasher] = PasswordHasher()
except ImportError:  # pragma: no cover
    _hasher = None
    VerifyMismatchError = VerificationError = InvalidHashError = Exception  # type: ignore

# How long a sign-in lasts before the user must authenticate again.
SESSION_TTL_HOURS = max(1, int(os.environ.get("SESSION_TTL_HOURS", "12")))

def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "")


# Set AUTH_REQUIRED=0 to run the API open (local development only).
AUTH_REQUIRED = _flag("AUTH_REQUIRED", "1")

# Set APP_ENV=production on every deployed environment.
#
# This exists because ``enforced()`` used to FAIL OPEN: it returned False
# whenever the database was unreachable, so a missing DATABASE_URL or a momentary
# RDS failover would silently drop authentication from every endpoint and serve
# the whole tool to anonymous callers. That is the correct behaviour on a laptop
# and unacceptable anywhere else. In production the API now refuses requests it
# cannot authenticate instead of waving them through.
IS_PRODUCTION = _flag("APP_ENV", "development") and     os.environ.get("APP_ENV", "development").strip().lower() in ("production", "prod", "staging")


def is_available() -> bool:
    """Auth needs both a database to store accounts in and a hasher."""
    return bool(db_service.is_configured() and _hasher is not None)


def enforced() -> bool:
    """True when requests must carry a valid session."""
    return AUTH_REQUIRED and is_available()


def fail_closed() -> bool:
    """
    True when auth is REQUIRED but cannot currently be performed.

    The caller must reject the request (503) rather than serve it unauthenticated.
    Only ever true in production: locally, a developer without a database still
    gets the open API they expect.
    """
    return IS_PRODUCTION and AUTH_REQUIRED and not is_available()


def startup_report() -> list[str]:
    """
    Configuration problems that make a deployment unsafe or unusable.

    Returned rather than raised so the caller decides whether to refuse to boot
    (production) or simply warn (local development).
    """
    problems: list[str] = []
    if not IS_PRODUCTION:
        return problems
    if not AUTH_REQUIRED:
        problems.append(
            "AUTH_REQUIRED=0 in production — every endpoint would be public.")
    if not db_service.is_configured():
        problems.append(
            "DATABASE_URL is not set — accounts and sessions cannot be read, so "
            "no request can be authenticated.")
    if _hasher is None:
        problems.append(
            "argon2-cffi is not installed — passwords cannot be verified.")
    if not os.environ.get("CORS_ORIGINS", "").strip():
        problems.append(
            "CORS_ORIGINS is not set — the browser UI will be unable to call this "
            "API from its real origin.")
    return problems


def _token_hash(token: str) -> str:
    """Sessions are looked up by hash, so the raw token is never at rest."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── accounts ────────────────────────────────────────────────────────────────

MIN_PASSWORD_LENGTH = 10


def create_user(email: str, password: str, name: str = "", role: str = "analyst",
                must_change_password: bool = False,
                created_by: Optional[int] = None) -> Dict[str, Any]:
    """
    Create an account. The password is hashed here and immediately discarded —
    it is never written to the database, a log, or the return value.

    ``must_change_password`` is what makes an admin-issued temporary password
    temporary: the holder cannot use the application until they replace it.
    Without it, whoever typed the temp password can sign in as that person for
    as long as the account exists.
    """
    if _hasher is None:
        raise RuntimeError("argon2-cffi is not installed — cannot hash passwords.")
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("A valid email address is required.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if role not in ("analyst", "admin"):
        raise ValueError("Role must be 'analyst' or 'admin'.")

    digest = _hasher.hash(password)
    with db_service._connection() as conn, conn.cursor(row_factory=db_service.dict_row) as cur:
        cur.execute(
            """
            INSERT INTO app_user (email, name, password_hash, role,
                                  must_change_password, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (lower(email)) DO UPDATE
                SET password_hash        = EXCLUDED.password_hash,
                    name                 = COALESCE(NULLIF(EXCLUDED.name, ''), app_user.name),
                    role                 = EXCLUDED.role,
                    must_change_password = EXCLUDED.must_change_password,
                    is_active            = TRUE
            RETURNING id, email, name, role, must_change_password
            """,
            (email, (name or "").strip(), digest, role,
             bool(must_change_password), created_by),
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
                "SELECT id, email, name, role, password_hash, is_active, "
                "must_change_password FROM app_user WHERE lower(email) = %s", (email,))
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
    return {"id": row["id"], "email": row["email"], "name": row["name"],
            "role": row["role"],
            "must_change_password": bool(row.get("must_change_password"))}


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
                SELECT u.id, u.email, u.name, u.role, u.must_change_password
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


# ── admin: managing accounts from the portal ────────────────────────────────
#
# There is no public sign-up. Someone with the `admin` role creates accounts,
# and every new account starts with a temporary password it must replace. That
# is deliberate: an internal tool with an open registration endpoint is an open
# door, and "we'll lock it down later" never happens.


def is_admin(user: Optional[Dict[str, Any]]) -> bool:
    return bool(user) and str(user.get("role", "")).lower() == "admin"


def generate_temp_password(length: int = 14) -> str:
    """
    A readable one-time password.

    Deliberately drops characters that get misread when someone copies a
    password off a screen or out of an email — no O/0, no l/1/I. The password is
    long enough that losing them costs nothing, and it is replaced on first
    sign-in anyway.
    """
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet)
                   for _ in range(max(MIN_PASSWORD_LENGTH, length)))


def list_users() -> list:
    """Every account, for the admin screen. Never returns a password hash."""
    if not is_available():
        return []
    try:
        with db_service._connection() as conn, conn.cursor(row_factory=db_service.dict_row) as cur:
            cur.execute(
                "SELECT id, email, name, role, is_active, must_change_password, "
                "last_login_at, created_at FROM app_user "
                "ORDER BY created_at DESC, id DESC")
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        print(f"  [AUTH] list_users failed: {exc.__class__.__name__}")
        return []


def set_password(user_id: int, new_password: str, must_change: bool = False) -> bool:
    """
    Replace an account's password, and end every session that account has.

    Signing the other sessions out is the point: if the reason for changing the
    password is that someone else knows the old one, leaving their session alive
    achieves nothing.
    """
    if _hasher is None or not is_available():
        return False
    if len(new_password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    digest = _hasher.hash(new_password)
    try:
        with db_service._connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE app_user SET password_hash = %s, must_change_password = %s "
                "WHERE id = %s", (digest, bool(must_change), user_id))
            changed = cur.rowcount or 0
            cur.execute("DELETE FROM user_session WHERE user_id = %s", (user_id,))
        return changed > 0
    except Exception as exc:  # noqa: BLE001
        print(f"  [AUTH] set_password failed: {exc.__class__.__name__}")
        return False


def set_active(user_id: int, active: bool) -> bool:
    """Enable or disable an account. Disabling also revokes its sessions."""
    if not is_available():
        return False
    try:
        with db_service._connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE app_user SET is_active = %s WHERE id = %s",
                        (bool(active), user_id))
            changed = cur.rowcount or 0
            if not active:
                cur.execute("DELETE FROM user_session WHERE user_id = %s", (user_id,))
        return changed > 0
    except Exception as exc:  # noqa: BLE001
        print(f"  [AUTH] set_active failed: {exc.__class__.__name__}")
        return False


def admin_count() -> int:
    """Used to refuse any change that would leave nobody able to administer."""
    if not is_available():
        return 0
    try:
        with db_service._connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM app_user WHERE is_active AND role = 'admin'")
            return int(cur.fetchone()[0])
    except Exception:  # noqa: BLE001
        return 0


# ── password reset by emailed code ──────────────────────────────────────────
#
# Six digits is a million possibilities, which sounds like plenty and is not:
# unthrottled it falls in minutes. The attempt cap is what makes this safe, not
# the length of the code. Three rules are load-bearing — expiry, the cap, and
# deleting the record on success so a code cannot be replayed.

RESET_CODE_TTL_MINUTES = max(1, int(os.environ.get("RESET_CODE_TTL_MINUTES", "10")))
RESET_MAX_ATTEMPTS = max(1, int(os.environ.get("RESET_MAX_ATTEMPTS", "5")))


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def create_reset_code(email: str) -> Optional[str]:
    """
    Issue a reset code for a registered address, or None if it is not one.

    Returns the plaintext code for the caller to email; only its hash is stored.
    The primary key on email means a new request REPLACES any live code, so
    nobody can bank valid codes by requesting repeatedly.
    """
    email = (email or "").strip().lower()
    if not email or not is_available():
        return None
    if not _existing_user(email):
        return None

    code = f"{secrets.randbelow(1_000_000):06d}"      # CSPRNG, not random.randint
    expires = datetime.now(timezone.utc) + timedelta(minutes=RESET_CODE_TTL_MINUTES)
    try:
        with db_service._connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pw_reset_codes (email, code_hash, expires_at, attempts)
                VALUES (%s, %s, %s, 0)
                ON CONFLICT (email) DO UPDATE
                    SET code_hash  = EXCLUDED.code_hash,
                        expires_at = EXCLUDED.expires_at,
                        attempts   = 0,
                        created_at = now()
                """, (email, _code_hash(code), expires))
    except Exception as exc:  # noqa: BLE001
        print(f"  [AUTH] could not store reset code: {exc.__class__.__name__}")
        return None
    return code


def _delete_reset_code(email: str) -> None:
    try:
        with db_service._connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM pw_reset_codes WHERE email = %s", (email,))
    except Exception:  # noqa: BLE001
        pass


def redeem_reset_code(email: str, code: str, new_password: str) -> tuple:
    """
    Verify a code and set the new password.

    Returns ``(ok, message, status)``. The status separates the cases a client
    must handle differently: 429 means the code was burned by too many wrong
    guesses and a fresh one is needed; 400 means try again.
    """
    # Normalise on read AND write, or a capitalised retry silently misses the row.
    email = (email or "").strip().lower()
    code = (code or "").strip()
    if not is_available():
        return False, "Accounts are unavailable.", 503
    if len(new_password or "") < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", 400

    try:
        with db_service._connection() as conn, conn.cursor(row_factory=db_service.dict_row) as cur:
            cur.execute("SELECT code_hash, expires_at, attempts FROM pw_reset_codes "
                        "WHERE email = %s", (email,))
            row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        print(f"  [AUTH] reset lookup failed: {exc.__class__.__name__}")
        return False, "Could not verify that code. Please try again.", 500

    if not row:
        return False, "No reset in progress for that address. Request a new code.", 400
    if row["expires_at"] <= datetime.now(timezone.utc):
        _delete_reset_code(email)
        return False, "That code has expired. Request a new one.", 400
    if int(row["attempts"]) >= RESET_MAX_ATTEMPTS:
        _delete_reset_code(email)
        return False, "Too many incorrect attempts. Request a new code.", 429

    if not secrets.compare_digest(row["code_hash"], _code_hash(code)):
        # The record SURVIVES a wrong guess — only the counter moves. Deleting
        # here would let anyone cancel someone else's reset with one bad guess.
        try:
            with db_service._connection() as conn, conn.cursor() as cur:
                cur.execute("UPDATE pw_reset_codes SET attempts = attempts + 1 "
                            "WHERE email = %s", (email,))
        except Exception:  # noqa: BLE001
            pass
        left = RESET_MAX_ATTEMPTS - int(row["attempts"]) - 1
        if left > 0:
            return False, f"That code is not correct. {left} attempt(s) left.", 400
        return False, "Too many incorrect attempts. Request a new code.", 400

    user = _existing_user(email)
    if not user:
        _delete_reset_code(email)
        return False, "That account no longer exists.", 404
    if not set_password(int(user["id"]), new_password, must_change=False):
        return False, "Could not update the password. Please try again.", 500

    # Single use: without this delete, the same code resets the account again.
    _delete_reset_code(email)
    return True, "Password updated. You can sign in now.", 200


def purge_expired_reset_codes() -> int:
    """Housekeeping at startup, mirroring purge_expired_sessions()."""
    if not is_available():
        return 0
    try:
        with db_service._connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM pw_reset_codes WHERE expires_at < now()")
            return cur.rowcount or 0
    except Exception:  # noqa: BLE001
        return 0


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
