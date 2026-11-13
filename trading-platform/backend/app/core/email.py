"""Outgoing email: a small interface with console, in-memory and SMTP backends."""

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from functools import lru_cache
from typing import Protocol

from .config import get_settings

log = logging.getLogger("app.email")


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    def send(self, email: Email) -> None: ...


class ConsoleEmailSender:
    """Development only: prints the email, including its links, to the log.

    Settings validation refuses this backend in production, because the links
    carry sign-in secrets.
    """

    def send(self, email: Email) -> None:
        log.info("EMAIL to=%s subject=%r\n%s", email.to, email.subject, email.body)


class MemoryEmailSender:
    """Tests: keeps sent emails in a list."""

    def __init__(self) -> None:
        self.outbox: list[Email] = []

    def send(self, email: Email) -> None:
        self.outbox.append(email)


class SmtpEmailSender:
    def send(self, email: Email) -> None:
        s = get_settings()
        message = MimeMessage()
        message["From"] = s.email_from
        message["To"] = email.to
        message["Subject"] = email.subject
        message.set_content(email.body)
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
                if s.smtp_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError):
            # Never log the body: it contains a single-use sign-in link.
            log.exception("could not send email with subject %r", email.subject)


@lru_cache
def get_email_sender() -> EmailSender:
    backend = get_settings().email_backend
    if backend == "smtp":
        return SmtpEmailSender()
    if backend == "memory":
        return MemoryEmailSender()
    return ConsoleEmailSender()


# --- messages ---------------------------------------------------------------


def _footer() -> str:
    return f"\n\nIf you did not expect this email you can ignore it.\n— {get_settings().app_name}"


def verification_email(to: str, token: str) -> Email:
    s = get_settings()
    link = f"{s.frontend_origin.rstrip('/')}/verify-email?token={token}"
    return Email(
        to,
        f"Confirm your email for {s.app_name}",
        f"Confirm your email address to finish creating your account:\n\n{link}\n\n"
        "The link works once and expires in 24 hours." + _footer(),
    )


def already_registered_email(to: str) -> Email:
    s = get_settings()
    base = s.frontend_origin.rstrip("/")
    return Email(
        to,
        f"You already have a {s.app_name} account",
        "Someone tried to create an account with this email address, but one already exists.\n\n"
        f"Sign in: {base}/login\nForgot your password: {base}/forgot-password" + _footer(),
    )


def password_reset_email(to: str, token: str) -> Email:
    s = get_settings()
    link = f"{s.frontend_origin.rstrip('/')}/reset-password?token={token}"
    return Email(
        to,
        f"Reset your {s.app_name} password",
        f"Use this link to choose a new password:\n\n{link}\n\n"
        "The link works once and expires in 1 hour. Your current password keeps working until you change it."
        + _footer(),
    )


def security_notice_email(to: str, what_happened: str) -> Email:
    s = get_settings()
    return Email(
        to,
        f"Security notice for your {s.app_name} account",
        f"{what_happened}\n\nIf this was you, no action is needed. If it was not, reset your password "
        f"immediately at {s.frontend_origin.rstrip('/')}/forgot-password and contact support." + _footer(),
    )
