"""Cifrado de credenciales en reposo (Fernet). Clave: env SYNAPSE_CREDENTIALS_KEY."""
from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

ENV_KEY = "SYNAPSE_CREDENTIALS_KEY"
PREFIX = "enc:v1:"


def _fernet() -> Optional[Fernet]:
    raw = (os.environ.get(ENV_KEY) or "").strip()
    if not raw:
        return None
    try:
        key = raw.encode("ascii") if isinstance(raw, str) else raw
        return Fernet(key)
    except Exception:
        return None


def credentials_key_configured() -> bool:
    return _fernet() is not None


def encrypt_secret(plain: str) -> str:
    if plain is None:
        return plain
    f = _fernet()
    if not f:
        raise RuntimeError(
            "Falta SYNAPSE_CREDENTIALS_KEY en el servidor. "
            "Configurá el secret en Fly antes de guardar credenciales."
        )
    token = f.encrypt(plain.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt_secret(stored: str) -> str:
    if not stored:
        return stored
    if not str(stored).startswith(PREFIX):
        # legado en texto plano (migración)
        return stored
    f = _fernet()
    if not f:
        raise RuntimeError("Falta SYNAPSE_CREDENTIALS_KEY para descifrar credenciales.")
    try:
        return f.decrypt(stored[len(PREFIX) :].encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError("No se pudieron descifrar las credenciales (clave incorrecta).") from e


def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return "***"
    user, domain = email.split("@", 1)
    if len(user) <= 1:
        return f"*@{domain}"
    return f"{user[0]}***@{domain}"


def safe_client_error(exc: BaseException, fallback: str = "No se pudo completar la operación.") -> str:
    """Mensaje seguro para el cliente: sin stack ni secretos."""
    msg = str(exc) or fallback
    lowered = msg.lower()
    # si parece contener password / token, no lo devolvemos
    for bad in ("password", "passwd", "token", "bearer", "secret", "authorization"):
        if bad in lowered:
            return fallback
    # limitar longitud
    if len(msg) > 180:
        return fallback
    # errores de conexión conocidos: mensaje corto usable
    if "SYNAPSE_CREDENTIALS_KEY" in msg:
        return "El servidor no tiene configurada la clave de cifrado. Contactá al admin."
    return msg
