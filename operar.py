import os
import sys
import time
from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timezone

import pandas as pd
from iqoptionapi.stable_api import IQ_Option
from ta.trend import EMAIndicator, MACD
from ta.volatility import BollingerBands

# ====================================================================
# CONFIGURACION DE ESTRATEGIA (EMA9/21 + MACD + Bollinger)
# ====================================================================
ACTIVO = "EURUSD-OTC"
TIMEFRAME_SECONDS = 300
CANDLES_HISTORY = 120
EXPIRATION_TIME = 5
DEFAULT_AMOUNT = 1

EMA_FAST = 9
EMA_SLOW = 21
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BB_PERIOD = 20
BB_STD = 2
MIN_BODY_RATIO = 0.50
BB_SQUEEZE_RATIO = 0.70
LOW_LIQUIDITY_HOURS_UTC = {21, 22, 23, 0}
COOLDOWN_AFTER_LOSS_SEC = TIMEFRAME_SECONDS

# ====================================================================
# ESTRATEGIAS SELECCIONABLES (EUR/USD OTC unicamente)
# ====================================================================
ESTRATEGIAS = ("original", "third_candle")
TIMEFRAMES_VALIDOS = {60, 300}

# --- Third Candle: logica exacta del laboratorio (solo CALL) ---
TC_BLOQUE_MIN = 12        # bloque minimo de velas del lab
TC_BLOQUE_MAX = 24        # bloque maximo de velas del lab
TC_BLOQUE_DEFAULT = 24    # como en el lab: look = min(24, i)
TC_DIST_MIN = 0.00025     # precio >= 0.025% por encima de la SMA del bloque
TC_GREENS_MIN = 0.58      # minimo 58% de velas verdes en el bloque

def _chaos_kill(point: str) -> None:
    """Si hay chaos arm one-shot en este point, mata el proceso (Fly reinicia)."""
    try:
        import database as _db
        if _db.consume_chaos_arm(point):
            print(f"CHAOS: kill deliberado en {point}", file=sys.stderr)
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(78)
    except Exception as e:
        print(f"CHAOS arm check falló: {e}", file=sys.stderr)


def candle_open_unix(ts: float = None, timeframe: int = TIMEFRAME_SECONDS) -> int:
    """Inicio de la vela actual (unix floor al timeframe)."""
    t = time.time() if ts is None else float(ts)
    return int(t // timeframe) * timeframe


FEATURES = [
    "ema_9", "ema_21", "macd", "macd_signal", "macd_hist",
    "bb_high", "bb_mid", "bb_low", "bb_width", "body_ratio",
]


class GestorRiesgoInteligente:
    def __init__(self, config_riesgo=None):
        self.config = {
            "riesgo_porcentaje": 2.0,
            "max_perdidas_consecutivas": 4,
            "stop_loss_diario": 5.0,
            "monto_maximo": 10,
            "cooldown_after_loss_sec": COOLDOWN_AFTER_LOSS_SEC,
        }
        if config_riesgo:
            self.config.update(config_riesgo)
        self.racha_perdidas = 0
        self.racha_ganancias = 0
        self.profit_diario = 0.0
        self.operaciones_hoy = 0
        self.dia = datetime.now(timezone.utc).date()
        self.last_loss_ts = 0.0
        self.bloqueado = False
        self.motivo_bloqueo = ""

    def _reset_si_cambio_dia(self):
        hoy = datetime.now(timezone.utc).date()
        if hoy != self.dia:
            self.dia = hoy
            self.racha_perdidas = 0
            self.racha_ganancias = 0
            self.profit_diario = 0.0
            self.operaciones_hoy = 0
            self.bloqueado = False
            self.motivo_bloqueo = ""

    def puede_operar(self, balance_actual: float) -> Tuple[bool, str]:
        self._reset_si_cambio_dia()
        max_loss = self.config.get("max_perdidas_consecutivas", 4)
        if self.racha_perdidas >= max_loss:
            self.bloqueado = True
            self.motivo_bloqueo = f"Limite de {max_loss} perdidas consecutivas"
            return False, self.motivo_bloqueo

        stop_pct = self.config.get("stop_loss_diario", 5.0)
        stop_abs = abs(balance_actual) * (stop_pct / 100.0)
        if self.profit_diario <= -stop_abs and self.operaciones_hoy > 0:
            self.bloqueado = True
            self.motivo_bloqueo = f"Stop loss diario ({stop_pct}% / ${stop_abs:.2f})"
            return False, self.motivo_bloqueo

        cooldown = float(self.config.get("cooldown_after_loss_sec", COOLDOWN_AFTER_LOSS_SEC))
        if time.time() - self.last_loss_ts < cooldown:
            restante = int(cooldown - (time.time() - self.last_loss_ts))
            return False, f"Cooldown post-perdida ({restante}s)"

        return True, ""

    def calcular_monto_operacion(self, balance_actual, senal_calidad="normal"):
        self._reset_si_cambio_dia()
        try:
            ok, motivo = self.puede_operar(balance_actual)
            if not ok and "Cooldown" not in motivo:
                print(f"STOP {motivo}", file=sys.stderr)
                return 0

            riesgo = float(self.config.get("riesgo_porcentaje", 2.0))
            riesgo = max(1.0, min(2.0, riesgo))
            monto = balance_actual * (riesgo / 100.0)
            monto = max(1.0, min(monto, self.config.get("monto_maximo", 10)))
            print(f"Monto fijo: ${monto:.2f} ({riesgo:.1f}% de ${balance_actual:.2f})", file=sys.stderr)
            return round(monto, 2)
        except Exception as e:
            print(f"Error calculando monto: {e}", file=sys.stderr)
            return DEFAULT_AMOUNT

    def actualizar_resultado(self, ganancia):
        self._reset_si_cambio_dia()
        if ganancia > 0:
            self.racha_ganancias += 1
            self.racha_perdidas = 0
        elif ganancia < 0:
            self.racha_perdidas += 1
            self.racha_ganancias = 0
            self.last_loss_ts = time.time()
        self.profit_diario += ganancia
        self.operaciones_hoy += 1
        print(
            f"Racha P:{self.racha_perdidas} G:{self.racha_ganancias} | P&L dia: ${self.profit_diario:.2f}",
            file=sys.stderr,
        )

    def obtener_estadisticas(self):
        return {
            "racha_actual": self.racha_ganancias if self.racha_ganancias > 0 else -self.racha_perdidas,
            "profit_diario": self.profit_diario,
            "operaciones_hoy": self.operaciones_hoy,
            "racha_perdidas": self.racha_perdidas,
            "racha_ganancias": self.racha_ganancias,
            "bloqueado": self.bloqueado,
            "motivo_bloqueo": self.motivo_bloqueo,
        }


_gestor_global = None


def _obtener_gestor(config_riesgo=None):
    global _gestor_global
    if _gestor_global is None:
        _gestor_global = GestorRiesgoInteligente(config_riesgo)
    elif config_riesgo:
        _gestor_global.config.update(config_riesgo)
    return _gestor_global


def get_latest_market_data(iq: IQ_Option, timeframe: int = TIMEFRAME_SECONDS) -> pd.DataFrame:
    if not iq:
        raise ValueError("La sesion de IQ Option no es valida.")

    print(f"Obteniendo velas de {ACTIVO} ({timeframe // 60}min)...", file=sys.stderr)
    candles = iq.get_candles(ACTIVO, timeframe, CANDLES_HISTORY, time.time())
    if not candles:
        raise RuntimeError(f"No se pudieron obtener velas de {ACTIVO}.")

    df = pd.DataFrame(candles)
    column_mapping = {"open": "open", "max": "high", "min": "low", "close": "close", "volume": "volume"}
    existing = {src: dst for src, dst in column_mapping.items() if src in df.columns}
    df = df.rename(columns=existing)

    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"Faltan columnas: {missing}. Disponibles: {list(df.columns)}")

    if "volume" not in df.columns:
        df["volume"] = 0

    print(f"Se obtuvieron {len(df)} velas", file=sys.stderr)
    return df


def calcular_features(df: pd.DataFrame) -> pd.DataFrame:
    print("Calculando indicadores EMA9/21 + MACD + Bollinger...", file=sys.stderr)
    out = df.copy()

    out["ema_9"] = EMAIndicator(out["close"], window=EMA_FAST).ema_indicator()
    out["ema_21"] = EMAIndicator(out["close"], window=EMA_SLOW).ema_indicator()

    macd = MACD(
        out["close"],
        window_slow=MACD_SLOW,
        window_fast=MACD_FAST,
        window_sign=MACD_SIGNAL,
    )
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_hist"] = macd.macd_diff()

    boll = BollingerBands(out["close"], window=BB_PERIOD, window_dev=BB_STD)
    out["bb_high"] = boll.bollinger_hband()
    out["bb_mid"] = boll.bollinger_mavg()
    out["bb_low"] = boll.bollinger_lband()
    out["bb_width"] = (out["bb_high"] - out["bb_low"]) / out["close"]
    out["bb_width_ma20"] = out["bb_width"].rolling(BB_PERIOD).mean()

    rango = (out["high"] - out["low"]).replace(0, pd.NA)
    out["body_ratio"] = (out["close"] - out["open"]).abs() / rango
    out["body_ratio"] = out["body_ratio"].fillna(0)

    out = out.dropna().reset_index(drop=True)
    print(f"Indicadores listos ({len(out)} velas validas).", file=sys.stderr)
    return out


def _vela_confirmacion_alcista(row) -> bool:
    return row["close"] > row["open"] and row["body_ratio"] >= MIN_BODY_RATIO


def _vela_confirmacion_bajista(row) -> bool:
    return row["close"] < row["open"] and row["body_ratio"] >= MIN_BODY_RATIO


def _macd_cruce_alcista(prev, curr) -> bool:
    cruce_lineas = prev["macd"] <= prev["macd_signal"] and curr["macd"] > curr["macd_signal"]
    hist_pos = prev["macd_hist"] <= 0 and curr["macd_hist"] > 0
    return bool(cruce_lineas or hist_pos)


def _macd_cruce_bajista(prev, curr) -> bool:
    cruce_lineas = prev["macd"] >= prev["macd_signal"] and curr["macd"] < curr["macd_signal"]
    hist_neg = prev["macd_hist"] >= 0 and curr["macd_hist"] < 0
    return bool(cruce_lineas or hist_neg)


def _bb_impulso_alcista(prev, curr) -> bool:
    ruptura = curr["close"] >= curr["bb_high"] or curr["high"] >= curr["bb_high"]
    rebote = prev["close"] <= prev["bb_mid"] and curr["close"] > curr["bb_mid"] and curr["close"] > curr["open"]
    return bool(ruptura or rebote)


def _bb_impulso_bajista(prev, curr) -> bool:
    ruptura = curr["close"] <= curr["bb_low"] or curr["low"] <= curr["bb_low"]
    rebote = prev["close"] >= prev["bb_mid"] and curr["close"] < curr["bb_mid"] and curr["close"] < curr["open"]
    return bool(ruptura or rebote)


def _sesion_baja_liquidez() -> bool:
    hora = datetime.now(timezone.utc).hour
    return hora in LOW_LIQUIDITY_HOURS_UTC


# ====================================================================
# THIRD CANDLE - replica de la logica del laboratorio (lab.js)
# analyzeThird: 2 velas verdes, la 2a cierra sobre la 1a, sesgo alcista
# -> CALL al abrir la 3a vela. Exclusivamente alcista: nunca PUT.
# ====================================================================
def _es_vela_verde(row) -> bool:
    """Verde como en el laboratorio: close >= open."""
    return float(row["close"]) >= float(row["open"])


def _normalizar_bloque(bloque) -> int:
    """Bloque configurable del laboratorio: minimo 12, maximo 24."""
    try:
        bloque = int(bloque)
    except (TypeError, ValueError):
        bloque = TC_BLOQUE_DEFAULT
    return max(TC_BLOQUE_MIN, min(TC_BLOQUE_MAX, bloque))


def sesgo_third_candle(df_completas, bloque=TC_BLOQUE_DEFAULT):
    """Sesgo del laboratorio sobre el bloque de velas completas.

    Reproduce marketBias() del lab: look = min(bloque, velas disponibles);
    con menos de 12 velas -> neutral. Alcista si: px > SMA del bloque,
    (px - SMA)/SMA >= 0.025% y proporcion de velas verdes >= 58%.
    Devuelve (sesgo, detalle).
    """
    bloque = _normalizar_bloque(bloque)
    n = len(df_completas)
    look = min(bloque, n)
    detalle = {"bloque": look, "bloque_min": TC_BLOQUE_MIN}
    if look < TC_BLOQUE_MIN:
        return "neutral", {**detalle, "razon": f"Bloque insuficiente ({look} < {TC_BLOQUE_MIN} velas)"}

    sma = float(df_completas["close"].iloc[-look:].mean())
    px = float(df_completas.iloc[-1]["close"])
    verdes = float((df_completas["close"].iloc[-look:] >= df_completas["open"].iloc[-look:]).mean())
    dist = ((px - sma) / sma) if sma else 0.0
    detalle.update({"px": px, "sma": sma, "dist_pct": dist * 100.0, "verdes_pct": verdes * 100.0})

    if px > sma and dist >= TC_DIST_MIN and verdes >= TC_GREENS_MIN:
        return "alcista", detalle
    return "neutral", detalle


def predecir_decision_third_candle(df_completas, bloque=TC_BLOQUE_DEFAULT, forzar: bool = False) -> Dict[str, Any]:
    """Third Candle (laboratorio): 2 verdes, 2a cierra sobre la 1a, sesgo alcista -> CALL en la 3a.

    df_completas: solo velas COMPLETAS (la ultima es la 2a vela, la confirmacion).
    La entrada es al abrir la 3a vela; la senal se evalua al cierre de esa vela
    y solo es acierto si el cierre supera la apertura. Solo CALL, nunca PUT.
    """
    base = {
        "score": 0,
        "score_call": 0,
        "score_put": 0,
        "lado_hipotetico": None,
        "componentes": {},
        "precio_entrada": None,
        "detalle": "",
    }
    if df_completas is None or getattr(df_completas, "empty", True) or len(df_completas) < TC_BLOQUE_MIN:
        return {**base, "decision": "SKIP", "razon": "TC: historico insuficiente (<12 velas)", "probabilidad": "N/A", "tipo": None}

    a = df_completas.iloc[-2]  # primera vela del patron
    b = df_completas.iloc[-1]  # segunda vela (confirmacion)
    precio = float(b["close"])

    patron = _es_vela_verde(a) and _es_vela_verde(b)
    segunda_sobre_primera = patron and float(b["close"]) > float(a["close"])
    sesgo, detalle_sesgo = sesgo_third_candle(df_completas, bloque=bloque)

    componentes = {
        "patron": "bullish" if segunda_sobre_primera else "neutral",
        "sesgo": sesgo,
        "bloque": detalle_sesgo.get("bloque"),
        "verdes_pct": round(detalle_sesgo.get("verdes_pct", 0.0), 1),
        "dist_pct": round(detalle_sesgo.get("dist_pct", 0.0), 4),
    }
    detalle = (
        f"TC: {'2 verdes' if patron else 'sin patron de 2 verdes'} | 2a "
        f"{'>' if segunda_sobre_primera else '<='} 1a | sesgo {sesgo} "
        f"(bloque {detalle_sesgo.get('bloque')}, dist {detalle_sesgo.get('dist_pct', 0.0):.3f}%, "
        f"verdes {detalle_sesgo.get('verdes_pct', 0.0):.1f}%)"
    )
    base.update({"componentes": componentes, "precio_entrada": precio, "detalle": detalle})

    if not patron:
        return {**base, "decision": "SKIP", "razon": f"TC: sin patron de 2 verdes - {detalle}", "probabilidad": "N/A", "tipo": None}
    if not segunda_sobre_primera:
        return {**base, "decision": "SKIP", "razon": f"TC: la 2a no cierra sobre la 1a - {detalle}", "probabilidad": "N/A", "tipo": None}
    if sesgo != "alcista":
        return {**base, "decision": "SKIP", "razon": f"TC: sesgo {sesgo} (no alcista) - {detalle}", "probabilidad": "N/A", "tipo": None}

    return {
        **base,
        "decision": "CALL",
        "razon": f"TC CALL: 2 verdes + 2a sobre 1a + sesgo alcista - {detalle}",
        "probabilidad": "1.0000",
        "tipo": "call",
        "score": 1,
        "score_call": 1,
        "lado_hipotetico": "call",
    }


def predecir_decision(model_or_df, df_vela_actual=None, forzar: bool = False) -> Dict[str, Any]:
    if df_vela_actual is None:
        df = model_or_df
    else:
        df = df_vela_actual

    vacio = {
        "decision": "SKIP",
        "razon": "No hay datos",
        "probabilidad": "N/A",
        "tipo": None,
        "score": 0,
        "score_call": 0,
        "score_put": 0,
        "lado_hipotetico": None,
        "componentes": {},
        "precio_entrada": None,
    }
    if df is None or getattr(df, "empty", True) or len(df) < 2:
        return vacio

    prev = df.iloc[-2]
    curr = df.iloc[-1]
    precio = float(curr["close"]) if pd.notna(curr.get("close")) else None

    filtro = None
    if not forzar:
        if _sesion_baja_liquidez():
            filtro = "Sesion de baja liquidez (filtro horario UTC)"
        else:
            ancho = curr.get("bb_width")
            media = curr.get("bb_width_ma20")
            if pd.notna(ancho) and pd.notna(media) and media > 0:
                if ancho < media * BB_SQUEEZE_RATIO:
                    filtro = f"Bollinger comprimidas ({ancho:.5f} < {BB_SQUEEZE_RATIO:.0%} de {media:.5f})"

    tendencia_alcista = bool(curr["ema_9"] > curr["ema_21"])
    tendencia_bajista = bool(curr["ema_9"] < curr["ema_21"])
    macd_up = _macd_cruce_alcista(prev, curr)
    macd_dn = _macd_cruce_bajista(prev, curr)
    bb_up = _bb_impulso_alcista(prev, curr)
    bb_dn = _bb_impulso_bajista(prev, curr)
    conf_up = _vela_confirmacion_alcista(curr)
    conf_dn = _vela_confirmacion_bajista(curr)

    condiciones_call = [tendencia_alcista, macd_up, bb_up, conf_up]
    condiciones_put = [tendencia_bajista, macd_dn, bb_dn, conf_dn]
    score_call = int(sum(bool(x) for x in condiciones_call))
    score_put = int(sum(bool(x) for x in condiciones_put))
    score = max(score_call, score_put)
    if score_call > score_put:
        lado = "call"
    elif score_put > score_call:
        lado = "put"
    else:
        lado = None

    # Componentes respecto al lado dominante (o CALL si empate)
    lado_comp = lado or ("call" if tendencia_alcista else "put")
    if lado_comp == "call":
        componentes = {
            "ema": "bullish" if tendencia_alcista else "bearish",
            "macd": "bullish" if macd_up else ("bearish" if macd_dn else "neutral"),
            "bb": "bullish" if bb_up else ("bearish" if bb_dn else "neutral"),
            "candle": "bullish" if conf_up else ("bearish" if conf_dn else "neutral"),
        }
    else:
        componentes = {
            "ema": "bearish" if tendencia_bajista else "bullish",
            "macd": "bearish" if macd_dn else ("bullish" if macd_up else "neutral"),
            "bb": "bearish" if bb_dn else ("bullish" if bb_up else "neutral"),
            "candle": "bearish" if conf_dn else ("bullish" if conf_up else "neutral"),
        }

    detalle = (
        f"EMA9{'>' if tendencia_alcista else '<'}EMA21 | "
        f"MACD{'UP' if macd_up else ('DN' if macd_dn else '-')} | "
        f"BB{'UP' if bb_up else ('DN' if bb_dn else '-')} | "
        f"cuerpo={curr['body_ratio']:.0%}"
    )

    base = {
        "score": score,
        "score_call": score_call,
        "score_put": score_put,
        "lado_hipotetico": lado,
        "componentes": componentes,
        "precio_entrada": precio,
        "detalle": detalle,
    }

    if filtro and not forzar:
        return {
            **base,
            "decision": "SKIP",
            "razon": filtro,
            "probabilidad": f"{score / 4:.4f}" if score else "N/A",
            "tipo": None,
            "filtro": filtro,
        }

    if all(condiciones_call):
        return {
            **base,
            "decision": "CALL",
            "razon": f"4/4 CALL - {detalle}",
            "probabilidad": f"{score_call / 4:.4f}",
            "tipo": "call",
            "lado_hipotetico": "call",
            "score": 4,
        }
    if all(condiciones_put):
        return {
            **base,
            "decision": "PUT",
            "razon": f"4/4 PUT - {detalle}",
            "probabilidad": f"{score_put / 4:.4f}",
            "tipo": "put",
            "lado_hipotetico": "put",
            "score": 4,
        }

    return {
        **base,
        "decision": "SKIP",
        "razon": f"Sin confluencia ({score}/4) - {detalle}",
        "probabilidad": f"{score / 4:.4f}",
        "tipo": None,
    }


def ejecutar_trade(iq: IQ_Option, tipo: str, monto: float, activo: str = ACTIVO, expiration: int = EXPIRATION_TIME) -> Tuple[bool, Any, str]:
    try:
        print(f"Ejecutando trade {tipo.upper()} por ${monto} en {activo} (exp {expiration}m)...", file=sys.stderr)
        check, id_operation = iq.buy(monto, activo, tipo, expiration)
        if check:
            print(f"Trade ejecutado. ID: {id_operation}", file=sys.stderr)
            return True, id_operation, f"Trade {tipo.upper()} ejecutado - ID: {id_operation}"
        print("Error al ejecutar trade", file=sys.stderr)
        return False, None, "Error al ejecutar la operacion"
    except Exception as e:
        print(f"Excepcion al ejecutar trade: {e}", file=sys.stderr)
        return False, None, f"Error: {str(e)}"


def verificar_resultado(iq: IQ_Option, id_operation: int, monto: float, timeout: int = 360) -> Dict[str, Any]:
    try:
        print(f"Esperando resultado de operacion {id_operation}...", file=sys.stderr)
        start_time = time.time()
        last_status = None

        while time.time() - start_time < timeout:
            try:
                resultado = iq.check_win_v3(id_operation)
                if resultado != last_status:
                    print(f"Estado actual: {resultado}", file=sys.stderr)
                    last_status = resultado

                if isinstance(resultado, (int, float)):
                    if resultado > 0:
                        print(f"WIN: ${resultado:.2f}", file=sys.stderr)
                        return {"finalizada": True, "ganancia": resultado, "win": True, "id": id_operation, "resultado_raw": resultado}
                    if resultado == 0:
                        print("REFUND", file=sys.stderr)
                        return {"finalizada": True, "ganancia": 0, "win": None, "id": id_operation, "resultado_raw": resultado}
                    print(f"LOSS: ${-monto:.2f}", file=sys.stderr)
                    return {"finalizada": True, "ganancia": -monto, "win": False, "id": id_operation, "resultado_raw": resultado}

                try:
                    detalle = iq.get_binary_option_detail(id_operation)
                    if detalle and isinstance(detalle, dict) and detalle.get("win") is not None:
                        win = detalle.get("win")
                        ganancia = detalle.get("profit", 0) if win else -monto
                        return {"finalizada": True, "ganancia": ganancia, "win": win, "id": id_operation, "resultado_raw": detalle}
                except Exception:
                    pass

                time.sleep(3)
            except Exception as e:
                print(f"Error verificando resultado: {e}", file=sys.stderr)
                time.sleep(3)

        return {
            "finalizada": False,
            "ganancia": 0,
            "win": None,
            "id": id_operation,
            "mensaje": f"Timeout despues de {timeout} segundos",
        }
    except Exception as e:
        return {"finalizada": False, "ganancia": 0, "win": None, "id": id_operation, "error": str(e)}


def load_model(model_file: str = None):
    print("Estrategia por reglas activa - LightGBM no se utiliza.", file=sys.stderr)
    return None


def ejecutar_operacion(
    iq: IQ_Option,
    modo: str = "demo",
    monto: float = None,
    ejecutar_auto: bool = False,
    forzar_operacion: bool = False,
    config_riesgo: dict = None,
    enforce_idempotency: bool = False,
    estrategia: str = "original",
    timeframe: int = TIMEFRAME_SECONDS,
    bloque_velas: int = TC_BLOQUE_DEFAULT,
) -> Dict[str, Any]:
    print("\n" + "-" * 50, file=sys.stderr)
    nombre_estrategia = "THIRD CANDLE (lab, solo CALL)" if estrategia == "third_candle" else "EMA/MACD/BB"
    print(f"ANALISIS {nombre_estrategia} - Modo: {modo.upper()}", file=sys.stderr)
    print("-" * 50, file=sys.stderr)

    defaults_riesgo = {
        "riesgo_porcentaje": 2.0,
        "max_perdidas_consecutivas": 4,
        "stop_loss_diario": 5.0,
        "monto_maximo": 10,
    }
    if config_riesgo:
        defaults_riesgo.update(config_riesgo)
    gestor_riesgo = _obtener_gestor(defaults_riesgo)

    # Estrategia y temporalidad (EUR/USD OTC unicamente; 1m o 5m)
    if estrategia not in ESTRATEGIAS:
        raise ValueError(f"Estrategia desconocida: {estrategia}")
    try:
        timeframe = int(timeframe)
    except (TypeError, ValueError):
        timeframe = TIMEFRAME_SECONDS
    if timeframe not in TIMEFRAMES_VALIDOS:
        raise ValueError(f"Temporalidad invalida: {timeframe}s (solo 60 o 300)")
    expiration = 1 if timeframe == 60 else 5  # expiracion = temporalidad elegida
    # Cooldown post-perdida escalado a la temporalidad (no bloquear de mas en 1m)
    gestor_riesgo.config["cooldown_after_loss_sec"] = timeframe

    try:
        balance_type = "PRACTICE" if modo == "demo" else "REAL"
        try:
            iq.change_balance(balance_type)
            time.sleep(1)
            balance_actual = iq.get_balance()
            print(f"Balance {modo}: ${balance_actual:.2f}", file=sys.stderr)
        except Exception as e:
            print(f"Advertencia cambiando balance: {e}", file=sys.stderr)
            balance_actual = 10000

        ok_riesgo, motivo_riesgo = gestor_riesgo.puede_operar(balance_actual)
        if not ok_riesgo and not forzar_operacion:
            return {
                "success": True,
                "decision": "SKIP",
                "razon": motivo_riesgo,
                "probabilidad": "N/A",
                "timestamp": datetime.now().isoformat(),
                "modo": modo,
                "ejecutado": False,
                "trade_id": None,
                "resultado_trade": None,
                "monto_calculado": 0,
                "estadisticas_riesgo": gestor_riesgo.obtener_estadisticas(),
            }

        df_historial = get_latest_market_data(iq, timeframe)
        min_velas = TC_BLOQUE_MIN + 2 if estrategia == "third_candle" else 50
        if len(df_historial) < min_velas:
            raise ValueError(f"Historico insuficiente ({len(df_historial)} velas). Se necesitan >= {min_velas}.")

        if estrategia == "third_candle":
            # Tercera vela: descartar la vela en formacion si viene incluida
            now_open = candle_open_unix(time.time(), timeframe)
            df_completas = df_historial
            if "from" in df_historial.columns and len(df_historial):
                try:
                    if int(float(df_historial.iloc[-1]["from"])) == now_open:
                        df_completas = df_historial.iloc[:-1]
                except (TypeError, ValueError):
                    pass
            print("Evaluando Third Candle: 2 verdes + 2a sobre 1a + sesgo alcista del laboratorio...", file=sys.stderr)
            decision_data = predecir_decision_third_candle(
                df_completas, bloque=_normalizar_bloque(bloque_velas), forzar=forzar_operacion
            )
        else:
            df_con_features = calcular_features(df_historial)
            if df_con_features.empty or len(df_con_features) < 2:
                raise ValueError("No se pudieron calcular indicadores")

            print("Evaluando confluencia EMA + MACD + Bollinger + vela...", file=sys.stderr)
            decision_data = predecir_decision(df_con_features, forzar=forzar_operacion)

        if monto is None:
            monto = gestor_riesgo.calcular_monto_operacion(balance_actual)
            if monto == 0:
                return {
                    "success": True,
                    "decision": "STOP_LOSS",
                    "razon": gestor_riesgo.motivo_bloqueo or "Stop loss / limite de racha",
                    "probabilidad": decision_data["probabilidad"],
                    "timestamp": datetime.now().isoformat(),
                    "modo": modo,
                    "ejecutado": False,
                    "trade_id": None,
                    "resultado_trade": None,
                    "monto_calculado": 0,
                    "estadisticas_riesgo": gestor_riesgo.obtener_estadisticas(),
                }
        else:
            print(f"Usando monto fijo recibido: ${monto:.2f}", file=sys.stderr)

        resultado = {
            "success": True,
            "decision": decision_data["decision"],
            "razon": decision_data["razon"],
            "probabilidad": decision_data["probabilidad"],
            "timestamp": datetime.now().isoformat(),
            "modo": modo,
            "ejecutado": False,
            "trade_id": None,
            "resultado_trade": None,
            "monto_calculado": monto,
            "estadisticas_riesgo": gestor_riesgo.obtener_estadisticas(),
            "score": decision_data.get("score"),
            "score_call": decision_data.get("score_call"),
            "score_put": decision_data.get("score_put"),
            "componentes": decision_data.get("componentes") or {},
            "lado_hipotetico": decision_data.get("lado_hipotetico"),
            "precio_entrada": decision_data.get("precio_entrada"),
            "activo": ACTIVO,
            "estrategia": estrategia,
            "timeframe": timeframe,
            "expiracion_min": expiration,
        }

        print(f"\nDECISION: {decision_data['decision']}", file=sys.stderr)
        print(f"Score: {decision_data['probabilidad']}", file=sys.stderr)
        print(f"Monto a operar: ${monto:.2f}", file=sys.stderr)

        tipo_operacion = decision_data["tipo"]
        if forzar_operacion and not tipo_operacion:
            tipo_operacion = "call"
            resultado["decision"] = "CALL (FORZADO)"
            resultado["razon"] = "Operacion manual forzada sin senal"
            print("Sin senal. Forzando CALL por peticion manual.", file=sys.stderr)

        candle_ts = candle_open_unix(timeframe=timeframe)
        resultado["candle_open"] = candle_ts
        resultado["idempotency_key"] = None

        if (ejecutar_auto and decision_data["tipo"]) or (forzar_operacion and tipo_operacion):
            print("\nEJECUCION AUTOMATICA/FORZADA", file=sys.stderr)
            # Idempotencia: reclamar CALL/PUT de esta vela ANTES del buy
            if enforce_idempotency and not forzar_operacion:
                import database as _db
                claimed, ikey = _db.claim_trade(ACTIVO, candle_ts, tipo_operacion, {
                    'modo': modo,
                    'monto': monto,
                    'policy': _db.IDEM_POLICY,
                })
                resultado["idempotency_key"] = ikey
                if not claimed:
                    existing = _db.get_idempotency(ikey) or {}
                    st = existing.get('status', 'claimed')
                    print(f"Idempotencia at-most-once: {ikey} status={st} — no se recompra.", file=sys.stderr)
                    resultado["decision"] = "SKIP"
                    resultado["razon"] = (
                        f"Idempotencia ({st}): trade {tipo_operacion.upper()} "
                        f"ya intentado en esta vela — no recompra"
                    )
                    resultado["ejecutado"] = False
                    resultado["idempotency_status"] = st
                    return resultado

                # Chaos: claimed pero aún no in_flight
                _chaos_kill('after_trade_claim')
                # claimed → in_flight ANTES del buy (crash aquí = uncertain, no recompra)
                _db.finalize_idempotency(ikey, 'in_flight', phase='pre_buy')
                _chaos_kill('after_in_flight')

            check, trade_id, mensaje = False, None, 'no_buy'
            try:
                if enforce_idempotency and os.environ.get('SYNAPSE_SIMULATE_CRASH_AFTER_CLAIM', '').strip() == '1':
                    raise SystemExit('SYNAPSE_SIMULATE_CRASH_AFTER_CLAIM: crash deliberado pre-BUY')
                check, trade_id, mensaje = ejecutar_trade(iq, tipo_operacion, monto, ACTIVO, expiration)
                if enforce_idempotency:
                    _chaos_kill('after_buy')
            except Exception as buy_exc:
                if enforce_idempotency and resultado.get("idempotency_key"):
                    import database as _db
                    # Respuesta perdida / crash durante buy: NO reintentar (at-most-once)
                    _db.finalize_idempotency(
                        resultado["idempotency_key"],
                        'uncertain_crash',
                        phase='buy_exception',
                        error=str(buy_exc)[:180],
                        policy=_db.IDEM_POLICY,
                    )
                    resultado["idempotency_status"] = "uncertain_crash"
                raise

            resultado["ejecutado"] = check
            resultado["trade_id"] = trade_id
            resultado["mensaje_trade"] = mensaje

            if enforce_idempotency and resultado.get("idempotency_key"):
                import database as _db
                _db.finalize_idempotency(
                    resultado["idempotency_key"],
                    "placed" if check else "failed",
                    trade_id=trade_id,
                    mensaje=str(mensaje)[:180] if mensaje is not None else None,
                    policy=_db.IDEM_POLICY,
                )
                resultado["idempotency_status"] = "placed" if check else "failed"

            if check and trade_id:
                resultado_trade = verificar_resultado(iq, trade_id, monto)
                resultado["resultado_trade"] = resultado_trade
                if resultado_trade.get("finalizada"):
                    gestor_riesgo.actualizar_resultado(resultado_trade.get("ganancia", 0))
                    resultado["estadisticas_riesgo"] = gestor_riesgo.obtener_estadisticas()

        print("=" * 50 + "\n", file=sys.stderr)
        return resultado

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return {
            "success": False,
            "decision": "ERROR",
            "razon": str(e),
            "probabilidad": "N/A",
            "error": str(e),
            "estadisticas_riesgo": gestor_riesgo.obtener_estadisticas(),
        }


def reconciliar_trade_id(iq: IQ_Option, trade_id, monto: float = None) -> Dict[str, Any]:
    """Contrasta un trade_id con IQ Option (at-most-once reconcile)."""
    out = {
        "found": False,
        "source": None,
        "trade_id": trade_id,
        "finalizada": False,
        "ganancia": None,
        "win": None,
        "raw": None,
        "mensaje": "",
    }
    if trade_id is None or trade_id == "":
        out["mensaje"] = "Sin trade_id para contrastar con IQ"
        return out
    try:
        tid = int(trade_id)
    except Exception:
        tid = trade_id

    # 1) check_win_v3
    try:
        r = iq.check_win_v3(tid)
        out["raw"] = r
        if isinstance(r, (int, float)):
            out["found"] = True
            out["source"] = "check_win_v3"
            out["finalizada"] = True
            out["ganancia"] = float(r)
            out["win"] = float(r) > 0
            out["mensaje"] = "Resultado obtenido vía check_win_v3"
            return out
        if r is None:
            out["mensaje"] = "check_win_v3 sin resultado aún / no encontrado"
    except Exception as e:
        out["mensaje"] = f"check_win_v3 error: {e}"

    # 2) get_async_order
    try:
        order = iq.get_async_order(tid)
        if order:
            out["found"] = True
            out["source"] = "get_async_order"
            out["raw"] = order
            out["mensaje"] = "Orden encontrada en get_async_order (detalle parcial)"
            # intentar profit fields comunes
            for path in (("profit",), ("pnl",), ("win",), ("result",)):
                pass
            if isinstance(order, dict):
                profit = order.get("profit") or order.get("pnl")
                if profit is not None:
                    out["finalizada"] = True
                    out["ganancia"] = float(profit)
                    out["win"] = float(profit) > 0
            return out
    except Exception as e:
        out["mensaje"] = (out.get("mensaje") or "") + f" | get_async_order: {e}"

    # 3) optioninfo recientes
    try:
        info = iq.get_optioninfo(30)
        out["raw"] = info
        rows = []
        if isinstance(info, dict):
            rows = info.get("msg") or info.get("result") or []
            if isinstance(rows, dict):
                rows = rows.get("items") or rows.get("data") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                rid = row.get("id") or row.get("option_id") or row.get("external_id")
                if str(rid) == str(tid):
                    out["found"] = True
                    out["source"] = "get_optioninfo"
                    out["finalizada"] = True
                    profit = row.get("profit") or row.get("pnl") or row.get("win_amount")
                    if profit is None and monto is not None and row.get("win") in ("win", "loose", "lose", "equal"):
                        w = str(row.get("win")).lower()
                        if w == "win":
                            profit = abs(float(monto)) * 0.8  # approx fallback unknown payout
                        elif w in ("loose", "lose"):
                            profit = -abs(float(monto))
                        else:
                            profit = 0.0
                    if profit is not None:
                        out["ganancia"] = float(profit)
                        out["win"] = float(profit) > 0
                    out["mensaje"] = "Encontrada en historial get_optioninfo"
                    return out
        out["mensaje"] = (out.get("mensaje") or "") + " | no aparece en optioninfo reciente"
    except Exception as e:
        out["mensaje"] = (out.get("mensaje") or "") + f" | optioninfo: {e}"

    return out


def evaluar_resultado_hipotetico(precio_entrada: float, precio_salida: float, lado: str) -> Dict[str, Any]:
    """WIN/LOSS hipotético estilo binaria: CALL gana si sale > entra; PUT si sale < entra."""
    if precio_entrada is None or precio_salida is None or not lado:
        return {"finalizada": False, "win": None, "ganancia_hipotetica": None}
    lado = lado.lower()
    if lado == "call":
        win = precio_salida > precio_entrada
        even = precio_salida == precio_entrada
    else:
        win = precio_salida < precio_entrada
        even = precio_salida == precio_entrada
    if even:
        return {"finalizada": True, "win": None, "even": True, "ganancia_hipotetica": 0.0,
                "precio_entrada": precio_entrada, "precio_salida": precio_salida}
    # payout demo simbólico +0.8 / -1.0 por unidad (solo investigación)
    pnl = 0.8 if win else -1.0
    return {
        "finalizada": True,
        "win": bool(win),
        "even": False,
        "ganancia_hipotetica": pnl,
        "precio_entrada": precio_entrada,
        "precio_salida": precio_salida,
        "lado": lado,
    }


def precio_en_timestamp(iq: IQ_Option, ts: float, activo: str = ACTIVO) -> Optional[float]:
    """Obtiene close cercano a ts usando velas 5m."""
    try:
        candles = iq.get_candles(activo, TIMEFRAME_SECONDS, 5, float(ts) + 5)
        if not candles:
            return None
        # última vela cerrada <= ts+buffer
        best = None
        for c in candles:
            from_t = c.get("from") or c.get("to")
            if from_t is None:
                continue
            if best is None or abs(float(from_t) - float(ts)) < abs(float(best.get("from", 0)) - float(ts)):
                best = c
        if not best:
            best = candles[-1]
        return float(best.get("close"))
    except Exception as e:
        print(f"precio_en_timestamp error: {e}", file=sys.stderr)
        return None
