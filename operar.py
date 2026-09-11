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

        if time.time() - self.last_loss_ts < COOLDOWN_AFTER_LOSS_SEC:
            restante = int(COOLDOWN_AFTER_LOSS_SEC - (time.time() - self.last_loss_ts))
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


def get_latest_market_data(iq: IQ_Option) -> pd.DataFrame:
    if not iq:
        raise ValueError("La sesion de IQ Option no es valida.")

    print(f"Obteniendo velas de {ACTIVO} (5min)...", file=sys.stderr)
    candles = iq.get_candles(ACTIVO, TIMEFRAME_SECONDS, CANDLES_HISTORY, time.time())
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


def predecir_decision(model_or_df, df_vela_actual=None, forzar: bool = False) -> Dict[str, Any]:
    if df_vela_actual is None:
        df = model_or_df
    else:
        df = df_vela_actual

    vacio = {"decision": "SKIP", "razon": "No hay datos", "probabilidad": "N/A", "tipo": None}
    if df is None or getattr(df, "empty", True) or len(df) < 2:
        return vacio

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    if not forzar:
        if _sesion_baja_liquidez():
            return {
                "decision": "SKIP",
                "razon": "Sesion de baja liquidez (filtro horario UTC)",
                "probabilidad": "N/A",
                "tipo": None,
            }
        ancho = curr.get("bb_width")
        media = curr.get("bb_width_ma20")
        if pd.notna(ancho) and pd.notna(media) and media > 0:
            if ancho < media * BB_SQUEEZE_RATIO:
                return {
                    "decision": "SKIP",
                    "razon": f"Bollinger comprimidas ({ancho:.5f} < {BB_SQUEEZE_RATIO:.0%} de {media:.5f})",
                    "probabilidad": "N/A",
                    "tipo": None,
                }

    tendencia_alcista = curr["ema_9"] > curr["ema_21"]
    tendencia_bajista = curr["ema_9"] < curr["ema_21"]
    macd_up = _macd_cruce_alcista(prev, curr)
    macd_dn = _macd_cruce_bajista(prev, curr)
    bb_up = _bb_impulso_alcista(prev, curr)
    bb_dn = _bb_impulso_bajista(prev, curr)
    conf_up = _vela_confirmacion_alcista(curr)
    conf_dn = _vela_confirmacion_bajista(curr)

    condiciones_call = [tendencia_alcista, macd_up, bb_up, conf_up]
    condiciones_put = [tendencia_bajista, macd_dn, bb_dn, conf_dn]
    score_call = sum(bool(x) for x in condiciones_call)
    score_put = sum(bool(x) for x in condiciones_put)

    detalle = (
        f"EMA9{'>' if tendencia_alcista else '<'}EMA21 | "
        f"MACD{'UP' if macd_up else ('DN' if macd_dn else '-')} | "
        f"BB{'UP' if bb_up else ('DN' if bb_dn else '-')} | "
        f"cuerpo={curr['body_ratio']:.0%}"
    )

    if all(condiciones_call):
        return {
            "decision": "CALL",
            "razon": f"4/4 CALL - {detalle}",
            "probabilidad": f"{score_call / 4:.4f}",
            "tipo": "call",
        }
    if all(condiciones_put):
        return {
            "decision": "PUT",
            "razon": f"4/4 PUT - {detalle}",
            "probabilidad": f"{score_put / 4:.4f}",
            "tipo": "put",
        }

    return {
        "decision": "SKIP",
        "razon": f"Sin confluencia ({max(score_call, score_put)}/4) - {detalle}",
        "probabilidad": f"{max(score_call, score_put) / 4:.4f}",
        "tipo": None,
    }


def ejecutar_trade(iq: IQ_Option, tipo: str, monto: float, activo: str = ACTIVO) -> Tuple[bool, Any, str]:
    try:
        print(f"Ejecutando trade {tipo.upper()} por ${monto} en {activo} (exp {EXPIRATION_TIME}m)...", file=sys.stderr)
        check, id_operation = iq.buy(monto, activo, tipo, EXPIRATION_TIME)
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
) -> Dict[str, Any]:
    print("\n" + "-" * 50, file=sys.stderr)
    print(f"ANALISIS EMA/MACD/BB - Modo: {modo.upper()}", file=sys.stderr)
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

        df_historial = get_latest_market_data(iq)
        if len(df_historial) < 50:
            raise ValueError(f"Historico insuficiente ({len(df_historial)} velas). Se necesitan >=50.")

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

        candle_ts = candle_open_unix()
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

                # Punto de fuego: claimed → in_flight ANTES del buy.
                # Si el proceso muere aquí o tras enviar sin respuesta: reconcile → uncertain_crash.
                _db.finalize_idempotency(ikey, 'in_flight', phase='pre_buy')

            check, trade_id, mensaje = False, None, 'no_buy'
            try:
                # Simulación de crash entre claim/in_flight y BUY (solo si env lo pide)
                if enforce_idempotency and os.environ.get('SYNAPSE_SIMULATE_CRASH_AFTER_CLAIM', '').strip() == '1':
                    raise SystemExit('SYNAPSE_SIMULATE_CRASH_AFTER_CLAIM: crash deliberado pre-BUY')
                check, trade_id, mensaje = ejecutar_trade(iq, tipo_operacion, monto, ACTIVO)
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
