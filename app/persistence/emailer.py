"""
emailer.py  —  outbound email (password-reset codes, account invitations)

One rule governs this module: **email failures never break the thing they were
sending about.** An account that was created successfully must not report
failure because SMTP timed out, and a reset code that was stored must not leave
the caller thinking it wasn't. Every function returns a boolean and logs; none
of them raise.

With SMTP unconfigured the whole module no-ops and says so. That is the normal
state on a laptop, and it is why ``send_reset_code`` prints the code to the
server log in development — the flow stays testable without a mail server. It
refuses to do that in production, where the log is not a private place.

Environment variables (all optional; without SMTP_HOST nothing is sent):
    SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD
    SMTP_FROM            From: address (defaults to SMTP_USER)
    SMTP_STARTTLS        "1" (default) to upgrade the connection
    APP_BASE_URL         Link included in emails, e.g. https://curator.example.com
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "").strip() or SMTP_USER
SMTP_STARTTLS = os.environ.get("SMTP_STARTTLS", "1").strip().lower() not in ("0", "false", "no")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")

_TIMEOUT = 15


def is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "development").strip().lower() in (
        "production", "prod", "staging")


def send(to: str, subject: str, body: str) -> bool:
    """
    Send one plain-text email. Returns True only if the server accepted it.

    Never raises: callers use this for best-effort notifications alongside work
    that has already succeeded.
    """
    to = (to or "").strip()
    if not to:
        return False
    if not is_configured():
        print(f"  [EMAIL] SMTP not configured — skipped '{subject}' to {to}")
        return False

    message = EmailMessage()
    message["From"] = SMTP_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=_TIMEOUT,
                                  context=ssl.create_default_context()) as server:
                if SMTP_USER:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(message)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=_TIMEOUT) as server:
                if SMTP_STARTTLS:
                    server.starttls(context=ssl.create_default_context())
                if SMTP_USER:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(message)
    except Exception as exc:  # noqa: BLE001 — email must never break the caller
        print(f"  [EMAIL] send failed to {to}: {exc.__class__.__name__}: {exc}")
        return False

    print(f"  [EMAIL] sent '{subject}' to {to}")
    return True


def _sign_in_line() -> str:
    return f"\nSign in: {APP_BASE_URL}/login\n" if APP_BASE_URL else ""


def send_reset_code(to: str, code: str, minutes: int) -> bool:
    """
    Email a password-reset code.

    In development with no SMTP the code is printed to the server log so the
    flow can be exercised end to end without a mail server. In production it is
    never logged — a reset code in a log file is a reset code in whatever
    aggregates that log.
    """
    sent = send(
        to,
        "Your Curator AI password reset code",
        f"Your password reset code is: {code}\n\n"
        f"It expires in {minutes} minutes and can be used once.\n"
        f"Enter it on the sign-in page along with your new password.\n"
        f"{_sign_in_line()}\n"
        f"If you did not request this, you can ignore this email — your password "
        f"has not changed.\n",
    )
    if not sent and not _is_production():
        print(f"  [EMAIL] DEV ONLY — reset code for {to} is {code} "
              f"(expires in {minutes} min)")
    return sent


def send_account_created(to: str, temp_password: Optional[str] = None) -> bool:
    """
    Tell someone an account was made for them.

    ``temp_password`` is included only when SMTP is configured — otherwise there
    is nowhere safe to put it, and the admin reads it off the screen instead.
    """
    lines = [
        "An account has been created for you on Curator AI.",
        "",
        f"Email: {to}",
    ]
    if temp_password:
        lines += [
            f"Temporary password: {temp_password}",
            "",
            "You will be asked to choose a new password the first time you sign in.",
        ]
    lines.append(_sign_in_line())
    return send(to, "Your Curator AI account", "\n".join(lines))
