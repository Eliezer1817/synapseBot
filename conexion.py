# ============================================
# conexion.py (VERSIÓN COMPATIBLE CON API)
# ============================================
from __future__ import annotations
import sys
import json
import time
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Optional
from iqoptionapi.stable_api import IQ_Option

# Tope total para auth WSS/SSID; si no llega, no suele llegar después.
CONNECT_TIMEOUT_SEC = float(os.environ.get("SYNAPSE_IQ_CONNECT_TIMEOUT", "12"))

USER_CONNECT_HINT = (
    "No se pudo conectar a IQ Option (revisá demo/real y access key)"
)


class IQOptionLoginError(Exception):
    """Fallo de login/conexión a IQ Option (mensaje seguro para cliente)."""

    def __init__(self, message: str = USER_CONNECT_HINT, code: str = "iq_auth_failed"):
        super().__init__(message)
        self.code = code


class IQOptionConnectTimeout(IQOptionLoginError):
    def __init__(self, message: str = USER_CONNECT_HINT):
        super().__init__(message, code="iq_connect_timeout")


def _connect_once(email: str, password: str) -> IQ_Option:
    """Un intento de connect + verificación de sesión (puede colgarse en WSS)."""
    iq = IQ_Option(email, password)
    check, reason = iq.connect()
    if not check:
        # WSS abrió pero no autenticó / credenciales / demo-real cruzado
        raise IQOptionLoginError(USER_CONNECT_HINT, code="iq_auth_failed")
    time.sleep(0.5)
    try:
        iq.get_balance()
    except Exception:
        raise IQOptionLoginError(USER_CONNECT_HINT, code="iq_auth_failed")
    return iq


def _connect(
    email: str,
    password: str,
    retries: int = 0,
    backoff: float = 1.0,
    timeout_sec: float | None = None,
) -> IQ_Option:
    """Conecta a IQ Option con tope de tiempo (default 12s). No reintenta por defecto:
    un hang de WSS/SSID no se arregla esperando más dentro del mismo request.
    """
    budget = CONNECT_TIMEOUT_SEC if timeout_sec is None else float(timeout_sec)
    attempts = max(0, int(retries)) + 1
    last_exc: BaseException | None = None

    # Un solo worker: el hilo puede quedar zombie si la lib no cancela el socket,
    # pero el caller siempre recibe timeout y puede responder HTTP.
    with ThreadPoolExecutor(max_workers=1) as pool:
        for i in range(attempts):
            remaining = budget
            if remaining <= 0:
                break
            fut = pool.submit(_connect_once, email, password)
            try:
                return fut.result(timeout=remaining)
            except FuturesTimeoutError:
                raise IQOptionConnectTimeout(USER_CONNECT_HINT)
            except IQOptionLoginError as e:
                last_exc = e
                if i < attempts - 1:
                    time.sleep(backoff * (i + 1))
                    budget -= backoff * (i + 1)
                    continue
                raise
            except Exception as e:
                last_exc = e
                if i < attempts - 1:
                    time.sleep(backoff * (i + 1))
                    budget -= backoff * (i + 1)
                    continue
                raise IQOptionLoginError(USER_CONNECT_HINT, code="iq_auth_failed") from e

    if isinstance(last_exc, IQOptionLoginError):
        raise last_exc
    raise IQOptionConnectTimeout(USER_CONNECT_HINT)


def get_real_account_data(email: str, password: str) -> dict:
    """
    Conecta a IQ Option en REAL y devuelve datos reales
    Versión simplificada y compatible
    """
    iq = None
    try:
        # Conectar
        iq = _connect(email, password)
        
        # Cambiar a cuenta REAL
        try:
            iq.change_balance('REAL')
            time.sleep(1)
        except Exception as e:
            print(f"[DEBUG] Advertencia al cambiar a REAL: {e}", file=sys.stderr)

        # Username por defecto
        username = email.split("@")[0]
        user_id = None
        currency = "USD"

        # Balance REAL
        real_balance = 0.0
        try:
            real_balance = float(iq.get_balance())
        except Exception as e:
            print(f"[DEBUG] Error obteniendo balance real: {e}", file=sys.stderr)

        # Balance de PRÁCTICA
        practice_balance = None
        try:
            iq.change_balance('PRACTICE')
            time.sleep(1)
            practice_balance = float(iq.get_balance())
            # Volver a REAL
            iq.change_balance('REAL')
            time.sleep(0.5)
        except Exception as e:
            print(f"[DEBUG] Error obteniendo balance práctica: {e}", file=sys.stderr)

        # IDs de balance (opcional)
        real_id = None
        practice_id = None
        try:
            balances = iq.get_balances()
            if balances:
                for b in balances:
                    if isinstance(b, dict):
                        t_str = (b.get("type_string") or "").upper()
                        t = b.get("type")
                        bal_id = b.get("id") or b.get("balance_id")
                        
                        if t_str == "REAL" or t == 1:
                            real_id = bal_id
                        if t_str == "PRACTICE" or t == 4:
                            practice_id = bal_id
        except Exception as e:
            print(f"[DEBUG] Error obteniendo IDs: {e}", file=sys.stderr)

        return {
            "success": True,
            "data": {
                "user": {
                    "username": username,
                    "email": email,
                    "userId": user_id
                },
                "real": {
                    "balance": real_balance,
                    "accountId": real_id,
                    "currency": currency
                },
                "practice": {
                    "balance": practice_balance,
                    "accountId": practice_id,
                    "currency": currency
                }
            }
        }
    except IQOptionLoginError as e:
        return {
            "success": False,
            "error": f"Error de login: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Error inesperado: {str(e)}"
        }
    finally:
        if iq:
            try:
                iq.api.close()
            except Exception:
                pass


# ---------------------------
# Modo CLI
# ---------------------------
if __name__ == "__main__":
    """
    Lee JSON desde stdin y devuelve resultado a stdout
    """
    if sys.platform == 'win32':
        import locale
        sys.stdin.reconfigure(encoding='utf-8')
        sys.stdout.reconfigure(encoding='utf-8')
    
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
            
        try:
            payload = json.loads(line)
            email = payload.get("email")
            password = payload.get("password")

            if not email or not password:
                result = {
                    "success": False,
                    "error": "Email y password son requeridos"
                }
            else:
                print(f"[DEBUG] Intentando conectar con: {email}", file=sys.stderr)
                result = get_real_account_data(email, password)
                print(f"[DEBUG] Resultado: {result.get('success')}", file=sys.stderr)
            
            print(json.dumps(result), flush=True)
            
        except json.JSONDecodeError as e:
            result = {
                "success": False,
                "error": f"JSON inválido: {str(e)}"
            }
            print(json.dumps(result), flush=True)
        except Exception as e:
            result = {
                "success": False,
                "error": f"Error: {str(e)}"
            }
            print(json.dumps(result), flush=True)
