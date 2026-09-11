"""Verificación automática de pagos USDT TRC20 vía TronGrid."""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

# USDT TRC20 oficial en Tron
USDT_TRC20 = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRONGRID = os.environ.get("TRONGRID_API_BASE", "https://api.trongrid.io").rstrip("/")


def payment_address() -> str:
    return (os.environ.get("SYNAPSE_USDT_ADDRESS") or "").strip()


def payment_configured() -> bool:
    addr = payment_address()
    return bool(addr) and addr.startswith("T") and len(addr) >= 30


def license_allowlist() -> set:
    raw = os.environ.get("SYNAPSE_LICENSE_ALLOWLIST") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def base_amount_usdt() -> float:
    try:
        return float(os.environ.get("SYNAPSE_PAYMENT_USDT", "100"))
    except Exception:
        return 100.0


def _trongrid_get(path: str, params: dict = None) -> dict:
    q = urllib.parse.urlencode(params or {})
    url = f"{TRONGRID}{path}"
    if q:
        url = f"{url}?{q}"
    headers = {"Accept": "application/json"}
    key = (os.environ.get("TRONGRID_API_KEY") or "").strip()
    if key:
        headers["TRON-PRO-API-KEY"] = key
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_incoming_usdt(min_timestamp_ms: int = 0, limit: int = 50) -> List[dict]:
    """Lista transferencias TRC20 USDT recibidas en la address de pago."""
    addr = payment_address()
    if not addr:
        return []
    params = {
        "only_to": "true",
        "limit": str(limit),
        "contract_address": USDT_TRC20,
    }
    if min_timestamp_ms:
        params["min_timestamp"] = str(int(min_timestamp_ms))
    data = _trongrid_get(f"/v1/accounts/{addr}/transactions/trc20", params)
    return list(data.get("data") or [])


def tx_to_usdt_amount(tx: dict) -> Optional[float]:
    try:
        raw = tx.get("value") or tx.get("quant") or "0"
        # USDT TRC20: 6 decimals
        return int(raw) / 1_000_000.0
    except Exception:
        return None


def find_matching_payment(amount_usdt: float, created_after_ms: int, tolerance: float = 0.000001) -> Optional[dict]:
    """Busca un inbound USDT que coincida con el monto exacto del intent."""
    txs = list_incoming_usdt(min_timestamp_ms=max(0, created_after_ms - 60_000), limit=80)
    target = round(float(amount_usdt), 6)
    for tx in txs:
        # solo USDT
        caddr = (tx.get("token_info") or {}).get("address") or tx.get("contract_address") or ""
        if caddr and caddr != USDT_TRC20:
            continue
        to_addr = tx.get("to") or ""
        if payment_address() and to_addr and to_addr != payment_address():
            continue
        amt = tx_to_usdt_amount(tx)
        if amt is None:
            continue
        if abs(amt - target) <= tolerance:
            return {
                "txid": tx.get("transaction_id") or tx.get("transactionID") or tx.get("txID"),
                "amount": amt,
                "from": tx.get("from"),
                "to": to_addr,
                "block_timestamp": tx.get("block_timestamp"),
                "raw": {k: tx.get(k) for k in ("transaction_id", "from", "to", "value", "block_timestamp")},
            }
    return None
