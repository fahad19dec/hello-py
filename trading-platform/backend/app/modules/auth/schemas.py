import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, model_validator


def _normalise_email(value: str) -> str:
    return value.strip().lower()


Email = Annotated[EmailStr, AfterValidator(_normalise_email)]
# Upper bounds only stop oversized input; the password policy lives in core.passwords.
Password = Annotated[str, Field(min_length=1, max_length=1024)]
Token = Annotated[str, Field(min_length=20, max_length=200)]
OtpCode = Annotated[str, Field(min_length=6, max_length=12)]
RecoveryCodeIn = Annotated[str, Field(min_length=8, max_length=40)]


class RegisterIn(BaseModel):
    email: Email
    password: Password


class EmailIn(BaseModel):
    email: Email


class TokenIn(BaseModel):
    token: Token


class LoginIn(BaseModel):
    email: Email
    password: Password


class SecondFactorIn(BaseModel):
    code: OtpCode | None = None
    recovery_code: RecoveryCodeIn | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if bool(self.code) == bool(self.recovery_code):
            raise ValueError("provide either an authenticator code or a recovery code")
        return self


class PasswordIn(BaseModel):
    password: Password


class CodeIn(BaseModel):
    code: OtpCode


class DisableMfaIn(SecondFactorIn):
    password: Password


class RegenerateCodesIn(BaseModel):
    password: Password
    code: OtpCode


class ChangePasswordIn(BaseModel):
    current_password: Password
    new_password: Password


class ResetPasswordIn(BaseModel):
    token: Token
    new_password: Password


class MessageOut(BaseModel):
    message: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    timezone: str
    role: str
    status: str
    mfa_enabled: bool
    email_verified: bool
    created_at: datetime
    last_login_at: datetime | None

    @model_validator(mode="before")
    @classmethod
    def _from_user(cls, value):
        if isinstance(value, dict):
            return value
        return {
            "id": value.id,
            "email": value.email,
            "display_name": value.display_name,
            "timezone": value.timezone,
            "role": value.role,
            "status": value.status,
            "mfa_enabled": value.mfa_enabled,
            "email_verified": value.email_verified_at is not None,
            "created_at": value.created_at,
            "last_login_at": value.last_login_at,
        }


class LoginOut(BaseModel):
    mfa_required: bool
    csrf_token: str
    user: UserOut | None = None


class MeOut(BaseModel):
    user: UserOut
    csrf_token: str
    session_id: uuid.UUID
    session_expires_at: datetime
    second_factor_used: bool


class MfaSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class MfaStatusOut(BaseModel):
    enabled: bool
    recovery_codes_remaining: int


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool


class CountOut(BaseModel):
    count: int
