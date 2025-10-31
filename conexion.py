# ============================================
# conexion.py (VERSIÓN COMPATIBLE CON API)
# ============================================
from __future__ import annotations
import sys
import json
import time
import os
from typing import Optional
from iqoptionapi.stable_api import IQ_Option


class IQOptionLoginError(Exception):
    pass


def _connect(email: str, password: str, retries: int = 3, backoff: float = 2.0) -> IQ_Option:
    """Conecta a IQ Option con reintentos"""
    iq = IQ_Option(email, password)
    
    last_reason = ""
    for i in range(retries + 1):
        try:
            # Intenta conectar
            check, reason = iq.connect()
            if check:
                # Verificar que realmente está conectado
                time.sleep(1)
                try:
                    # Test de conexión simple
                    iq.get_balance()
                    return iq
                except Exception as e:
                    last_reason = f"Conectado pero no pudo obtener balance: {e}"
                    if i < retries:
                        time.sleep(backoff * (i + 1))
                        continue
            else:
                last_reason = reason or "Login failed"
        except Exception as e:
            last_reason = f"Error de conexión: {e}"
        
        if i < retries:
            time.sleep(backoff * (i + 1))
    
    raise IQOptionLoginError(last_reason)


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