import os
import sys
import numpy as np
import pandas as pd
import lightgbm as lgb
import time
from typing import Dict, Any, Tuple
from datetime import datetime

from iqoptionapi.stable_api import IQ_Option

# Importar las mismas librerías de indicadores
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import BollingerBands, AverageTrueRange

# ====================================================================
# CONFIGURACIÓN Y CONSTANTES
# ====================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_FILE = os.path.join(SCRIPT_DIR, "lgbm_model.txt")

# --- Umbrales y configuración del modelo ---
LOWER_THRESHOLD = 0.45
UPPER_THRESHOLD = 0.52
REGIME_FEATURE  = "bb_width"
REGIME_CUTOFF   = 0.0005

# --- Configuración de trading ---
EXPIRATION_TIME = 1  # Minutos (1, 5, 15, etc.)
DEFAULT_AMOUNT = 1   # Monto por defecto en USD

# --- Lista de features ---
FEATURES = [
    "rsi_14", "ema_20", "ema_50",
    "macd", "macd_signal", "macd_hist",
    "bb_high", "bb_low", "bb_width",
    "atr_14",
    "ret_1", "ret_3", "ret_6", "vol_10",
    "harami"
]
# ====================================================================

# ====================================================================
# GESTIÓN DE RIESGO INTELIGENTE
# ====================================================================

class GestorRiesgoInteligente:
    def __init__(self, config_riesgo=None):
        self.config = config_riesgo or {
            'riesgo_porcentaje': 2.0,
            'max_perdidas_consecutivas': 3,
            'stop_loss_diario': 15,
            'monto_maximo': 10
        }
        self.racha_perdidas = 0
        self.racha_ganancias = 0
        self.profit_diario = 0
        self.operaciones_hoy = 0
        
    def calcular_monto_operacion(self, balance_actual, señal_calidad="normal"):
        """
        Calcula el monto inteligente basado en balance y configuración
        """
        try:
            # 1. Porcentaje base del balance
            riesgo_base = self.config.get('riesgo_porcentaje', 2.0)
            
            # 2. Ajustar por calidad de señal
            if señal_calidad == "alta":
                riesgo_base *= 1.2  # +20% para señales fuertes
                print(f"📈 Señal ALTA - Aumentando riesgo a {riesgo_base:.1f}%", file=sys.stderr)
            elif señal_calidad == "baja":
                riesgo_base *= 0.8  # -20% para señales débiles
                print(f"📉 Señal BAJA - Reduciendo riesgo a {riesgo_base:.1f}%", file=sys.stderr)
                
            # 3. Ajustar por racha de resultados
            riesgo_base = self._ajustar_por_racha(riesgo_base)
            
            # 4. Calcular monto base
            monto_base = balance_actual * (riesgo_base / 100)
            
            # 5. Aplicar límites inteligentes
            monto_final = self._aplicar_limites_inteligentes(monto_base, balance_actual)
            
            print(f"🎯 Monto calculado: ${monto_final:.2f} ({(monto_final/balance_actual)*100:.1f}% del balance)", file=sys.stderr)
            return round(monto_final, 2)
            
        except Exception as e:
            print(f"⚠️ Error calculando monto inteligente: {e}, usando monto por defecto", file=sys.stderr)
            return DEFAULT_AMOUNT
    
    def _ajustar_por_racha(self, riesgo_base):
        """
        Ajusta el riesgo basado en rachas de resultados
        """
        # Reducir riesgo después de pérdidas consecutivas
        if self.racha_perdidas >= self.config.get('max_perdidas_consecutivas', 3):
            nuevo_riesgo = riesgo_base * 0.5  # Reducir 50%
            print(f"🔻 Racha de {self.racha_perdidas} pérdidas - Reduciendo riesgo a {nuevo_riesgo:.1f}%", file=sys.stderr)
            return nuevo_riesgo
        
        # Aumentar ligeramente en rachas ganadoras
        if self.racha_ganancias >= 3:
            nuevo_riesgo = riesgo_base * 1.1  # Aumentar 10%
            print(f"🔺 Racha de {self.racha_ganancias} ganancias - Aumentando riesgo a {nuevo_riesgo:.1f}%", file=sys.stderr)
            return nuevo_riesgo
            
        return riesgo_base
    
    def _aplicar_limites_inteligentes(self, monto, balance):
        """
        Aplica límites inteligentes basados en el tamaño de la cuenta
        """
        # Mínimo absoluto
        monto_minimo = 1
        
        # Máximo basado en tamaño de cuenta
        if balance < 100:
            monto_maximo = min(monto, 2)  # Cuentas pequeñas: máx $2
        elif balance < 500:
            monto_maximo = min(monto, 5)  # Cuentas medianas: máx $5
        else:
            monto_maximo = min(monto, self.config.get('monto_maximo', 10))
            
        # Asegurar que está entre mínimo y máximo
        monto_final = max(monto_minimo, min(monto_maximo, monto))
        
        # Verificar stop loss diario
        stop_diario = balance * (self.config.get('stop_loss_diario', 15) / 100)
        if self.profit_diario <= -stop_diario:
            print(f"🛑 STOP LOSS DIARIO ACTIVADO - Profit hoy: ${self.profit_diario:.2f}", file=sys.stderr)
            return 0  # No operar más hoy
            
        return monto_final
    
    def actualizar_resultado(self, ganancia):
        """
        Actualiza estadísticas después de una operación
        """
        if ganancia > 0:
            self.racha_ganancias += 1
            self.racha_perdidas = 0
        else:
            self.racha_perdidas += 1
            self.racha_ganancias = 0
            
        self.profit_diario += ganancia
        self.operaciones_hoy += 1
        
        print(f"📊 Estadísticas actualizadas - Racha: {self.racha_ganancias if ganancia > 0 else self.racha_perdidas} {'✅' if ganancia > 0 else '❌'} | Profit hoy: ${self.profit_diario:.2f}", file=sys.stderr)
    
    def obtener_estadisticas(self):
        """
        Retorna estadísticas actuales para el frontend
        """
        return {
            'racha_actual': self.racha_ganancias if self.racha_ganancias > 0 else -self.racha_perdidas,
            'profit_diario': self.profit_diario,
            'operaciones_hoy': self.operaciones_hoy,
            'racha_perdidas': self.racha_perdidas,
            'racha_ganancias': self.racha_ganancias
        }


def get_latest_market_data(iq: IQ_Option):
    """Obtiene los datos más recientes del mercado"""
    if not iq:
        raise ValueError("La sesión de IQ Option no es válida.")

    print("📈 Obteniendo velas de EURUSD (5min)...", file=sys.stderr)
    
    candles = iq.get_candles("EURUSD-OTC", 300, 120, time.time())
    
    if not candles:
        raise RuntimeError("No se pudieron obtener velas de EURUSD.")

    df = pd.DataFrame(candles)
    
    # Mapear nombres de columnas
    column_mapping = {
        'open': 'open',
        'max': 'high',
        'min': 'low',
        'close': 'close',
        'volume': 'volume'
    }
    
    existing_cols = {}
    for api_col, std_col in column_mapping.items():
        if api_col in df.columns:
            existing_cols[api_col] = std_col
    
    df = df.rename(columns=existing_cols)
    
    required_cols = ['open', 'high', 'low', 'close', 'volume']
    available_cols = [col for col in required_cols if col in df.columns]
    
    if len(available_cols) < 4:
        raise RuntimeError(f"Faltan columnas necesarias. Disponibles: {available_cols}")
    
    df = df[available_cols]
    
    if 'volume' not in df.columns:
        df['volume'] = 0
    
    print(f"📊 Se obtuvieron {len(df)} velas", file=sys.stderr)
    return df

def detect_harami(row_prev, row_curr):
    """Detecta un patrón Harami simple."""
    body_prev = abs(row_prev["close"] - row_prev["open"])
    body_curr = abs(row_curr["close"] - row_curr["open"])
    high_prev = max(row_prev["open"], row_prev["close"])
    low_prev  = min(row_prev["open"], row_prev["close"])
    inside = (max(row_curr["open"], row_curr["close"]) <= high_prev) and \
             (min(row_curr["open"], row_curr["close"]) >= low_prev)
    return int((body_curr < body_prev) and inside)

def calcular_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula todos los features técnicos necesarios para el modelo."""
    print("⚙️  Calculando features técnicos...", file=sys.stderr)
    df_feat = df.copy()
    
    df_feat["rsi_14"]  = RSIIndicator(df_feat["close"], window=14).rsi()
    df_feat["ema_20"]  = EMAIndicator(df_feat["close"], window=20).ema_indicator()
    df_feat["ema_50"]  = EMAIndicator(df_feat["close"], window=50).ema_indicator()
    
    macd = MACD(df_feat["close"])
    df_feat["macd"]        = macd.macd()
    df_feat["macd_signal"] = macd.macd_signal()
    df_feat["macd_hist"]   = macd.macd_diff()
    
    boll = BollingerBands(df_feat["close"])
    df_feat["bb_high"]  = boll.bollinger_hband()
    df_feat["bb_low"]   = boll.bollinger_lband()
    df_feat["bb_width"] = (df_feat["bb_high"] - df_feat["bb_low"]) / df_feat["close"]
    
    df_feat["atr_14"] = AverageTrueRange(df_feat["high"], df_feat["low"], df_feat["close"], window=14).average_true_range()
    
    df_feat["ret_1"]  = df_feat["close"].pct_change()
    df_feat["ret_3"]  = df_feat["close"].pct_change(3)
    df_feat["ret_6"]  = df_feat["close"].pct_change(6)
    df_feat["vol_10"] = df_feat["ret_1"].rolling(10).std()
    
    harami = [0]
    for i in range(1, len(df_feat)):
        harami.append(detect_harami(df_feat.iloc[i - 1], df_feat.iloc[i]))
    df_feat["harami"] = harami
    
    print("✅ Features calculados.", file=sys.stderr)
    return df_feat.dropna()

def predecir_decision(model, df_vela_actual: pd.DataFrame, forzar: bool = False) -> Dict[str, Any]:
    """Toma la última vela y retorna una decisión estructurada."""
    if df_vela_actual.empty:
        return {"decision": "SKIP", "razon": "No hay datos", "probabilidad": "N/A", "tipo": None}
        
    vela_features = df_vela_actual.iloc[-1]
    
    # Chequeo de Régimen de Volatilidad (solo si no se fuerza la operación)
    if not forzar:
        regime_value = vela_features[REGIME_FEATURE]
        if regime_value < REGIME_CUTOFF:
            return {
                "decision": "SKIP",
                "razon": f"Volatilidad baja ({regime_value:.6f})",
                "probabilidad": "N/A",
                "tipo": None
            }
    
    # Predicción de probabilidad
    try:
        proba = model.predict([vela_features[FEATURES]])[0]
    except Exception as e:
        return {"decision": "SKIP", "razon": f"Error: {e}", "probabilidad": "N/A", "tipo": None}
        
    # Decisión según umbrales
    if proba <= LOWER_THRESHOLD:
        decision = "PUT"
        tipo = "put"
    elif proba >= UPPER_THRESHOLD:
        decision = "CALL"
        tipo = "call"
    else:
        decision = "SKIP"
        tipo = None
        
    return {
        "decision": decision,
        "razon": "Señal detectada" if tipo else "Sin señal clara",
        "probabilidad": f"{proba:.4f}",
        "tipo": tipo
    }

def ejecutar_trade(iq: IQ_Option, tipo: str, monto: float, activo: str = "EURUSD") -> Tuple[bool, Any, str]:
    """
    Ejecuta una operación en IQ Option
    Retorna: (éxito, id_operación, mensaje)
    """
    try:
        print(f"💰 Ejecutando trade {tipo.upper()} por ${monto} en {activo}...", file=sys.stderr)
        
        # Ejecutar la compra
        check, id_operation = iq.buy(monto, activo, tipo, EXPIRATION_TIME)
        
        if check:
            print(f"✅ Trade ejecutado exitosamente. ID: {id_operation}", file=sys.stderr)
            return True, id_operation, f"Trade {tipo.upper()} ejecutado - ID: {id_operation}"
        else:
            print(f"❌ Error al ejecutar trade", file=sys.stderr)
            return False, None, "Error al ejecutar la operación"
            
    except Exception as e:
        print(f"❌ Excepción al ejecutar trade: {e}", file=sys.stderr)
        return False, None, f"Error: {str(e)}"

def verificar_resultado(iq: IQ_Option, id_operation: int, monto: float, timeout: int = 70) -> Dict[str, Any]:
    """
    Verifica el resultado de una operación
    Espera hasta que la operación se cierre
    """
    try:
        print(f"⏳ Esperando resultado de operación {id_operation}...", file=sys.stderr)
        
        start_time = time.time()
        last_status = None
        
        while time.time() - start_time < timeout:
            try:
                # Método 1: check_win_v3 (nuevo)
                resultado = iq.check_win_v3(id_operation)
                
                # Debug: mostrar qué devuelve la API
                if resultado != last_status:
                    print(f"🔍 Estado actual: {resultado} (tipo: {type(resultado)})", file=sys.stderr)
                    last_status = resultado
                
                # Si es un número, la operación está cerrada
                if isinstance(resultado, (int, float)):
                    if resultado > 0:
                        # Ganancia
                        ganancia = resultado
                        win = True
                        print(f"✅ WIN: ${ganancia:.2f}", file=sys.stderr)
                        return {
                            "finalizada": True,
                            "ganancia": ganancia,
                            "win": win,
                            "id": id_operation,
                            "resultado_raw": resultado
                        }
                    elif resultado == 0:
                        # Empate - devolución del monto
                        ganancia = 0
                        win = None
                        print(f"⚪ REFUND: ${ganancia:.2f} (Monto devuelto)", file=sys.stderr)
                        return {
                            "finalizada": True,
                            "ganancia": ganancia,
                            "win": win,
                            "id": id_operation,
                            "resultado_raw": resultado
                        }
                    else:
                        # Pérdida
                        ganancia = -monto
                        win = False
                        print(f"❌ LOSS: ${ganancia:.2f}", file=sys.stderr)
                        return {
                            "finalizada": True,
                            "ganancia": ganancia,
                            "win": win,
                            "id": id_operation,
                            "resultado_raw": resultado
                        }
                
                # Método 2: Intentar con get_binary_option_detail
                try:
                    detalle = iq.get_binary_option_detail(id_operation)
                    if detalle and isinstance(detalle, dict):
                        if detalle.get('win') is not None:
                            win = detalle.get('win')
                            ganancia = detalle.get('profit', 0) if win else -monto
                            print(f"✅ Resultado por detalle: {'WIN' if win else 'LOSS'} - ${ganancia:.2f}", file=sys.stderr)
                            return {
                                "finalizada": True,
                                "ganancia": ganancia,
                                "win": win,
                                "id": id_operation,
                                "resultado_raw": detalle
                            }
                except Exception as e_detail:
                    pass  # Ignorar errores en método alternativo
                    
                # Si llegamos aquí, la operación sigue abierta
                time.sleep(3)  # Esperar 3 segundos antes de verificar de nuevo
                    
            except Exception as e:
                print(f"⚠️  Error verificando resultado: {e}", file=sys.stderr)
                time.sleep(3)
        
        # Timeout
        print(f"⏰ Timeout esperando resultado después de {timeout} segundos", file=sys.stderr)
        return {
            "finalizada": False,
            "ganancia": 0,
            "win": None,
            "id": id_operation,
            "mensaje": f"Timeout después de {timeout} segundos"
        }
        
    except Exception as e:
        print(f"❌ Error verificando resultado: {e}", file=sys.stderr)
        return {
            "finalizada": False,
            "ganancia": 0,
            "win": None,
            "id": id_operation,
            "error": str(e)
        }

def load_model(model_file: str):
    """Carga el modelo LightGBM."""
    if not os.path.exists(model_file):
        raise FileNotFoundError(f"Modelo no encontrado: {model_file}")
    
    try:
        bst = lgb.Booster(model_file=model_file)
        print(f"🧠 Modelo cargado exitosamente.", file=sys.stderr)
        return bst
    except Exception as e:
        raise IOError(f"No se pudo cargar el modelo: {e}")


def ejecutar_operacion(iq: IQ_Option, modo: str = "demo", monto: float = None, 
                      ejecutar_auto: bool = False, forzar_operacion: bool = False,
                      config_riesgo: dict = None) -> Dict[str, Any]:
    """
    Función principal de trading
    
    Args:
        iq: Sesión de IQ Option
        modo: "demo" o "real"
        monto: Cantidad a invertir (None para cálculo automático)
        ejecutar_auto: Si True, ejecuta automáticamente la operación
        forzar_operacion: Si True, fuerza una operación aunque no haya señal.
        config_riesgo: Configuración para gestión inteligente de riesgo
    """
    print("\n" + "-"*50, file=sys.stderr)
    print(f"🚀 INICIANDO ANÁLISIS - Modo: {modo.upper()}", file=sys.stderr)
    print("-"*50, file=sys.stderr)
    
    # Inicializar gestor de riesgo
    gestor_riesgo = GestorRiesgoInteligente(config_riesgo)
    
    try:
        # Cambiar a la cuenta correcta
        balance_type = 'PRACTICE' if modo == 'demo' else 'REAL'
        try:
            iq.change_balance(balance_type)
            time.sleep(1)
            balance_actual = iq.get_balance()
            print(f"💰 Balance {modo}: ${balance_actual:.2f}", file=sys.stderr)
        except Exception as e:
            print(f"⚠️  Advertencia cambiando balance: {e}", file=sys.stderr)
            balance_actual = 10000  # Valor por defecto si hay error
        
        # Cargar modelo
        bst = load_model(MODEL_FILE)

        # Obtener datos de mercado
        df_historial = get_latest_market_data(iq)

        # Calcular features
        df_con_features = calcular_features(df_historial)
        
        if df_con_features.empty:
            raise ValueError("No se pudieron calcular features")

        # Tomar decisión
        print("🤔 Analizando mercado...", file=sys.stderr)
        decision_data = predecir_decision(bst, df_con_features, forzar=forzar_operacion)
        
        # Determinar calidad de señal para gestión de riesgo
        señal_calidad = "normal"
        if decision_data["tipo"]:
            proba = float(decision_data["probabilidad"])
            if proba <= 0.40 or proba >= 0.80:
                señal_calidad = "alta"
            elif proba <= 0.45 or proba >= 0.75:
                señal_calidad = "baja"
        
        # Calcular monto inteligente si no se especifica
        if monto is None:
            monto = gestor_riesgo.calcular_monto_operacion(balance_actual, señal_calidad)
            if monto == 0:
                return {
                    "success": True,
                    "decision": "STOP_LOSS",
                    "razon": "Stop loss diario alcanzado",
                    "probabilidad": decision_data["probabilidad"],
                    "timestamp": datetime.now().isoformat(),
                    "modo": modo,
                    "ejecutado": False,
                    "trade_id": None,
                    "resultado_trade": None,
                    "monto_calculado": 0,
                    "estadisticas_riesgo": gestor_riesgo.obtener_estadisticas()
                }
        else:
            print(f"💰 Usando monto fijo: ${monto:.2f}", file=sys.stderr)
        
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
            "estadisticas_riesgo": gestor_riesgo.obtener_estadisticas()
        }
        
        print(f"\n🎯 DECISIÓN: {decision_data['decision']}", file=sys.stderr)
        print(f"📊 Probabilidad: {decision_data['probabilidad']}", file=sys.stderr)
        print(f"💰 Monto a operar: ${monto:.2f}", file=sys.stderr)
        
        tipo_operacion = decision_data["tipo"]

        # Si se fuerza la operación y no hay señal, se asume CALL
        if forzar_operacion and not tipo_operacion:
            tipo_operacion = "call"
            resultado["decision"] = "CALL (FORZADO)"
            resultado["razon"] = "Operación manual forzada sin señal"
            print(f"⚠️  No se encontró señal. Forzando 'CALL' por petición manual.", file=sys.stderr)

        # Ejecutar si el bot está activo y hay señal, o si se ha forzado la operación
        if (ejecutar_auto and decision_data["tipo"]) or (forzar_operacion and tipo_operacion):
            print(f"\n🤖 EJECUCIÓN AUTOMÁTICA/FORZADA ACTIVADA", file=sys.stderr)
            
            # Ejecutar el trade
            check, trade_id, mensaje = ejecutar_trade(
                iq, 
                tipo_operacion, 
                monto, 
                "EURUSD-OTC"
            )
            
            resultado["ejecutado"] = check
            resultado["trade_id"] = trade_id
            resultado["mensaje_trade"] = mensaje
            
            if check and trade_id:
                # Esperar y verificar resultado
                resultado_trade = verificar_resultado(iq, trade_id, monto)
                resultado["resultado_trade"] = resultado_trade
                
                # Actualizar estadísticas de riesgo
                if resultado_trade["finalizada"]:
                    gestor_riesgo.actualizar_resultado(resultado_trade["ganancia"])
                    resultado["estadisticas_riesgo"] = gestor_riesgo.obtener_estadisticas()
        
        print("="*50 + "\n", file=sys.stderr)
        return resultado

    except Exception as e:
        print(f"❌ ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        
        return {
            "success": False,
            "decision": "ERROR",
            "razon": str(e),
            "probabilidad": "N/A",
            "error": str(e),
            "estadisticas_riesgo": gestor_riesgo.obtener_estadisticas()
        }