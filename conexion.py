from __future__ import annotations
import sys
import json
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Optional
from iqoptionapi.stable_api import IQ_Option

# Tope total para auth WSS/SSID; si no llega, no suele llegar después.
CONNECT_TIMEOUT_SEC = float(os.environ.get("SYNAPSE_IQ_CONNECT_TIMEOUT", "12"))

USER_CONNECT_HINT = (
    "No se pudo conectar a IQ Option (revisá email y contraseña de la cuenta)"
)
USER_TIMEOUT_HINT = (
    "IQ Option no respondió a tiempo. Probá de nuevo; si sigue fallando, intentá más tarde."
)


class IQOptionLoginError(Exception):
    """Fallo de login/conexión a IQ Option (mensaje seguro para cliente)."""

    def __init__(self, message: str = USER_CONNECT_HINT, code: str = "iq_auth_failed"):
        super().__init__(message)
        self.code = code


class IQOptionConnectTimeout(IQOptionLoginError):
    def __init__(self, message: str = USER_TIMEOUT_HINT):
        super().__init__(message, code="iq_connect_timeout")


def _force_close_iq(iq: Optional[IQ_Option]) -> None:
    """Cierra el WSS al timeout para no dejar hilos zombie.

    `api.close()` hace `websocket_thread.join()` sin tope; por eso cerramos el
    socket primero y solo intentamos `close()` en un hilo daemon con join corto.
    """
    if iq is None:
        return
    api = getattr(iq, "api", None)
    if api is None:
        return

    ws = getattr(api, "websocket", None)
    if ws is not None:
        try:
            ws.keep_running = False
        except Exception:
            pass
        try:
            ws.close()
        except Exception:
            pass

    def _api_close() -> None:
        try:
            api.close()
        except Exception:
            pass

    t = threading.Thread(target=_api_close, name="iq-wss-force-close", daemon=True)
    t.start()
    t.join(timeout=2.0)


def _connect_once(email: str, password: str, holder: dict) -> IQ_Option:
    """Un intento de connect + verificación de sesión (puede colgarse en WSS)."""
    iq = IQ_Option(email, password)
    holder["iq"] = iq
    check, reason = iq.connect()
    if not check:
        _force_close_iq(iq)
        raise IQOptionLoginError(USER_CONNECT_HINT, code="iq_auth_failed")
    time.sleep(0.5)
    try:
        iq.get_balance()
    except Exception:
        _force_close_iq(iq)
        raise IQOptionLoginError(USER_CONNECT_HINT, code="iq_auth_failed")
    return iq


def _connect(
    email: str,
    password: str,
    retries: int = 0,
    backoff: float = 1.0,
    timeout_sec: float | None = None,
) -> IQ_Option:
    """Conecta a IQ Option con tope de tiempo (default 12s). Al timeout force-close del WSS.
    """
    budget = CONNECT_TIMEOUT_SEC if timeout_sec is None else float(timeout_sec)
    attempts = max(0, int(retries)) + 1
    last_exc: BaseException | None = None

    with ThreadPoolExecutor(max_workers=1) as pool:
        for i in range(attempts):
            remaining = budget
            if remaining <= 0:
                break
            holder: dict = {"iq": None}
            fut = pool.submit(_connect_once, email, password, holder)
            try:
                return fut.result(timeout=remaining)
            except FuturesTimeoutError:
                _force_close_iq(holder.get("iq"))
                raise IQOptionConnectTimeout(USER_TIMEOUT_HINT)
            except IQOptionLoginError as e:
                last_exc = e
                if i < attempts - 1:
                    time.sleep(backoff * (i + 1))
                    budget -= backoff * (i + 1)
                    continue
                raise
            except Exception as e:
                last_exc = e
                _force_close_iq(holder.get("iq"))
                if i < attempts - 1:
                    time.sleep(backoff * (i + 1))
                    budget -= backoff * (i + 1)
                    continue
                raise IQOptionLoginError(USER_CONNECT_HINT, code="iq_auth_failed") from e

    if isinstance(last_exc, IQOptionLoginError):
        raise last_exc
    raise IQOptionConnectTimeout(USER_TIMEOUT_HINT)


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
