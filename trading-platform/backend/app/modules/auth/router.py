"""HTTP endpoints for accounts, sign-in, two-factor and session management."""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core import ratelimit, tokens
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.email import get_email_sender
from app.core.http import request_meta

from . import schemas, service
from .deps import AuthContext, current_user, session_from_request

router = APIRouter(prefix="/auth", tags=["auth"])

REGISTERED_MESSAGE = "If this address can be registered, we have sent it a confirmation link."
RESET_MESSAGE = "If an account exists for this address, we have sent it a reset link."


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_absolute_days * 86400,
        httponly=True,  # not readable by page scripts
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.cookie_name, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax"
    )


def _sender(background: BackgroundTasks) -> service.SendEmail:
    """Emails go out after the response, so slow mail servers neither delay the
    request nor make "address exists" and "address unknown" take different times."""
    sender = get_email_sender()
    return lambda email: background.add_task(sender.send, email)


# ----------------------------------------------------------------- registration


@router.post("/register", status_code=202, response_model=schemas.MessageOut)
def register(body: schemas.RegisterIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    meta = request_meta(request)
    ratelimit.enforce(f"register:ip:{meta.ip}", limit=10, window_seconds=3600)
    ratelimit.enforce(f"register:email:{body.email}", limit=5, window_seconds=3600)
    service.register(db, email=body.email, password=body.password, meta=meta, send=_sender(background))
    return {"message": REGISTERED_MESSAGE}


@router.post("/verify-email", response_model=schemas.MessageOut)
def verify_email(body: schemas.TokenIn, request: Request, db: Session = Depends(get_db)):
    meta = request_meta(request)
    ratelimit.enforce(f"verify:ip:{meta.ip}", limit=30, window_seconds=3600)
    service.verify_email(db, token=body.token, meta=meta)
    return {"message": "Email confirmed. You can sign in now."}


@router.post("/resend-verification", status_code=202, response_model=schemas.MessageOut)
def resend_verification(
    body: schemas.EmailIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)
):
    meta = request_meta(request)
    ratelimit.enforce(f"resend:ip:{meta.ip}", limit=10, window_seconds=3600)
    ratelimit.enforce(f"resend:email:{body.email}", limit=3, window_seconds=3600)
    service.resend_verification(db, email=body.email, send=_sender(background))
    return {"message": REGISTERED_MESSAGE}


# ----------------------------------------------------------------- sign-in


@router.post("/login", response_model=schemas.LoginOut)
def login(body: schemas.LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    meta = request_meta(request)
    ratelimit.enforce(f"login:ip:{meta.ip}", limit=30, window_seconds=60)
    outcome = service.login(db, email=body.email, password=body.password, meta=meta)
    previous = request.cookies.get(settings.cookie_name)
    if previous:
        service.revoke_session_token(db, previous, "replaced")
    set_session_cookie(response, outcome.token, settings)
    return schemas.LoginOut(
        mfa_required=outcome.mfa_required,
        csrf_token=tokens.csrf_token_for(outcome.token, settings.secret_key),
        user=None if outcome.mfa_required else schemas.UserOut.model_validate(outcome.user),
    )


@router.post("/mfa/verify", response_model=schemas.LoginOut)
def verify_second_factor(
    body: schemas.SecondFactorIn,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    ctx: AuthContext = Depends(session_from_request),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    token = service.complete_mfa(
        db,
        user=ctx.user,
        session=ctx.session,
        code=body.code,
        recovery_code=body.recovery_code,
        meta=request_meta(request),
        send=_sender(background),
    )
    set_session_cookie(response, token, settings)
    return schemas.LoginOut(
        mfa_required=False,
        csrf_token=tokens.csrf_token_for(token, settings.secret_key),
        user=schemas.UserOut.model_validate(ctx.user),
    )


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(session_from_request),
    db: Session = Depends(get_db),
):
    service.logout(db, user=ctx.user, session=ctx.session, meta=request_meta(request))
    clear_session_cookie(response, get_settings())
    response.status_code = 204


@router.get("/me", response_model=schemas.MeOut)
def me(ctx: AuthContext = Depends(current_user)):
    return schemas.MeOut(
        user=schemas.UserOut.model_validate(ctx.user),
        csrf_token=ctx.csrf_token,
        session_id=ctx.session.id,
        session_expires_at=ctx.session.expires_at,
        second_factor_used=ctx.session.mfa_at is not None,
    )


# ----------------------------------------------------------------- passwords


@router.post("/password/forgot", status_code=202, response_model=schemas.MessageOut)
def forgot_password(
    body: schemas.EmailIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)
):
    meta = request_meta(request)
    ratelimit.enforce(f"forgot:ip:{meta.ip}", limit=10, window_seconds=3600)
    ratelimit.enforce(f"forgot:email:{body.email}", limit=3, window_seconds=3600)
    service.request_password_reset(db, email=body.email, meta=meta, send=_sender(background))
    return {"message": RESET_MESSAGE}


@router.post("/password/reset", response_model=schemas.MessageOut)
def reset_password(
    body: schemas.ResetPasswordIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)
):
    meta = request_meta(request)
    ratelimit.enforce(f"reset:ip:{meta.ip}", limit=20, window_seconds=3600)
    service.reset_password(
        db, token=body.token, new_password=body.new_password, meta=meta, send=_sender(background)
    )
    return {"message": "Password changed. Sign in with your new password."}


@router.post("/password/change", response_model=schemas.CountOut)
def change_password(
    body: schemas.ChangePasswordIn,
    request: Request,
    background: BackgroundTasks,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    revoked = service.change_password(
        db,
        user=ctx.user,
        session=ctx.session,
        current_password=body.current_password,
        new_password=body.new_password,
        meta=request_meta(request),
        send=_sender(background),
    )
    return {"count": revoked}


# ----------------------------------------------------------------- two-factor


@router.get("/mfa", response_model=schemas.MfaStatusOut)
def mfa_status(ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    return {
        "enabled": ctx.user.mfa_enabled,
        "recovery_codes_remaining": service.recovery_codes_remaining(db, ctx.user.id),
    }


@router.post("/mfa/setup", response_model=schemas.MfaSetupOut)
def mfa_setup(body: schemas.PasswordIn, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    secret, uri = service.begin_mfa_setup(db, user=ctx.user, password=body.password)
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/mfa/enable", response_model=schemas.RecoveryCodesOut)
def mfa_enable(
    body: schemas.CodeIn,
    request: Request,
    background: BackgroundTasks,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    ratelimit.enforce(f"mfa-enable:{ctx.user.id}", limit=10, window_seconds=900)
    codes = service.enable_mfa(
        db, user=ctx.user, session=ctx.session, code=body.code, meta=request_meta(request), send=_sender(background)
    )
    return {"recovery_codes": codes}


@router.post("/mfa/disable", status_code=204)
def mfa_disable(
    body: schemas.DisableMfaIn,
    request: Request,
    background: BackgroundTasks,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    ratelimit.enforce(f"mfa-disable:{ctx.user.id}", limit=10, window_seconds=900)
    service.disable_mfa(
        db,
        user=ctx.user,
        session=ctx.session,
        password=body.password,
        code=body.code,
        recovery_code=body.recovery_code,
        meta=request_meta(request),
        send=_sender(background),
    )
    return Response(status_code=204)


@router.post("/mfa/recovery-codes", response_model=schemas.RecoveryCodesOut)
def mfa_recovery_codes(
    body: schemas.RegenerateCodesIn,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    ratelimit.enforce(f"mfa-codes:{ctx.user.id}", limit=10, window_seconds=900)
    codes = service.regenerate_recovery_codes(
        db, user=ctx.user, password=body.password, code=body.code, meta=request_meta(request)
    )
    return {"recovery_codes": codes}


# ----------------------------------------------------------------- sessions


@router.get("/sessions", response_model=list[schemas.SessionOut])
def sessions(ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)):
    return [
        schemas.SessionOut(
            id=s.id,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            expires_at=s.expires_at,
            ip=s.ip,
            user_agent=s.user_agent,
            current=s.id == ctx.session.id,
        )
        for s in service.list_sessions(db, ctx.user.id)
    ]


@router.post("/sessions/revoke-others", response_model=schemas.CountOut)
def revoke_other_sessions(
    request: Request, ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)
):
    count = service.revoke_other_sessions(db, user=ctx.user, session=ctx.session, meta=request_meta(request))
    return {"count": count}


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(current_user),
    db: Session = Depends(get_db),
):
    service.revoke_session(db, user=ctx.user, session_id=session_id, meta=request_meta(request))
    return Response(status_code=204)
