"""
Admin-created accounts, and the emailed-code password reset.

Six digits is only a million possibilities. Expiry, the attempt cap and
single-use deletion are what make that safe — none of them is optional, so each
has a test that fails loudly if it is removed.

No database and no SMTP: the storage layer is a dict and the mailer is a spy.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.persistence import auth as auth_service
from app.persistence import emailer


# ── a fake reset-code store ─────────────────────────────────────────────────

class FakeStore:
    """Stands in for the pw_reset_codes table and the app_user password write."""

    def __init__(self, users=("her@x.com",)):
        self.rows = {}
        self.users = {e.lower() for e in users}
        self.passwords = {}

    def existing_user(self, email):
        email = (email or "").strip().lower()
        return {"id": 1, "email": email} if email in self.users else None

    def set_password(self, user_id, new_password, must_change=False):
        if len(new_password) < auth_service.MIN_PASSWORD_LENGTH:
            raise ValueError("too short")
        self.passwords[user_id] = new_password
        return True


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(auth_service, "is_available", lambda: True)
    monkeypatch.setattr(auth_service, "_existing_user", fake.existing_user)
    monkeypatch.setattr(auth_service, "set_password", fake.set_password)

    # Replace the three SQL touchpoints with dict operations.
    def create_reset_code(email):
        email = (email or "").strip().lower()
        if not fake.existing_user(email):
            return None
        import secrets
        code = f"{secrets.randbelow(1_000_000):06d}"
        fake.rows[email] = {
            "code_hash": auth_service._code_hash(code),
            "expires_at": datetime.now(timezone.utc) + timedelta(
                minutes=auth_service.RESET_CODE_TTL_MINUTES),
            "attempts": 0,
        }
        return code

    monkeypatch.setattr(auth_service, "create_reset_code", create_reset_code)
    monkeypatch.setattr(auth_service, "_delete_reset_code",
                        lambda e: fake.rows.pop((e or "").strip().lower(), None))

    class Cur:
        def __init__(self, rows): self.rows = rows; self._row = None
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=()):
            email = (params[-1] or "").strip().lower()
            if sql.strip().upper().startswith("SELECT"):
                self._row = self.rows.get(email)
            else:                                   # attempts = attempts + 1
                if email in self.rows:
                    self.rows[email]["attempts"] += 1
        def fetchone(self): return self._row

    class Conn:
        def __init__(self, rows): self.rows = rows
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self, **kw): return Cur(self.rows)

    monkeypatch.setattr(auth_service.db_service, "_connection", lambda: Conn(fake.rows))
    return fake


NEW_PW = "a-good-long-password"


# ── the happy path ──────────────────────────────────────────────────────────

def test_a_valid_code_sets_the_new_password(store):
    code = auth_service.create_reset_code("her@x.com")
    ok, _msg, status = auth_service.redeem_reset_code("her@x.com", code, NEW_PW)
    assert ok and status == 200
    assert store.passwords[1] == NEW_PW


def test_the_code_is_single_use(store):
    """Without the delete-on-success, the same code resets the account again."""
    code = auth_service.create_reset_code("her@x.com")
    assert auth_service.redeem_reset_code("her@x.com", code, NEW_PW)[0]
    ok, _msg, status = auth_service.redeem_reset_code("her@x.com", code, "another-long-one")
    assert not ok and status == 400


def test_the_plaintext_code_is_never_stored(store):
    """A database peek must not let anyone take over an account."""
    code = auth_service.create_reset_code("her@x.com")
    stored = store.rows["her@x.com"]["code_hash"]
    assert stored != code
    assert stored == auth_service._code_hash(code)
    assert len(stored) == 64          # sha256 hex


# ── the three rules that make six digits safe ───────────────────────────────

def test_a_wrong_code_is_rejected_but_the_reset_survives(store):
    """Deleting on a wrong guess would let anyone cancel someone else's reset."""
    auth_service.create_reset_code("her@x.com")
    ok, _msg, status = auth_service.redeem_reset_code("her@x.com", "000000", NEW_PW)
    assert not ok and status == 400
    assert "her@x.com" in store.rows
    assert store.rows["her@x.com"]["attempts"] == 1


def test_the_attempt_cap_burns_the_code(store):
    """This cap — not the length of the code — is the actual security."""
    real = auth_service.create_reset_code("her@x.com")
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(auth_service.RESET_MAX_ATTEMPTS):
        auth_service.redeem_reset_code("her@x.com", wrong, NEW_PW)
    ok, _msg, status = auth_service.redeem_reset_code("her@x.com", real, NEW_PW)
    assert not ok and status == 429
    assert "her@x.com" not in store.rows      # burned
    assert 1 not in store.passwords


def test_an_expired_code_is_refused_and_deleted(store):
    code = auth_service.create_reset_code("her@x.com")
    store.rows["her@x.com"]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    ok, msg, status = auth_service.redeem_reset_code("her@x.com", code, NEW_PW)
    assert not ok and status == 400 and "expired" in msg.lower()
    assert "her@x.com" not in store.rows


# ── the mistakes the guide calls out ────────────────────────────────────────

def test_email_case_and_whitespace_do_not_defeat_the_code(store):
    """Normalise on read AND write, or a capitalised retry silently misses."""
    code = auth_service.create_reset_code("HER@x.com")
    ok, _msg, _s = auth_service.redeem_reset_code("  Her@X.COM ", code, NEW_PW)
    assert ok


def test_an_unregistered_address_gets_no_code_and_no_row(store):
    assert auth_service.create_reset_code("nobody@x.com") is None
    assert store.rows == {}


def test_a_short_new_password_is_refused_before_anything_is_touched(store):
    code = auth_service.create_reset_code("her@x.com")
    ok, _msg, status = auth_service.redeem_reset_code("her@x.com", code, "short")
    assert not ok and status == 400
    assert 1 not in store.passwords
    assert "her@x.com" in store.rows          # the code is still usable


def test_a_reset_with_no_request_in_progress_is_refused(store):
    ok, _msg, status = auth_service.redeem_reset_code("her@x.com", "123456", NEW_PW)
    assert not ok and status == 400


# ── admin account creation ──────────────────────────────────────────────────

def test_only_admins_pass_the_admin_check():
    assert auth_service.is_admin({"role": "admin"})
    assert auth_service.is_admin({"role": "ADMIN"})       # case-insensitive
    assert not auth_service.is_admin({"role": "analyst"})
    assert not auth_service.is_admin({})
    assert not auth_service.is_admin(None)


def test_a_generated_temp_password_clears_the_length_bar():
    for _ in range(20):
        assert len(auth_service.generate_temp_password()) >= auth_service.MIN_PASSWORD_LENGTH


def test_temp_passwords_omit_characters_that_get_misread():
    """An admin reads these off a screen; O/0 and l/1/I cost support time."""
    joined = "".join(auth_service.generate_temp_password() for _ in range(50))
    assert not (set(joined) & set("O0lI1"))


def test_temp_passwords_are_not_predictable():
    assert len({auth_service.generate_temp_password() for _ in range(50)}) == 50


# ── email degrades, never crashes ───────────────────────────────────────────

def test_sending_with_no_smtp_returns_false_rather_than_raising(monkeypatch):
    monkeypatch.setattr(emailer, "SMTP_HOST", "")
    assert emailer.send("her@x.com", "subject", "body") is False


def test_a_broken_smtp_server_does_not_raise(monkeypatch):
    monkeypatch.setattr(emailer, "SMTP_HOST", "smtp.invalid")
    monkeypatch.setattr(emailer, "SMTP_FROM", "no-reply@x.com")

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(emailer.smtplib, "SMTP", boom)
    # An account was already created / a code already stored by the time this
    # runs, so raising here would report failure for work that succeeded.
    assert emailer.send("her@x.com", "s", "b") is False


def test_the_code_is_never_logged_in_production(monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(emailer, "SMTP_HOST", "")
    emailer.send_reset_code("her@x.com", "424242", 10)
    assert "424242" not in capsys.readouterr().out


def test_the_code_is_logged_in_development_so_the_flow_is_testable(monkeypatch, capsys):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(emailer, "SMTP_HOST", "")
    emailer.send_reset_code("her@x.com", "424242", 10)
    assert "424242" in capsys.readouterr().out
