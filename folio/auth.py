"""Passcode session authentication for cloud / Railway deployments."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from fastapi import Request

from .db import get_setting, set_setting


def get_active_passcode() -> str:
    env_pc = os.environ.get("FOLIO_PASSCODE", "").strip()
    if env_pc:
        return env_pc
    return get_setting("folio_passcode", "").strip()


def _get_session_secret() -> bytes:
    secret = os.environ.get("FOLIO_SESSION_SECRET", "").strip()
    if not secret:
        secret = get_setting("folio_session_secret", "").strip()
    if not secret:
        secret = secrets.token_hex(32)
        set_setting("folio_session_secret", secret)
    return secret.encode("utf-8")


def make_session_token(passcode: str) -> str:
    exp = int(time.time()) + 30 * 86400
    payload = f"{exp}.{hashlib.sha256(passcode.encode('utf-8')).hexdigest()[:16]}"
    sig = hmac.new(_get_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: str, passcode: str) -> bool:
    if not token or not passcode:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    exp_s, pc_hash, sig = parts
    try:
        if int(exp_s) < int(time.time()):
            return False
    except ValueError:
        return False
    expected_hash = hashlib.sha256(passcode.encode("utf-8")).hexdigest()[:16]
    if not hmac.compare_digest(pc_hash, expected_hash):
        return False
    expected_sig = hmac.new(_get_session_secret(), f"{exp_s}.{pc_hash}".encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected_sig)


def is_request_authenticated(request: Request) -> bool:
    passcode = get_active_passcode()
    if not passcode:
        return True
    tok = request.cookies.get("folio_session", "")
    return bool(tok and verify_session_token(tok, passcode))
