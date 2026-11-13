"""Operator commands.

    python -m app.cli create-admin --email you@example.com [--role super_admin]
    python -m app.cli generate-encryption-key

There are no default accounts or passwords: the first super admin is created
here, by someone with access to the server.
"""
import argparse
import getpass
import os
import sys

from sqlalchemy import select

from app.core import audit, passwords
from app.core.crypto import generate_key
from app.core.db import SYSTEM, access, new_session
from app.core.errors import AppError
from app.models.identity import User, UserCredential, UserRole
from app.modules.auth.service import utcnow


def create_admin(email: str, role: str) -> int:
    email = email.strip().lower()
    password = os.environ.get("ADMIN_PASSWORD")
    if password is None:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1
    try:
        passwords.validate_new_password(password, email=email)
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        return 1

    db = new_session()
    try:
        with access(db, SYSTEM):
            if db.scalar(select(User).where(User.email == email)) is not None:
                print("An account with that email already exists.", file=sys.stderr)
                return 1
            user = User(email=email, role=role, email_verified_at=utcnow())
            db.add(user)
            db.flush()
            db.add(UserCredential(user_id=user.id, password_hash=passwords.hash_password(password)))
            audit.record(db, "admin.bootstrap.created", user_id=user.id, detail={"role": role, "via": "cli"})
            db.commit()
    finally:
        db.close()
    print(f"Created {role} {email}. Sign in and turn on two-factor authentication to use the admin area.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("create-admin", help="create a staff account")
    admin.add_argument("--email", required=True)
    admin.add_argument(
        "--role", default=UserRole.SUPER_ADMIN.value,
        choices=[UserRole.SUPER_ADMIN.value, UserRole.SUPPORT_ADMIN.value],
    )
    commands.add_parser("generate-encryption-key", help="print a new key for DATA_ENCRYPTION_KEYS")

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return create_admin(args.email, args.role)
    print(generate_key())
    return 0


if __name__ == "__main__":
    sys.exit(main())
