#!/usr/bin/env python3
"""Tests de Third Candle: patron, sesgo, solo-CALL, fidelidad con el laboratorio,
validaciones de estrategia/temporalidad y cooldown escalado."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import types

import pandas as pd

# Stub de iqoptionapi (la logica de senal es pura; no se conecta a IQ en tests)
_iq = types.ModuleType("iqoptionapi")
_stable = types.ModuleType("iqoptionapi.stable_api")
_stable.IQ_Option = object
_iq.stable_api = _stable
sys.modules.setdefault("iqoptionapi", _iq)
sys.modules.setdefault("iqoptionapi.stable_api", _stable)

import operar

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def mk_df(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def serie_ascendente(n=24, paso=0.0010, cuerpo=0.0009):
    """n velas verdes con cierres crecientes (sesgo claramente alcista)."""
    rows = []
    px = 1.0000
    for _ in range(n):
        op = px
        cl = op + cuerpo + paso * 0.1
        rows.append({"open": op, "high": cl + 0.0002, "low": op - 0.0002, "close": cl})
        px = cl + paso
    return mk_df(rows)


def serie_plana(n=24, cuerpo=0.0004):
    """Velas verdes con cierre ~apenas encima de la apertura: dist < 0.025%."""
    rows = []
    for i in range(n):
        op = 1.0000 + (i % 2) * 0.00005
        cl = op + cuerpo
        rows.append({"open": op, "high": cl + 0.0001, "low": op - 0.0001, "close": cl})
    return mk_df(rows)


def serie_alternada(n=24):
    """50% verdes / 50% rojas, tendencia alcista: falla solo el 58% de verdes."""
    rows = []
    px = 1.0000
    for i in range(n):
        op = px
        if i % 2 == 0:  # verde
            cl = op + 0.0020
            px = cl + 0.0010
        else:           # roja
            cl = op - 0.0005
            px = cl
        rows.append({"open": op, "high": max(op, cl) + 0.0002, "low": min(op, cl) - 0.0002, "close": cl})
    return mk_df(rows)


# ------------------------------------------------------------------
# 1. Patron completo -> CALL
# ------------------------------------------------------------------
res = operar.predecir_decision_third_candle(serie_ascendente())
assert res["decision"] == "CALL", res
assert res["tipo"] == "call", res
assert res["score_call"] == 1 and res["score_put"] == 0
print("OK 1: dos verdes + 2a sobre 1a + sesgo alcista -> CALL")

# ------------------------------------------------------------------
# 2. Nunca PUT (estrategia exclusivamente alcista)
# ------------------------------------------------------------------
bajista = []
px = 1.5000
for _ in range(24):  # todas rojas, cayendo
    op = px
    cl = op - 0.0012
    bajista.append({"open": op, "high": op + 0.0001, "low": cl - 0.0001, "close": cl})
    px = cl
res = operar.predecir_decision_third_candle(mk_df(bajista))
assert res["decision"] == "SKIP" and res["tipo"] is None, res
print("OK 2: series bajistas -> SKIP, nunca PUT")

# ------------------------------------------------------------------
# 3. La 2a no cierra sobre la 1a -> SKIP
# ------------------------------------------------------------------
s = serie_ascendente()
rows = s.to_dict("records")
a, b = rows[-2], rows[-1]
b["close"] = a["close"] - 0.0001   # verde pero cierra debajo del cierre de la 1a
b["open"] = b["close"] - 0.0005
rows[-2], rows[-1] = a, b
res = operar.predecir_decision_third_candle(mk_df(rows))
assert res["decision"] == "SKIP" and "2a" in res["razon"], res
print("OK 3: 2a no cierra sobre la 1a -> SKIP")

# ------------------------------------------------------------------
# 4. Distancia < 0.025% sobre la SMA -> SKIP (sesgo no alcista)
# ------------------------------------------------------------------
res = operar.predecir_decision_third_candle(serie_plana())
assert res["decision"] == "SKIP" and "sesgo" in res["razon"], res
sesgo, det = operar.sesgo_third_candle(serie_plana())
assert sesgo == "neutral", (sesgo, det)
assert det["dist_pct"] < 0.025, det
print(f"OK 4: dist {det['dist_pct']:.4f}% < 0.025% -> sesgo neutral -> SKIP")

# ------------------------------------------------------------------
# 5. Menos de 58% de verdes -> SKIP
# ------------------------------------------------------------------
res = operar.predecir_decision_third_candle(serie_alternada())
assert res["decision"] == "SKIP" and "sesgo" in res["razon"], res
sesgo, det = operar.sesgo_third_candle(serie_alternada())
assert det["verdes_pct"] < 58.0, det
assert det["dist_pct"] >= 0.025, det  # la distancia si pasa: solo falla el % de verdes
print(f"OK 5: verdes {det['verdes_pct']:.0f}% < 58% -> sesgo neutral -> SKIP")

# ------------------------------------------------------------------
# 6. Bloque insuficiente (< 12) -> SKIP
# ------------------------------------------------------------------
res = operar.predecir_decision_third_candle(serie_ascendente(10))
assert res["decision"] == "SKIP" and "insuficiente" in res["razon"], res
sesgo, det = operar.sesgo_third_candle(serie_ascendente(11))
assert sesgo == "neutral" and "Bloque insuficiente" in det["razon"], det
print("OK 6: menos de 12 velas -> neutral/SKIP")

# ------------------------------------------------------------------
# 7. Bloque configurable: clamp 12..24
# ------------------------------------------------------------------
assert operar._normalizar_bloque(5) == 12
assert operar._normalizar_bloque(50) == 24
assert operar._normalizar_bloque(None) == 24
assert operar._normalizar_bloque("18") == 18
assert operar._normalizar_bloque(24) == 24
sesgo, det = operar.sesgo_third_candle(serie_ascendente(30), bloque=14)
assert det["bloque"] == 14, det
print("OK 7: bloque clamped a [12, 24] y configurable")

# ------------------------------------------------------------------
# 8. Fidelidad con el laboratorio (lab.js): 500 series aleatorias
# ------------------------------------------------------------------
import random
random.seed(20260915)

def lab_isGreen(c):
    return c["close"] >= c["open"]

def lab_marketBias(cands, i):
    look = min(24, i)
    if look < 12:
        return "neutral"
    sl = cands[i - look:i]
    sma = sum(c["close"] for c in sl) / len(sl)
    px = cands[i - 1]["close"]
    greens = sum(1 for c in sl if lab_isGreen(c)) / len(sl)
    dist = (px - sma) / sma
    if px > sma and dist >= 0.00025 and greens >= 0.58:
        return "alcista"
    if px < sma and dist <= -0.00025 and greens <= 0.42:
        return "bajista"
    return "neutral"

def lab_analyzeThird(cands):
    """Devuelve True si el lab daria senal CALL en la vela de entrada i=len-1."""
    i = len(cands) - 1
    a, b = cands[i - 2], cands[i - 1]
    if not (lab_isGreen(a) and lab_isGreen(b) and b["close"] > a["close"]):
        return False
    return lab_marketBias(cands, i) == "alcista"

mismatches = 0
for t in range(500):
    n = random.randint(14, 40)
    cands = []
    px = 1.0
    for _ in range(n):
        op = px
        cl = op + random.uniform(-0.0012, 0.0012)
        cands.append({"open": op, "high": max(op, cl) + 0.0002, "low": min(op, cl) - 0.0002, "close": cl})
        px = cl
    # cands = completas + vela de entrada; el lab mira a=cands[-3], b=cands[-2]
    entrada = cands.pop()
    df_completas = mk_df(cands)
    lab_signal = lab_analyzeThird(cands + [entrada])
    py = operar.predecir_decision_third_candle(df_completas)
    if lab_signal != (py["decision"] == "CALL"):
        mismatches += 1
        print(f"  mismatch t={t}: lab={lab_signal} py={py['decision']} razon={py['razon']}")
assert mismatches == 0, f"{mismatches} desacuerdos con el laboratorio"
print("OK 8: fidelidad 100% con lab.js en 500 series aleatorias (senal CALL identica)")

# ------------------------------------------------------------------
# 9. Validaciones de ejecutar_operacion (sin IQ)
# ------------------------------------------------------------------
try:
    operar.ejecutar_operacion(None, estrategia="inexistente")
    raise AssertionError("debio rechazar estrategia desconocida")
except ValueError as e:
    assert "desconocida" in str(e), str(e)
try:
    operar.ejecutar_operacion(None, estrategia="third_candle", timeframe=99)
    raise AssertionError("debio rechazar temporalidad invalida")
except ValueError as e:
    assert "Temporalidad" in str(e), str(e)
res = operar.ejecutar_operacion(None, estrategia="third_candle", timeframe=60, bloque_velas=3)
assert res["success"] is False and res["decision"] == "ERROR" and "IQ Option" in res["razon"], res
assert res["estrategia"] if "estrategia" in res else True
print("OK 9: estrategia invalida, temporalidad invalida y sin sesion IQ rechazadas (60/300 unicas)")

# ------------------------------------------------------------------
# 10. Cooldown escalado a la temporalidad
# ------------------------------------------------------------------
g = operar.GestorRiesgoInteligente({"cooldown_after_loss_sec": 60})
g.last_loss_ts = __import__("time").time() - 30
ok, motivo = g.puede_operar(1000)
assert not ok and "Cooldown" in motivo, motivo
import re as _re
_seg = int(_re.search(r"\((\d+)s\)", motivo).group(1))
assert 25 <= _seg <= 30, f"cooldown restante fuera de rango: {_seg}s"  # 60s de TF, pasaron ~30s
g2 = operar.GestorRiesgoInteligente({"cooldown_after_loss_sec": 60})
g2.last_loss_ts = __import__("time").time() - 90
ok, motivo = g2.puede_operar(1000)
assert ok, motivo
print("OK 10: cooldown post-perdida respeta la temporalidad (60s en 1m)")

# ------------------------------------------------------------------
# 11. Resto de controles de riesgo intactos (stop loss diario / racha / monto max)
# ------------------------------------------------------------------
g3 = operar.GestorRiesgoInteligente({"max_perdidas_consecutivas": 2, "stop_loss_diario": 5, "monto_maximo": 10})
g3.actualizar_resultado(-1)
g3.actualizar_resultado(-1)
ok, motivo = g3.puede_operar(1000)
assert not ok and "consecutivas" in motivo, motivo
m = g3.calcular_monto_operacion(100000)
assert m <= 10, m
print("OK 11: stop de racha y monto maximo siguen operando igual")

print("\nTODOS LOS TESTS DE THIRD CANDLE PASARON")
