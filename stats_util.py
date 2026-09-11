"""Estadísticas reales a partir del historial en trading_data.json."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _ts(op: dict) -> float:
    t = op.get("timestamp")
    if isinstance(t, (int, float)):
        return float(t)
    rt = op.get("resultado_trade") or {}
    if isinstance(rt.get("timestamp"), (int, float)):
        return float(rt["timestamp"])
    return 0.0


def _score(op: dict) -> Optional[float]:
    raw = op.get("probabilidad")
    if raw is None:
        return None
    try:
        s = str(raw).replace("%", "").strip()
        if s.upper() == "N/A":
            return None
        return float(s)
    except Exception:
        return None


def _extract_closed(op: dict) -> Optional[dict]:
    """Devuelve dict normalizado si la op cerró con PnL conocido."""
    if not op:
        return None
    if isinstance(op.get("resultado"), dict) and (
        op["resultado"].get("resultado_trade") or op["resultado"].get("ejecutado") or op["resultado"].get("decision")
    ):
        # a veces se guardó el wrapper del bot
        inner = op["resultado"]
        got = _extract_closed(inner)
        if got:
            return got
    decision = (op.get("decision") or "").upper()
    # SKIP / errores sin trade no cuentan como operación cerrada
    rt = op.get("resultado_trade") or op.get("resultado") or {}
    if isinstance(op.get("resultado"), dict) and "ganancia" in (op.get("resultado") or {}):
        rt = op["resultado"]
    if not op.get("ejecutado") and not rt.get("finalizada"):
        return None
    if not rt.get("finalizada") and op.get("ganancia") is None and not isinstance(rt.get("ganancia"), (int, float)):
        # a veces solo trade_id sin resultado aún
        return None
    ganancia = rt.get("ganancia")
    if ganancia is None:
        ganancia = op.get("ganancia")
    if ganancia is None:
        return None
    try:
        pnl = float(ganancia)
    except Exception:
        return None
    win = rt.get("win")
    if win is None:
        win = pnl > 0
    score = _score(op)
    ts = _ts(op)
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour if ts else None
    return {
        "decision": decision or "—",
        "pnl": pnl,
        "win": bool(win) and pnl > 0,
        "loss": pnl < 0,
        "even": pnl == 0,
        "score": score,
        "ts": ts,
        "hour": hour,
        "trade_id": op.get("trade_id") or rt.get("id"),
        "modo": (op.get("modo") or "").lower(),
    }


def compute_stats(operaciones: List[dict]) -> Dict[str, Any]:
    closed = []
    skips = 0
    analizados = 0
    for op in operaciones or []:
        # unwrap if stored as {resultado: {...}}
        raw = op.get('resultado') if isinstance(op.get('resultado'), dict) and 'decision' in op.get('resultado', {}) else op
        analizados += 1
        decision = (raw.get('decision') or op.get('decision') or '').upper()
        if decision == 'SKIP' or (not raw.get('ejecutado') and 'CALL' not in decision and 'PUT' not in decision):
            if decision == 'SKIP' or raw.get('ejecutado') is False:
                skips += 1
        n = _extract_closed(raw)
        if not n:
            n = _extract_closed(op)
        if n:
            closed.append(n)
    closed.sort(key=lambda x: x["ts"])

    n = len(closed)
    wins = [c for c in closed if c["win"]]
    losses = [c for c in closed if c["loss"]]
    evens = [c for c in closed if c["even"]]
    pnl_list = [c["pnl"] for c in closed]
    total_pnl = sum(pnl_list) if pnl_list else 0.0
    gross_profit = sum(p for p in pnl_list if p > 0)
    gross_loss = abs(sum(p for p in pnl_list if p < 0))
    win_rate = (len(wins) / n * 100.0) if n else 0.0
    expectancy = (total_pnl / n) if n else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    # Drawdown sobre curva de equity
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    max_dd_pct = 0.0
    curve = []
    for p in pnl_list:
        equity += p
        curve.append(round(equity, 4))
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
        if peak > 0:
            max_dd_pct = max(max_dd_pct, dd / peak * 100.0)

    # Rachas
    cur_w = cur_l = max_w = max_l = 0
    for c in closed:
        if c["win"]:
            cur_w += 1
            cur_l = 0
            max_w = max(max_w, cur_w)
        elif c["loss"]:
            cur_l += 1
            cur_w = 0
            max_l = max(max_l, cur_l)
        else:
            cur_w = cur_l = 0
    # racha actual al final
    streak_type = "none"
    streak_len = 0
    if closed:
        if closed[-1]["win"]:
            streak_type, streak_len = "win", cur_w
        elif closed[-1]["loss"]:
            streak_type, streak_len = "loss", cur_l

    by_hour = {str(h): {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0} for h in range(24)}
    for c in closed:
        if c["hour"] is None:
            continue
        k = str(c["hour"])
        by_hour[k]["n"] += 1
        by_hour[k]["pnl"] = round(by_hour[k]["pnl"] + c["pnl"], 4)
        if c["win"]:
            by_hour[k]["wins"] += 1
        elif c["loss"]:
            by_hour[k]["losses"] += 1

    # Buckets de score
    buckets = {
        "n/a": {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0},
        "0.00-0.50": {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0},
        "0.50-0.75": {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0},
        "0.75-1.00": {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0},
    }
    for c in closed:
        s = c["score"]
        if s is None:
            b = "n/a"
        elif s < 0.5:
            b = "0.00-0.50"
        elif s < 0.75:
            b = "0.50-0.75"
        else:
            b = "0.75-1.00"
        buckets[b]["n"] += 1
        buckets[b]["pnl"] = round(buckets[b]["pnl"] + c["pnl"], 4)
        if c["win"]:
            buckets[b]["wins"] += 1
        elif c["loss"]:
            buckets[b]["losses"] += 1

    by_side = {"CALL": {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0}, "PUT": {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0}}
    for c in closed:
        d = "CALL" if "CALL" in c["decision"] else ("PUT" if "PUT" in c["decision"] else None)
        if not d:
            continue
        by_side[d]["n"] += 1
        by_side[d]["pnl"] = round(by_side[d]["pnl"] + c["pnl"], 4)
        if c["win"]:
            by_side[d]["wins"] += 1
        elif c["loss"]:
            by_side[d]["losses"] += 1

    pf_out = profit_factor if profit_factor != float("inf") else None

    return {
        "operaciones": n,
        "analizados": analizados,
        "skips": skips,
        "wins": len(wins),
        "losses": len(losses),
        "evens": len(evens),
        "win_rate": round(win_rate, 2),
        "profit_loss": round(total_pnl, 4),
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "expectancy": round(expectancy, 4),
        "profit_factor": round(pf_out, 4) if pf_out is not None else None,
        "profit_factor_infinite": profit_factor == float("inf"),
        "max_drawdown": round(max_dd, 4),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "racha_win_max": max_w,
        "racha_loss_max": max_l,
        "racha_actual_tipo": streak_type,
        "racha_actual_len": streak_len,
        "equity_curve": curve[-50:],
        "por_hora": by_hour,
        "por_score": buckets,
        "por_senal": by_side,
        "closed_trades": [
            {
                "decision": c["decision"],
                "pnl": c["pnl"],
                "win": c["win"],
                "score": c["score"],
                "ts": c["ts"],
                "hour": c["hour"],
                "trade_id": c["trade_id"],
                "modo": c["modo"],
            }
            for c in reversed(closed[-100:])
        ],
    }
