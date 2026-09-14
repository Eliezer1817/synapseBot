import http.server
import socketserver
import json
import os
import sys
import traceback
import time
import uuid
import threading
from urllib.parse import urlparse, parse_qs
from conexion import _connect
from operar import ejecutar_operacion
from datetime import datetime
import database  # ✅ Importación correcta
import crypto_util
import payment_tron

PORT = int(os.environ.get("PORT", 8000))
CWD = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("SYNAPSE_DATA_DIR") or (
    "/data" if os.path.isdir("/data") else CWD
)
os.makedirs(DATA_DIR, exist_ok=True)

def _migrate_legacy_sessions():
    legacy = os.path.join(CWD, "sessions.json")
    dest = os.path.join(DATA_DIR, "sessions.json")
    if legacy == dest or os.path.exists(dest) or not os.path.exists(legacy):
        return
    try:
        import shutil
        shutil.copy2(legacy, dest)
        print(f"📦 Migrado sessions.json → {dest}")
    except Exception as e:
        print(f"⚠️ No se pudo migrar sessions.json: {e}")

_migrate_legacy_sessions()

# CORS: solo orígenes explícitos (override con SYNAPSE_CORS_ORIGINS, separados por coma)
_DEFAULT_CORS = (
    "https://synapsebot.fly.dev,"
    "http://localhost:8000,"
    "http://127.0.0.1:8000"
)
ALLOWED_ORIGINS = frozenset(
    o.strip().rstrip("/")
    for o in os.environ.get("SYNAPSE_CORS_ORIGINS", _DEFAULT_CORS).split(",")
    if o.strip()
)


# Sistema de sesiones mejorado
active_sessions = {}
session_tokens = {}

# Cargar estado del bot desde la base de datos al iniciar
db_data = database.load_database()

# 🔥 SISTEMA BOT 24/7 MEJORADO - PERSISTENTE EN SERVIDOR
bot_servidor_activo = db_data['bot_servidor']['activo']
bot_servidor_config = db_data['bot_servidor']['config']
bot_servidor_thread = None
bot_servidor_estadisticas = db_data['bot_servidor']['estadisticas']
bot_start_lock = threading.Lock()

class SessionManager:
    # Inactividad: 8h. Edad máxima absoluta: 24h desde create.
    SESSION_TIMEOUT = 8 * 3600
    SESSION_MAX_AGE = 24 * 3600

    @staticmethod
    def generate_token():
        return str(uuid.uuid4())

    @staticmethod
    def _sessions_path():
        return os.path.join(DATA_DIR, 'sessions.json')

    @staticmethod
    def _load_persisted():
        path = SessionManager._sessions_path()
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f) or {}
        except Exception:
            pass
        return {}

    @staticmethod
    def _save_persisted(data):
        path = SessionManager._sessions_path()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"⚠️ No se pudo persistir sesión: {e}")

    @staticmethod
    def _persist_token(token, email):
        data = SessionManager._load_persisted()
        data[token] = {'email': email, 'ts': time.time()}
        SessionManager._save_persisted(data)

    @staticmethod
    def _unpersist_token(token):
        data = SessionManager._load_persisted()
        if token in data:
            del data[token]
            SessionManager._save_persisted(data)

    @staticmethod
    def _is_expired(session):
        now = time.time()
        if now - session.get('last_activity', 0) > SessionManager.SESSION_TIMEOUT:
            return True
        if now - session.get('created_at', 0) > SessionManager.SESSION_MAX_AGE:
            return True
        return False

    @staticmethod
    def create_session(email, iq_instance):
        token = SessionManager.generate_token()
        active_sessions[token] = {
            'email': email,
            'iq': iq_instance,
            'created_at': time.time(),
            'last_activity': time.time(),
            'gestor_riesgo': None
        }
        session_tokens[email] = token
        SessionManager._persist_token(token, email)
        return token

    @staticmethod
    def _restore_session(token):
        data = SessionManager._load_persisted()
        info = data.get(token)
        if not info:
            return None
        # Expiración del token persistido
        if time.time() - float(info.get('ts') or 0) > SessionManager.SESSION_MAX_AGE:
            SessionManager._unpersist_token(token)
            return None
        email = info.get('email')
        creds = database.obtener_credenciales_bot() or {}
        # Aislamiento: solo restaurar si las credenciales cifradas son del mismo email
        if not email or creds.get('email') != email or not creds.get('password'):
            SessionManager._unpersist_token(token)
            return None
        print(f"🔄 Restaurando sesión de {crypto_util.mask_email(email)} tras reinicio de Fly...")
        iq_session = _connect(email, creds['password'])
        active_sessions[token] = {
            'email': email,
            'iq': iq_session,
            'created_at': float(info.get('ts') or time.time()),
            'last_activity': time.time(),
            'gestor_riesgo': None
        }
        session_tokens[email] = token
        print("✅ Sesión restaurada")
        return active_sessions[token]

    @staticmethod
    def get_session(token):
        if token in active_sessions:
            session = active_sessions[token]
            if SessionManager._is_expired(session):
                SessionManager.delete_session(token)
                return None
            session['last_activity'] = time.time()
            return session
        try:
            return SessionManager._restore_session(token)
        except Exception as e:
            print(f"❌ No se pudo restaurar sesión: {crypto_util.safe_client_error(e)}")
            return None

    @staticmethod
    def delete_session(token):
        if token in active_sessions:
            email = active_sessions[token]['email']
            if email in session_tokens:
                del session_tokens[email]
            del active_sessions[token]
        SessionManager._unpersist_token(token)

    @staticmethod
    def delete_session_by_email(email):
        if email in session_tokens:
            token = session_tokens[email]
            SessionManager.delete_session(token)
        else:
            # Limpiar tokens persistidos huérfanos de ese email
            data = SessionManager._load_persisted()
            changed = False
            for t, info in list(data.items()):
                if info.get('email') == email:
                    del data[t]
                    changed = True
            if changed:
                SessionManager._save_persisted(data)

    @staticmethod
    def cleanup_expired_sessions():
        """Limpiar sesiones expiradas"""
        expired_tokens = [
            token for token, session_data in active_sessions.items()
            if SessionManager._is_expired(session_data)
        ]
        for token in expired_tokens:
            SessionManager.delete_session(token)
        if expired_tokens:
            print(f"🧹 Sesiones expiradas limpiadas: {len(expired_tokens)}")

def get_authenticated_session(handler):
    """Obtener sesión autenticada desde headers con mejor manejo de errores"""
    try:
        auth_header = handler.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return None
        
        token = auth_header.replace('Bearer ', '').strip()
        if not token:
            return None
        
        session = SessionManager.get_session(token)
        return session
            
    except Exception as e:
        print(f"❌ Error en autenticación: {e}")
        return None

def obtener_balances_reales(iq):
    """Obtiene balances REALES de las cuentas demo y real"""
    real_balance = 0.0
    demo_balance = 0.0
    real_id = None
    demo_id = None
    
    try:
        print("💰 Obteniendo balances REALES...")
        
        # Método 1: Intentar con get_balances()
        try:
            balances_data = iq.get_balances()
            
            if balances_data and isinstance(balances_data, dict):
                balances_list = balances_data.get('msg', [])
                
                for bal in balances_list:
                    if isinstance(bal, dict):
                        bal_type = bal.get('type')
                        amount = bal.get('amount', 0)
                        bal_id = bal.get('id')
                        
                        # Tipo 1 = REAL, Tipo 4 = PRACTICE
                        if bal_type == 1:
                            real_balance = float(amount)
                            real_id = bal_id
                            print(f"✅ Balance REAL: ${real_balance}")
                        elif bal_type == 4:
                            demo_balance = float(amount)
                            demo_id = bal_id
                            print(f"✅ Balance DEMO: ${demo_balance}")
            
        except Exception as e:
            print(f"⚠️ Error con get_balances(): {e}")

        # Método 2: Método alternativo si no se encontraron balances
        if real_balance == 0 and demo_balance == 0:
            print("🔄 Usando método alternativo para balances...")
            try:
                # Cambiar a REAL y obtener balance
                if iq.change_balance('REAL'):
                    time.sleep(1)
                    real_balance_raw = iq.get_balance()
                    if real_balance_raw:
                        real_balance = float(real_balance_raw)
                        print(f"💰 Balance REAL (alternativo): ${real_balance}")
                
                # Cambiar a PRACTICE y obtener balance
                if iq.change_balance('PRACTICE'):
                    time.sleep(1)
                    demo_balance_raw = iq.get_balance()
                    if demo_balance_raw:
                        demo_balance = float(demo_balance_raw)
                        print(f"🎯 Balance DEMO (alternativo): ${demo_balance}")
                
                # Volver a REAL por defecto
                iq.change_balance('REAL')
                time.sleep(0.5)
                
            except Exception as e2:
                print(f"❌ Error en método alternativo: {e2}")

        # Si aún no hay balances, usar valores por defecto
        if real_balance == 0 and demo_balance == 0:
            print("💰 Usando valores por defecto para balances")
            real_balance = 0.0
            demo_balance = 10000.0

        print(f"📊 RESUMEN FINAL: REAL: ${real_balance}, DEMO: ${demo_balance}")
        
    except Exception as e:
        print(f"❌ Error general obteniendo balances: {e}")
        real_balance = 0.0
        demo_balance = 10000.0
    
    return real_balance, demo_balance, real_id, demo_id

# 🔥 NUEVA FUNCIÓN MEJORADA: Bot servidor 24/7 con timing preciso
def _set_fase_bot(fase, mensaje='', **extra):
    """Actualiza fase + heartbeat del daemon para el dashboard."""
    try:
        database.actualizar_estado_vivo(fase, mensaje, **extra)
    except Exception as e:
        print(f"⚠️ No se pudo guardar fase del bot: {e}")


def ejecutar_bot_servidor():
    """Ejecuta el bot automático en el servidor de forma continua y precisa"""
    global bot_servidor_thread
    
    print(f"\n🎯 INICIANDO BOT SERVIDOR 24/7 - INDEPENDIENTE DEL CLIENTE")
    _set_fase_bot('conectando', 'Conectando a IQ Option...')
    
    # Cargar configuración / credenciales descifradas
    db_data = database.load_database()
    bot_config = db_data['bot_servidor']['config']
    bot_stats = db_data['bot_servidor']['estadisticas']
    bot_credenciales = database.obtener_credenciales_bot()

    if not bot_credenciales or not bot_credenciales.get('email') or not bot_credenciales.get('password'):
        print("❌ ERROR: Credenciales del bot no configuradas. Deteniendo bot.")
        _set_fase_bot('error', 'Credenciales del bot no configuradas. Volvé a iniciar sesión.')
        database.detener_bot_servidor()
        return

    # At-most-once: cerrar claims/in_flight huérfanos de un crash previo
    try:
        orphans = database.reconcile_orphan_idempotency(min_age_sec=2)
        if orphans:
            print(f"⚠️ Idempotencia: {len(orphans)} key(s) → uncertain_crash (no recompra)")
            for k in orphans[:5]:
                print(f"   · {k}")
    except Exception as rec_err:
        print(f"⚠️ No se pudo reconciliar idempotencia: {rec_err}")

    try:
        print(f"🤖 Conectando bot ({crypto_util.mask_email(bot_credenciales['email'])}) a IQ Option...")
        iq_session = _connect(bot_credenciales['email'], bot_credenciales['password'])
        print("✅ Bot conectado exitosamente.")
    except Exception as e:
        print(f"❌ ERROR FATAL al conectar el bot: {e}")
        _set_fase_bot('error', f'No se pudo conectar a IQ Option: {e}')
        database.detener_bot_servidor()
        return

    session_activa = {
        'email': bot_credenciales['email'],
        'iq': iq_session
    }

    print(f"⏰ Intervalo: {bot_config.get('intervalo', 5)} minutos")
    print(f"🎮 Modo: {bot_config.get('modo', 'demo').upper()}")
    print(f"📊 Configuración riesgo: {bot_config.get('riesgo_porcentaje', 2)}%")
    
    # 🔥 TIMING PRECISO: Calcular el próximo ciclo exacto
    intervalo_segundos = bot_config.get('intervalo', 5) * 60
    siguiente_ciclo = time.time()
    
    # Estadísticas de inicio
    bot_stats['inicio_timestamp'] = time.time()
    bot_stats['proxima_operacion_timestamp'] = siguiente_ciclo
    bot_stats['ciclo_actual'] = 0
    database.actualizar_estadisticas_bot(bot_stats)
    _set_fase_bot(
        'esperando',
        'Núcleo activo. Esperando el primer ciclo de análisis.',
        ciclo=0,
        modo=bot_config.get('modo', 'demo'),
    )
    
    ciclo_numero = 0
    
    while database.esta_activo_bot_servidor():
        try:
            ciclo_numero += 1
            bot_stats['ciclo_actual'] = ciclo_numero
            tiempo_actual = time.time()
            
            # 🔥 TIMING PRECISO: Esperar hasta el próximo ciclo exacto
            tiempo_espera = siguiente_ciclo - tiempo_actual
            if tiempo_espera > 0:
                _set_fase_bot(
                    'esperando',
                    f'Esperando próximo ciclo ({int(tiempo_espera)}s). Modo {bot_config.get("modo", "demo").upper()}.',
                    ciclo=ciclo_numero,
                    modo=bot_config.get('modo', 'demo'),
                )
                # Espera precisa en segmentos pequeños para poder detener el bot
                segmentos = int(tiempo_espera)
                for i in range(segmentos):
                    if not database.esta_activo_bot_servidor():
                        _set_fase_bot('detenido', 'El núcleo fue detenido.')
                        return
                    # Heartbeat cada ~15s mientras espera
                    if i % 15 == 0:
                        restante = max(0, int(siguiente_ciclo - time.time()))
                        _set_fase_bot(
                            'esperando',
                            f'Esperando próximo ciclo ({restante}s). Modo {bot_config.get("modo", "demo").upper()}.',
                            ciclo=ciclo_numero,
                            modo=bot_config.get('modo', 'demo'),
                        )
                    time.sleep(1)
                if tiempo_espera - segmentos > 0:
                    time.sleep(tiempo_espera - segmentos)
            
            # Calcular próximo ciclo
            siguiente_ciclo = time.time() + intervalo_segundos
            bot_stats['proxima_operacion_timestamp'] = siguiente_ciclo
            database.actualizar_estadisticas_bot(bot_stats)
            
            print(f"\n{'='*60}")
            print(f"🤖 BOT SERVIDOR - CICLO {ciclo_numero}")
            print(f"{'='*60}")
            print(f"👤 Usuario: {crypto_util.mask_email(session_activa['email'])}")
            print(f"⏰ Hora actual: {time.strftime('%H:%M:%S')}")
            print(f"⏰ Próxima operación: {time.strftime('%H:%M:%S', time.localtime(siguiente_ciclo))}")
            print(f"{'='*60}")
            
            # Stop / racha: operar.py ya bloquea. Si viene STOP_LOSS, apagar el loop 24/7.
            stop_loss_diario = bot_config.get('stop_loss_diario', 5)
            
            from operar import ACTIVO, candle_open_unix
            candle_ts = candle_open_unix()
            claimed_cycle, cycle_key = database.claim_candle_cycle(
                ACTIVO,
                candle_ts,
                {'ciclo': ciclo_numero, 'modo': bot_config.get('modo', 'demo')},
            )
            if not claimed_cycle:
                print(f"⏭️  Vela {candle_ts} ya procesada ({cycle_key}) — skip ciclo (idempotencia)")
                _set_fase_bot(
                    'esperando',
                    f'Vela {candle_ts} ya analizada. Esperando próxima vela (idempotencia).',
                    ciclo=ciclo_numero,
                    modo=bot_config.get('modo', 'demo'),
                    candle_open=candle_ts,
                )
                # Empujar siguiente ciclo al borde de la próxima vela
                siguiente_ciclo = candle_ts + max(intervalo_segundos, 300)
                bot_stats['proxima_operacion_timestamp'] = siguiente_ciclo
                database.actualizar_estadisticas_bot(bot_stats)
                continue

            # Chaos DEMO: matar justo después de reclamar la vela
            try:
                from operar import _chaos_kill
                _chaos_kill('after_cycle_claim')
            except Exception:
                pass

            _set_fase_bot(
                'analizando',
                f'Ciclo {ciclo_numero}: leyendo velas y evaluando señal EMA/MACD/BB...',
                ciclo=ciclo_numero,
                modo=bot_config.get('modo', 'demo'),
                candle_open=candle_ts,
            )

            # 🔥 EJECUTAR OPERACIÓN (idempotencia en buy)
            resultado = ejecutar_operacion(
                session_activa['iq'],
                modo=bot_config.get('modo', 'demo'),
                monto=bot_config.get('monto'),
                ejecutar_auto=True,
                forzar_operacion=False,
                enforce_idempotency=True,
                config_riesgo={
                    'riesgo_porcentaje': bot_config.get('riesgo_porcentaje', 2.0),
                    'max_perdidas_consecutivas': bot_config.get('max_perdidas_consecutivas', 4),
                    'stop_loss_diario': bot_config.get('stop_loss_diario', 5),
                    'monto_maximo': bot_config.get('monto_maximo', 10)
                }
            )
            database.finalize_idempotency(
                cycle_key,
                'done',
                decision=(resultado.get('decision') or 'SKIP'),
                ejecutado=bool(resultado.get('ejecutado')),
                trade_id=resultado.get('trade_id'),
            )

            decision = (resultado.get('decision') or 'SKIP').upper()
            # Investigación: loguear TODA señal (también SKIP) sin cambiar reglas
            try:
                database.registrar_senal_investigacion({
                    'decision': resultado.get('decision'),
                    'razon': resultado.get('razon'),
                    'score': resultado.get('score'),
                    'score_call': resultado.get('score_call'),
                    'score_put': resultado.get('score_put'),
                    'componentes': resultado.get('componentes') or {},
                    'lado_hipotetico': resultado.get('lado_hipotetico') or (
                        'call' if 'CALL' in decision else ('put' if 'PUT' in decision else None)
                    ),
                    'precio_entrada': resultado.get('precio_entrada'),
                    'probabilidad': resultado.get('probabilidad'),
                    'ejecutado': bool(resultado.get('ejecutado')),
                    'trade_id': resultado.get('trade_id'),
                    'modo': bot_config.get('modo', 'demo'),
                    'activo': resultado.get('activo') or 'EURUSD-OTC',
                    'expiracion_min': resultado.get('expiracion_min') or 5,
                    'candle_open': resultado.get('candle_open'),
                })
            except Exception as res_err:
                print(f"⚠️ No se pudo registrar señal de investigación: {res_err}")

            # Resolver hipotéticos vencidos (sin ejecutar trade)
            try:
                from operar import precio_en_timestamp, evaluar_resultado_hipotetico
                for pend in database.listar_senales_pendientes_hipoteticas(30):
                    lado = pend.get('lado_hipotetico')
                    entrada = pend.get('precio_entrada')
                    if not lado or entrada is None:
                        database.actualizar_senal_hipotetica(pend.get('id'), {
                            'pendiente': False, 'skip_reason': 'sin_lado_o_precio'
                        })
                        continue
                    salida = precio_en_timestamp(session_activa['iq'], float(pend.get('resolve_at') or time.time()))
                    if salida is None:
                        continue
                    hyp = evaluar_resultado_hipotetico(float(entrada), float(salida), lado)
                    hyp['pendiente'] = False
                    database.actualizar_senal_hipotetica(pend.get('id'), hyp)
            except Exception as hyp_err:
                print(f"⚠️ Hipotéticos: {hyp_err}")

            if resultado.get('ejecutado'):
                _set_fase_bot(
                    'operando',
                    f'Ciclo {ciclo_numero}: ejecutó {decision}. Esperando resultado del trade...',
                    ciclo=ciclo_numero,
                    decision=decision,
                    modo=bot_config.get('modo', 'demo'),
                )
            else:
                _set_fase_bot(
                    'resultado',
                    f'Ciclo {ciclo_numero}: {decision} — {resultado.get("razon") or "sin ejecución"}',
                    ciclo=ciclo_numero,
                    decision=decision,
                    modo=bot_config.get('modo', 'demo'),
                )
            
            # 🔥 ACTUALIZAR ESTADÍSTICAS
            bot_stats['operaciones_ejecutadas'] += 1
            ultima_operacion = {
                'timestamp': time.time(),
                'resultado': resultado,
                'numero_operacion': bot_stats['operaciones_ejecutadas'],
                'ciclo': ciclo_numero
            }
            database.guardar_ultima_operacion_bot(ultima_operacion)
            
            if resultado.get('decision') in ('STOP_LOSS',) or (resultado.get('estadisticas_riesgo') or {}).get('bloqueado'):
                print(f"🛑 Riesgo: bot detenido ({resultado.get('razon')})")
                _set_fase_bot(
                    'riesgo',
                    f'Detenido por riesgo: {resultado.get("razon") or "stop / racha"}',
                    ciclo=ciclo_numero,
                    decision=decision,
                )
                database.detener_bot_servidor()
                bot_stats['ultima_operacion_timestamp'] = time.time()
                database.actualizar_estadisticas_bot(bot_stats)
                database.agregar_operacion(resultado)
                break

            if resultado.get('ejecutado'):
                bot_stats['operaciones_exitosas'] += 1
                if resultado.get('resultado_trade') and resultado['resultado_trade'].get('finalizada'):
                    ganancia = resultado['resultado_trade'].get('ganancia', 0)
                    bot_stats['ganancia_total'] += ganancia
                    print(f"💰 Resultado: {'✅ GANANCIA' if ganancia > 0 else '❌ PÉRDIDA'} - ${abs(ganancia):.2f}")
                    win_txt = 'WIN' if ganancia > 0 else ('EMPATE' if ganancia == 0 else 'LOSS')
                    _set_fase_bot(
                        'resultado',
                        f'Ciclo {ciclo_numero}: {decision} → {win_txt} (${ganancia:+.2f}). Esperando próximo ciclo.',
                        ciclo=ciclo_numero,
                        decision=decision,
                        modo=bot_config.get('modo', 'demo'),
                    )
            
            bot_stats['ultima_operacion_timestamp'] = time.time()
            database.actualizar_estadisticas_bot(bot_stats)
            database.agregar_operacion(resultado)
            
            # 🔥 MOSTRAR ESTADÍSTICAS ACTUALIZADAS
            print(f"📊 ESTADÍSTICAS BOT 24/7:")
            print(f"   Operaciones totales: {bot_stats['operaciones_ejecutadas']}")
            print(f"   Operaciones exitosas: {bot_stats['operaciones_exitosas']}")
            print(f"   Ganancia total: ${bot_stats['ganancia_total']:.2f}")
            print(f"   Stop loss diario: ${stop_loss_diario}")
            print(f"⏳ Próxima operación: {time.strftime('%H:%M:%S', time.localtime(siguiente_ciclo))}")
            print(f"{'='*60}\n")

            if database.esta_activo_bot_servidor() and not resultado.get('ejecutado'):
                _set_fase_bot(
                    'esperando',
                    f'Última señal {decision}. Esperando próximo ciclo.',
                    ciclo=ciclo_numero,
                    decision=decision,
                    modo=bot_config.get('modo', 'demo'),
                )
                
        except Exception as e:
            print(f"❌ ERROR en bot servidor: {e}")
            traceback.print_exc()
            _set_fase_bot('error', f'Error en ciclo: {e}. Reintentando en 2 min.')
            # En caso de error, esperar 2 minutos antes de reintentar
            siguiente_ciclo = time.time() + 120
            bot_stats['proxima_operacion_timestamp'] = siguiente_ciclo
            database.actualizar_estadisticas_bot(bot_stats)
    
    vivo = database.obtener_estado_vivo()
    if vivo.get('fase') not in ('error', 'riesgo'):
        _set_fase_bot('detenido', 'El núcleo está detenido.')
    print("🛑 BOT SERVIDOR DETENIDO")

class MyHttpRequestHandler(http.server.BaseHTTPRequestHandler):
    
    def log_message(self, format, *args):
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")
    
    def _cors_origin(self):
        origin = (self.headers.get('Origin') or '').strip().rstrip('/')
        if origin in ALLOWED_ORIGINS:
            return origin
        return None

    def end_headers(self):
        allowed = self._cors_origin()
        if allowed:
            self.send_header('Access-Control-Allow-Origin', allowed)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
            self.send_header('Access-Control-Max-Age', '86400')
        super().end_headers()

    def do_OPTIONS(self):
        if (self.headers.get('Origin') or '').strip() and not self._cors_origin():
            self.send_response(403)
            super().end_headers()
            return
        self.send_response(204)
        self.end_headers()
    
    def do_GET(self):
        if self.path.rstrip('/') == '/test':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'status': 'ok',
                'message': 'Servidor funcionando correctamente',
                'cwd': CWD
            }).encode('utf-8'))
            return
        
        # 🔥 Endpoint para verificar estado del bot servidor
        elif self.path == '/estado_bot_servidor':
            session = get_authenticated_session(self)
            if not session:
                self.send_response(401)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': 'No autorizado'
                }).encode('utf-8'))
                return
            
            db_data = database.load_database()
            bot_stats = db_data['bot_servidor']['estadisticas']
            bot_config = db_data['bot_servidor']['config']
            
            proxima_operacion = bot_stats.get('proxima_operacion_timestamp')
            tiempo_restante = None
            if proxima_operacion:
                tiempo_restante = max(0, proxima_operacion - time.time())
            
            thread_vivo = bool(bot_servidor_thread and bot_servidor_thread.is_alive())
            estado_vivo = database.obtener_estado_vivo()
            # Si la DB dice activo pero el thread murió, avisar al dashboard
            bot_activo = bool(db_data['bot_servidor']['activo'])
            if bot_activo and not thread_vivo:
                estado_vivo = dict(estado_vivo or {})
                estado_vivo['fase'] = 'error'
                estado_vivo['mensaje'] = 'El flag dice activo, pero el proceso del núcleo no está corriendo. Reiniciá el 24/7.'

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': True,
                'bot_activo': bot_activo,
                'thread_vivo': thread_vivo,
                'config': bot_config,
                'estadisticas': bot_stats,
                'estado_vivo': estado_vivo,
                'idempotencia_policy': 'at-most-once',
                'idempotencia_alerta': (estado_vivo or {}).get('idempotencia_alerta'),
                'idempotencia_uncertain': database.list_uncertain_idempotency(),
                'chaos_arm': database.get_chaos_arm(),
                'observabilidad': database.observabilidad_stats(),
                'ultima_operacion': database.obtener_ultima_operacion_bot(),
                'ultima_operacion_timestamp': bot_stats.get('ultima_operacion_timestamp'),
                'proxima_operacion_timestamp': proxima_operacion,
                'tiempo_restante_segundos': tiempo_restante,
                'intervalo': bot_config.get('intervalo', 5),
                'modo': bot_config.get('modo', 'demo')
            }).encode('utf-8'))
            return


        elif self.path.split('?')[0] == '/pago_info':
            try:
                cfg = payment_tron.payment_configured()
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'enabled': cfg,
                    'network': 'TRC20',
                    'currency': 'USDT',
                    'base_amount': payment_tron.base_amount_usdt() if cfg else None,
                    'address': payment_tron.payment_address() if cfg else None,
                    'contract': payment_tron.USDT_TRC20 if cfg else None,
                }).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode('utf-8'))
            return

        elif self.path.split('?')[0] == '/pago_estado':
            try:
                qs = parse_qs(urlparse(self.path).query)
                pago_id = (qs.get('id') or [''])[0].strip()
                email = (qs.get('email') or [''])[0].strip().lower()
                # auto-check pending on poll
                try:
                    verificar_pagos_pendientes()
                except Exception:
                    pass
                pago = database.obtener_pago(pago_id) if pago_id else None
                lic_ok = database.tiene_licencia_activa(email) if email else False
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                # no exponer match.raw enorme
                safe_pago = None
                if pago:
                    safe_pago = {k: pago.get(k) for k in (
                        'id', 'email', 'amount_usdt', 'currency', 'network', 'address',
                        'status', 'created_at', 'expires_at', 'paid_at', 'txid'
                    )}
                self.wfile.write(json.dumps({
                    'success': True,
                    'pago': safe_pago,
                    'license_active': lic_ok,
                }).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': crypto_util.safe_client_error(e)}).encode('utf-8'))
            return

        elif self.path.split('?')[0] == '/investigacion_stats':
            session = get_authenticated_session(self)
            if not session:
                self.send_response(401)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': 'No autorizado'}).encode('utf-8'))
                return
            try:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'por_score': database.stats_investigacion_por_score(),
                    'recientes': database.listar_investigacion(40),
                }).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': crypto_util.safe_client_error(e)}).encode('utf-8'))
            return

        elif self.path.split('?')[0] == '/estadisticas_reales':
            session = get_authenticated_session(self)
            if not session:
                self.send_response(401)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': 'No autorizado'}).encode('utf-8'))
                return
            try:
                stats = database.calcular_estadisticas_reales()
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'stats': stats,
                    'uncertain': database.list_uncertain_idempotency(),
                    'idempotencia_alerta': database.obtener_estado_vivo().get('idempotencia_alerta'),
                    'investigacion_por_score': database.stats_investigacion_por_score(),
                }).encode('utf-8'))
            except Exception as e:
                print(f"❌ Error en /estadisticas_reales: {e}")
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': crypto_util.safe_client_error(e)}).encode('utf-8'))
            return

        elif self.path == '/historial_operaciones':
            session = get_authenticated_session(self)
            if not session:
                self.send_response(401)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': 'No autorizado'
                }).encode('utf-8'))
                return
            
            try:
                # Obtener el límite de operaciones desde los parámetros de la URL
                query_components = parse_qs(urlparse(self.path).query)
                limit = int(query_components.get('limit', [50])[0])

                historial = database.obtener_historial(limit=limit)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'historial': historial
                }).encode('utf-8'))

            except Exception as e:
                print(f"❌ Error en /historial_operaciones: {e}")
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': 'Error al obtener el historial'
                }).encode('utf-8'))
            return

        elif self.path == '/check_session':
            try:
                session = get_authenticated_session(self)
                if session:
                    real_balance, demo_balance, real_id, demo_id = obtener_balances_reales(session['iq'])
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'session_valid': True,
                        'user_data': {
                            'user': {'username': session['email'].split('@')[0], 'email': session['email']},
                            'real': {'balance': real_balance, 'accountId': real_id or 'real_123'},
                            'practice': {'balance': demo_balance, 'accountId': demo_id or 'demo_123'}
                        }
                    }).encode('utf-8'))
                else:
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'session_valid': False,
                        'message': 'Sesión no válida o expirada'
                    }).encode('utf-8'))
            except Exception as e:
                print(f"❌ Error en check_session: {e}")
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': str(e)
                }).encode('utf-8'))
            return

        elif self.path == '/debug_sessions':
            # Solo con SYNAPSE_DEBUG=1 (nunca expuesto en prod por defecto)
            if os.environ.get('SYNAPSE_DEBUG', '').strip() != '1':
                self.send_error(404, "Not Found")
                return
            session = get_authenticated_session(self)
            if not session:
                self.send_response(401)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': 'No autorizado'
                }).encode('utf-8'))
                return
            db_data = database.load_database()
            creds = db_data['bot_servidor'].get('credenciales') or {}
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'active_sessions_count': len(active_sessions),
                'session_tokens_count': len(session_tokens),
                'bot_activo': db_data['bot_servidor']['activo'],
                'bot_tiene_credenciales': bool(creds),
                'bot_credenciales_email_match': (
                    bool(creds) and creds.get('email') == session.get('email')
                ),
            }).encode('utf-8'))
            return
        
        # Servir archivos estáticos
        path_to_serve = 'index.html' if self.path == '/' else self.path.lstrip('/')
        requested_path = os.path.abspath(os.path.join(CWD, path_to_serve))
        
        if not requested_path.startswith(CWD):
            self.send_error(403, "Forbidden")
            return

        try:
            with open(requested_path, 'rb') as file:
                self.send_response(200)
                
                if requested_path.endswith(".html"):
                    self.send_header('Content-type', 'text/html; charset=utf-8')
                    self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
                    self.send_header('Pragma', 'no-cache')
                elif requested_path.endswith(".css"):
                    self.send_header('Content-type', 'text/css')
                elif requested_path.endswith(".js"):
                    self.send_header('Content-type', 'application/javascript')
                else:
                    self.send_header('Content-type', 'application/octet-stream')
                
                self.end_headers()
                self.wfile.write(file.read())
                
        except FileNotFoundError:
            self.send_error(404, f'File Not Found: {path_to_serve}')
        except Exception as e:
            print(f"❌ Error en GET: {e}")
            traceback.print_exc()
            self.send_error(500, f'Server Error: {e}')

    def do_POST(self):
        global bot_servidor_thread
        
        try:
            if self.path == '/login':
                try:
                    content_length = int(self.headers.get('Content-Length', 0))
                    if content_length == 0:
                        raise Exception("Request body vacío")
                    
                    post_data = self.rfile.read(content_length)
                    credentials = json.loads(post_data.decode('utf-8'))
                    
                    email = credentials.get('email', '').strip()
                    password = credentials.get('password', '').strip()
                    
                    if not email or not password:
                        raise Exception("Email y password son requeridos")
                    if payment_tron.payment_configured() and not database.tiene_licencia_activa(email):
                        raise Exception(
                            "LICENSE_REQUIRED: Acceso de pago requerido (100 USDT TRC20). "
                            "Andá a Pagar acceso, enviá el monto exacto y esperá la confirmación automática."
                        )
                    if not crypto_util.credentials_key_configured():
                        raise Exception(
                            "Falta SYNAPSE_CREDENTIALS_KEY en el servidor. "
                            "Configurá el secret en Fly antes de iniciar sesión."
                        )
                    
                    masked = crypto_util.mask_email(email)
                    print(f"\n{'='*70}")
                    print(f"🔥 LOGIN REQUEST")
                    print(f"{'='*70}")
                    print(f"📧 Email: {masked}")
                    print(f"{'='*70}\n")

                    # Revocar sesión previa del mismo email
                    if email in session_tokens:
                        existing_token = session_tokens[email]
                        SessionManager.delete_session(existing_token)
                        print(f"🔄 Sesión anterior eliminada para {masked}")

                    # Aislamiento: si hay bot 24/7 con OTRO email, no mezclar credenciales
                    prev = database.obtener_credenciales_bot() or {}
                    prev_email = (prev.get('email') or '').strip()
                    if prev_email and prev_email.lower() != email.lower():
                        if database.esta_activo_bot_servidor():
                            print(f"🛑 Deteniendo bot de {crypto_util.mask_email(prev_email)} por cambio de usuario")
                            database.detener_bot_servidor()
                            database.actualizar_estado_vivo(
                                'detenido',
                                'Núcleo detenido: otro usuario inició sesión.',
                            )
                        database.limpiar_credenciales_bot()
                        SessionManager.delete_session_by_email(prev_email)

                    # Conectar a IQ Option
                    print("⏳ Conectando a IQ Option...")
                    iq_session = _connect(email, password)
                    print("✅ Conexión establecida.")

                    # Guardar credenciales CIFRADAS solo del usuario actual
                    database.guardar_credenciales_bot({'email': email, 'password': password})
                    print("🔐 Credenciales cifradas guardadas para el bot 24/7.")
                    
                    # Obtener balances REALES
                    real_balance, demo_balance, real_id, demo_id = obtener_balances_reales(iq_session)
                    
                    # Crear nueva sesión
                    token = SessionManager.create_session(email, iq_session)
                    print(f"🔑 Token de sesión generado: {token[:8]}...")

                    # Respuesta exitosa con balances reales
                    response_data = {
                        "success": True,
                        "session_token": token,
                        "data": {
                            "user": {
                                "username": email.split("@")[0],
                                "email": email,
                                "userId": "user_123"
                            },
                            "real": {
                                "balance": real_balance,
                                "accountId": real_id or "real_123",
                                "currency": "USD",
                                "type": "REAL"
                            },
                            "practice": {
                                "balance": demo_balance,
                                "accountId": demo_id or "demo_123", 
                                "currency": "USD",
                                "type": "PRACTICE"
                            }
                        }
                    }

                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(response_data).encode('utf-8'))
                    
                    print(f"✅ LOGIN EXITOSO para {crypto_util.mask_email(email)}")
                    print(f"💰 Balances - Real: ${real_balance}, Demo: ${demo_balance}\n")

                except Exception as e:
                    print(f"❌ ERROR en login: {crypto_util.safe_client_error(e)}")
                    traceback.print_exc()
                    raw = str(e)
                    license_required = raw.startswith('LICENSE_REQUIRED')
                    client_msg = crypto_util.safe_client_error(
                        e, fallback="No se pudo iniciar sesión. Revisá credenciales o intentá de nuevo."
                    )
                    if license_required:
                        client_msg = raw.split('LICENSE_REQUIRED:', 1)[-1].strip()
                    self.send_response(402 if license_required else 500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': client_msg,
                        'license_required': license_required,
                    }).encode('utf-8'))
            

            elif self.path == '/crear_pago':
                try:
                    content_length = int(self.headers.get('Content-Length', 0))
                    body = json.loads(self.rfile.read(content_length).decode('utf-8') if content_length else '{}')
                    email = (body.get('email') or '').strip().lower()
                    pago = database.crear_pago_pendiente(email)
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'pago': {
                            'id': pago['id'],
                            'email': pago['email'],
                            'amount_usdt': pago['amount_usdt'],
                            'currency': pago['currency'],
                            'network': pago['network'],
                            'address': pago['address'],
                            'expires_at': pago['expires_at'],
                            'status': pago['status'],
                        },
                        'instrucciones': (
                            f"Enviá exactamente {pago['amount_usdt']} USDT en red TRC20 (Tron) a la address indicada. "
                            "El monto con centavos identifica tu pago. La activación es automática al confirmarse en blockchain."
                        ),
                    }).encode('utf-8'))
                except Exception as e:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': crypto_util.safe_client_error(e, 'No se pudo crear el pago'),
                    }).encode('utf-8'))

            elif self.path == '/verificar_pago':
                try:
                    content_length = int(self.headers.get('Content-Length', 0))
                    body = json.loads(self.rfile.read(content_length).decode('utf-8') if content_length else '{}')
                    pago_id = (body.get('id') or '').strip()
                    results = verificar_pagos_pendientes(only_id=pago_id or None)
                    pago = database.obtener_pago(pago_id) if pago_id else None
                    email = (pago or {}).get('email') or (body.get('email') or '').strip().lower()
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'checked': results,
                        'pago': pago and {k: pago.get(k) for k in (
                            'id', 'email', 'amount_usdt', 'status', 'txid', 'paid_at', 'address', 'network'
                        )},
                        'license_active': database.tiene_licencia_activa(email) if email else False,
                    }).encode('utf-8'))
                except Exception as e:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': crypto_util.safe_client_error(e, 'No se pudo verificar el pago'),
                    }).encode('utf-8'))

            elif self.path == '/logout':
                try:
                    session = get_authenticated_session(self)
                    if session:
                        email = session['email']
                        SessionManager.delete_session_by_email(email)
                        # Si el bot no está activo y las credenciales son de este user, limpiar
                        if not database.esta_activo_bot_servidor():
                            creds = database.obtener_credenciales_bot() or {}
                            if (creds.get('email') or '').lower() == email.lower():
                                database.limpiar_credenciales_bot()
                        print(f"✅ Sesión cerrada para {crypto_util.mask_email(email)}")

                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'message': 'Sesión cerrada correctamente'
                    }).encode('utf-8'))

                except Exception as e:
                    print(f"❌ ERROR en logout: {crypto_util.safe_client_error(e)}")
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': 'No se pudo cerrar la sesión'
                    }).encode('utf-8'))

            elif self.path == '/force_logout':
                # Solo el usuario autenticado puede revocar SU propia sesión
                try:
                    session = get_authenticated_session(self)
                    if not session:
                        self.send_response(401)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            'success': False,
                            'error': 'No autorizado'
                        }).encode('utf-8'))
                    else:
                        email = session['email']
                        SessionManager.delete_session_by_email(email)
                        print(f"🔄 Sesión forzada cerrada para {crypto_util.mask_email(email)}")
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            'success': True,
                            'message': 'Sesión revocada'
                        }).encode('utf-8'))

                except Exception as e:
                    print(f"❌ ERROR en force_logout: {crypto_util.safe_client_error(e)}")
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': 'No se pudo revocar la sesión'
                    }).encode('utf-8'))
            
            elif self.path == '/operar':
                try:
                    session = get_authenticated_session(self)
                    if not session:
                        self.send_response(401)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            'success': False,
                            'error': 'Sesión no válida. Por favor, inicie sesión nuevamente.',
                            'session_expired': True
                        }).encode('utf-8'))
                        return
                    
                    content_length = int(self.headers.get('Content-Length', 0))
                    post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
                    config = json.loads(post_data.decode('utf-8'))
                    
                    modo = config.get('modo', 'demo')
                    monto = config.get('monto')
                    ejecutar_auto = config.get('ejecutar_auto', True)
                    forzar_operacion = config.get('forzar_operacion', False)
                    
                    print(f"\n{'='*70}")
                    print(f"🎯 OPERACIÓN MANUAL SOLICITADA")
                    print(f"{'='*70}")
                    print(f"Usuario: {session['email']}")
                    print(f"Modo: {modo.upper()}")
                    print(f"Monto: {'AUTO' if monto is None else f'${monto}'}")
                    print(f"Auto: {'SÍ' if ejecutar_auto else 'NO'}")
                    print(f"Forzar: {'SÍ' if forzar_operacion else 'NO'}")
                    print(f"{'='*70}\n")
                    
                    # EJECUTAR OPERACIÓN MANUAL
                    resultado = ejecutar_operacion(
                        session['iq'],
                        modo=modo,
                        monto=monto,
                        ejecutar_auto=ejecutar_auto,
                        forzar_operacion=forzar_operacion,
                        config_riesgo={
                            'riesgo_porcentaje': config.get('riesgo_porcentaje', 2.0),
                            'max_perdidas_consecutivas': config.get('max_perdidas_consecutivas', 4),
                            'stop_loss_diario': config.get('stop_loss_diario', 5),
                            'monto_maximo': config.get('monto_maximo', 10)
                        }
                    )
                    
                    # Obtener balances actualizados después de la operación
                    real_balance, demo_balance, real_id, demo_id = obtener_balances_reales(session['iq'])
                    
                    # Agregar balances actualizados al resultado
                    resultado['balances_actualizados'] = {
                        'real': real_balance,
                        'demo': demo_balance
                    }
                    
                    # Guardar operación en la base de datos
                    database.agregar_operacion(resultado)
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    
                    try:
                        self.wfile.write(json.dumps(resultado).encode('utf-8'))
                        print(f"✅ Operación MANUAL completada\n")
                    except BrokenPipeError:
                        print("⚠️ Cliente cerró la conexión antes de recibir la respuesta completa")
                    
                except Exception as e:
                    error_msg = str(e)
                    print(f"❌ ERROR en operación manual: {error_msg}")
                    traceback.print_exc()
                    
                    try:
                        self.send_response(500)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            'success': False,
                            'error': error_msg
                        }).encode('utf-8'))
                    except BrokenPipeError:
                        print("⚠️ Cliente cerró la conexión durante el manejo de error")
            
            # 🔥 BOT 24/7 - OPERACIÓN AUTOMÁTICA EN SERVIDOR
            
            elif self.path == '/iniciar_bot_servidor':
                try:
                    session = get_authenticated_session(self)
                    if not session:
                        raise Exception("No hay sesión activa")

                    content_length = int(self.headers.get('Content-Length', 0))
                    post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
                    config = json.loads(post_data.decode('utf-8'))

                    with bot_start_lock:
                        unc = database.unresolved_uncertain_count()
                        if unc > 0:
                            raise Exception(
                                f"Hay {unc} operación(es) incierta(s). "
                                "Reconciliá con la cuenta IQ o resolvé manualmente antes de continuar."
                            )
                        if database.esta_activo_bot_servidor() or (
                            bot_servidor_thread and bot_servidor_thread.is_alive()
                        ):
                            raise Exception("El bot servidor ya está activo")

                        # Credenciales del login; deben ser del mismo usuario autenticado
                        credenciales = database.obtener_credenciales_bot()
                        if not credenciales or not credenciales.get('password'):
                            raise Exception("No se encontraron credenciales guardadas. Por favor, inicie sesión de nuevo.")
                        if (credenciales.get('email') or '').lower() != (session.get('email') or '').lower():
                            raise Exception(
                                "Las credenciales guardadas no coinciden con tu sesión. "
                                "Cerrá sesión e iniciá de nuevo."
                            )
                        database.guardar_credenciales_bot({
                            'email': session['email'],
                            'password': credenciales['password'],
                        })

                        database.guardar_config_bot(config)

                        nuevas_estadisticas = {
                            'operaciones_ejecutadas': 0,
                            'operaciones_exitosas': 0,
                            'ganancia_total': 0.0,
                            'ultima_operacion_timestamp': None,
                            'inicio_timestamp': time.time(),
                            'proxima_operacion_timestamp': None
                        }
                        database.actualizar_estadisticas_bot(nuevas_estadisticas)
                        database.actualizar_estado_vivo(
                            'conectando',
                            'Iniciando núcleo 24/7...',
                            modo=config.get('modo', 'demo'),
                        )

                        bot_servidor_thread = threading.Thread(target=ejecutar_bot_servidor)
                        bot_servidor_thread.daemon = True
                        bot_servidor_thread.start()

                    print(f"🚀 BOT 24/7 INICIADO para {crypto_util.mask_email(session['email'])}")
                    print(f"📋 Configuración: {config}")
                    print(f"🔐 Credenciales guardadas para reconexión automática")

                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'message': 'Bot 24/7 iniciado en servidor - Funciona independientemente del cliente',
                        'config': config
                    }).encode('utf-8'))
                    
                except Exception as e:
                    error_msg = str(e)
                    print(f"❌ ERROR iniciando bot servidor: {error_msg}")
                    
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': error_msg
                    }).encode('utf-8'))
            
            elif self.path == '/detener_bot_servidor':
                try:
                    session = get_authenticated_session(self)
                    if not session:
                        raise Exception("No hay sesión activa")
                    
                    if not database.esta_activo_bot_servidor():
                        raise Exception("El bot servidor no está activo")
                    
                    database.actualizar_estado_vivo('detenido', 'El núcleo fue detenido desde el dashboard.')
                    database.detener_bot_servidor()
                    creds = database.obtener_credenciales_bot() or {}
                    if (creds.get('email') or '').lower() == (session.get('email') or '').lower():
                        database.limpiar_credenciales_bot()
                    print(f"🛑 BOT 24/7 DETENIDO por {crypto_util.mask_email(session['email'])}")
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'message': 'Bot servidor detenido',
                        'estadisticas_finales': database.obtener_estadisticas_bot()
                    }).encode('utf-8'))
                    
                except Exception as e:
                    error_msg = str(e)
                    print(f"❌ ERROR deteniendo bot servidor: {error_msg}")
                    
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': error_msg
                    }).encode('utf-8'))

            elif self.path == '/resolver_idempotencia':
                try:
                    session = get_authenticated_session(self)
                    if not session:
                        raise Exception('No autorizado')
                    content_length = int(self.headers.get('Content-Length', 0))
                    body = json.loads(self.rfile.read(content_length).decode('utf-8') if content_length else '{}')
                    key = (body.get('key') or '').strip()
                    resolution = (body.get('resolution') or '').strip()
                    note = (body.get('note') or '').strip()
                    entry = database.resolve_idempotency(key, resolution, note=note)
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'entry': entry,
                        'key': key,
                        'idempotencia_alerta': database.obtener_estado_vivo().get('idempotencia_alerta'),
                    }).encode('utf-8'))
                except Exception as e:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': crypto_util.safe_client_error(e, 'No se pudo resolver'),
                    }).encode('utf-8'))


            elif self.path == '/reconciliar_idempotencia':
                try:
                    session = get_authenticated_session(self)
                    if not session:
                        raise Exception('No autorizado')
                    content_length = int(self.headers.get('Content-Length', 0))
                    body = json.loads(self.rfile.read(content_length).decode('utf-8') if content_length else '{}')
                    key = (body.get('key') or '').strip()
                    entry = database.get_idempotency(key)
                    if not entry:
                        raise Exception('key no encontrada')
                    if entry.get('status') != 'uncertain_crash':
                        raise Exception('solo se reconcilian uncertain_crash')
                    trade_id = entry.get('trade_id') or body.get('trade_id')
                    monto = entry.get('monto')
                    from operar import reconciliar_trade_id
                    iq = session.get('iq')
                    result = reconciliar_trade_id(iq, trade_id, monto=monto)
                    resolution = None
                    if result.get('found') and result.get('finalizada'):
                        resolution = 'resolved_placed'
                        database.finalize_idempotency(key, 'uncertain_crash', iq_reconcile=result)
                        database.resolve_idempotency(
                            key,
                            'resolved_placed',
                            note=f"IQ reconcile: pnl={result.get('ganancia')} via {result.get('source')}",
                        )
                        # registrar en historial si hay pnl
                        try:
                            database.agregar_operacion({
                                'decision': key.split(':')[-1] if key else 'CALL',
                                'ejecutado': True,
                                'trade_id': trade_id,
                                'probabilidad': 'N/A',
                                'modo': entry.get('modo') or 'demo',
                                'resultado_trade': {
                                    'finalizada': True,
                                    'ganancia': result.get('ganancia') or 0,
                                    'win': bool(result.get('win')),
                                    'id': trade_id,
                                    'reconciled': True,
                                },
                                'timestamp': time.time(),
                                'fuente': 'reconciliacion_iq',
                            })
                        except Exception:
                            pass
                    elif result.get('found') is False and 'no aparece' in (result.get('mensaje') or '').lower():
                        # no evidencia en historial reciente → tratar como no hubo trade visible
                        resolution = None
                        # leave uncertain; client can mark no_trade manually
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'key': key,
                        'iq': result,
                        'auto_resolved': resolution,
                        'idempotencia_alerta': database.obtener_estado_vivo().get('idempotencia_alerta'),
                        'uncertain': database.list_uncertain_idempotency(),
                    }).encode('utf-8'))
                except Exception as e:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': crypto_util.safe_client_error(e, 'No se pudo reconciliar'),
                    }).encode('utf-8'))

            elif self.path == '/chaos_arm':
                try:
                    session = get_authenticated_session(self)
                    if not session:
                        raise Exception('No autorizado')
                    cfg = database.load_database()['bot_servidor'].get('config') or {}
                    if (cfg.get('modo') or 'demo').lower() != 'demo':
                        raise Exception('Chaos solo permitido en DEMO')
                    content_length = int(self.headers.get('Content-Length', 0))
                    body = json.loads(self.rfile.read(content_length).decode('utf-8') if content_length else '{}')
                    point = (body.get('point') or '').strip()
                    arm = database.arm_chaos(point, armed_by=crypto_util.mask_email(session.get('email') or ''))
                    print(f"CHAOS armado: {point}")
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'chaos_arm': arm,
                        'message': f'Chaos one-shot armado en {point}. El próximo ciclo que pase por ese punto matará el proceso.',
                    }).encode('utf-8'))
                except Exception as e:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': crypto_util.safe_client_error(e, 'No se pudo armar chaos'),
                    }).encode('utf-8'))

            elif self.path == '/chaos_clear':
                try:
                    session = get_authenticated_session(self)
                    if not session:
                        raise Exception('No autorizado')
                    database.clear_chaos_arm()
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'success': True}).encode('utf-8'))
                except Exception as e:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode('utf-8'))

            elif self.path == '/reset_riesgo':
                try:
                    session = get_authenticated_session(self)
                    if not session:
                        raise Exception("No hay sesión activa")
                    
                    print(f"🔄 Riesgo reseteado para {session['email']}")
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'message': 'Estadísticas de riesgo reseteadas'
                    }).encode('utf-8'))
                    
                except Exception as e:
                    error_msg = str(e)
                    print(f"❌ ERROR reseteando riesgo: {error_msg}")
                    
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': error_msg
                    }).encode('utf-8'))
            
            else:
                self.send_error(404, 'Endpoint not found')
                
        except BrokenPipeError:
            print("⚠️ Cliente cerró la conexión abruptamente (BrokenPipeError)")
        except Exception as e:
            print(f"❌ ERROR general en do_POST: {e}")
            traceback.print_exc()
            
            try:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': 'Error interno del servidor'
                }).encode('utf-8'))
            except BrokenPipeError:
                print("⚠️ Cliente cerró la conexión durante el manejo de error general")


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

def cleanup_sessions_periodically():
    """Ejecutar limpieza de sesiones cada hora"""
    while True:
        time.sleep(3600)  # 1 hora
        SessionManager.cleanup_expired_sessions()


def verificar_pagos_pendientes(only_id=None):
    """Revisa TronGrid y otorga licencia automáticamente si matchea el monto."""
    if not payment_tron.payment_configured():
        return []
    confirmed = []
    pendientes = database.listar_pagos_pendientes()
    if only_id:
        pendientes = [p for p in pendientes if p.get('id') == only_id]
    for p in pendientes:
        try:
            created_ms = int(float(p.get('created_at') or 0) * 1000)
            match = payment_tron.find_matching_payment(p['amount_usdt'], created_ms)
            if not match or not match.get('txid'):
                continue
            paid = database.marcar_pago_confirmado(p['id'], match)
            lic = database.otorgar_licencia(paid['email'], {
                'txid': match.get('txid'),
                'amount_usdt': paid.get('amount_usdt'),
                'pago_id': paid.get('id'),
            })
            confirmed.append({'pago_id': p['id'], 'email': paid['email'], 'txid': match.get('txid')})
            print(f"✅ Pago automático confirmado {p['id']} → licencia {paid['email']} tx={match.get('txid')}")
        except Exception as e:
            print(f"⚠️ verificar pago {p.get('id')}: {e}")
    return confirmed


def payment_poller_loop():
    while True:
        try:
            time.sleep(45)
            if payment_tron.payment_configured():
                verificar_pagos_pendientes()
        except Exception as e:
            print(f"⚠️ payment poller: {e}")
            time.sleep(30)

def run_server(port=PORT):
    # SYNAPSE_CREDENTIALS_KEY check
    if not crypto_util.credentials_key_configured():
        print("⚠️  SYNAPSE_CREDENTIALS_KEY no está configurada. El login rechazará guardar credenciales.")

    global bot_servidor_thread

    # Inicializar la base de datos
    database.init_database()

    # Si el bot estaba activo, reiniciar el thread (idempotencia evita re-buy de la misma vela)
    with bot_start_lock:
        if database.esta_activo_bot_servidor() and not (
            bot_servidor_thread and bot_servidor_thread.is_alive()
        ):
            print("🤖 Reiniciando el bot servidor...")
            bot_servidor_thread = threading.Thread(target=ejecutar_bot_servidor)
            bot_servidor_thread.daemon = True
            bot_servidor_thread.start()

    # Iniciar limpieza de sesiones
    cleanup_thread = threading.Thread(target=cleanup_sessions_periodically)
    cleanup_thread.daemon = True
    cleanup_thread.start()
    if payment_tron.payment_configured():
        pay_thread = threading.Thread(target=payment_poller_loop)
        pay_thread.daemon = True
        pay_thread.start()
        print("💳 Payment poller USDT TRC20 activo")
    else:
        print("💳 Paywall desactivado (falta SYNAPSE_USDT_ADDRESS)")
    
    server_address = ('', port)
    
    try:
        httpd = ThreadedHTTPServer(server_address, MyHttpRequestHandler)
        
        print("\n" + "="*70)
        print(f"🚀 SERVIDOR HTTP INICIADO")
        print("="*70)
        print(f"🌐 URL: http://localhost:{port}")
        print(f"📂 Directorio: {CWD}")
        print(f"💾 Datos persistentes: {DATA_DIR}")
        print(f"🔐 Sistema de sesiones activado")
        print(f"🌐 CORS allowlist: {sorted(ALLOWED_ORIGINS)}")
        print(f"🤖 BOT 24/7 ACTIVADO - INDEPENDIENTE DEL CLIENTE")
        print(f"💰 Balances REALES activados")
        print("="*70)
        print("\n✅ Servidor listo para recibir conexiones")
        print("⌨️  Presiona Ctrl+C para detener\n")
        
        httpd.serve_forever()
        
    except OSError as e:
        if "address already in use" in str(e).lower():
            print(f"\n❌ ERROR: El puerto {port} ya está en uso")
            print(f"💡 Solución: Cambia PORT en server.py o cierra el proceso que usa el puerto")
        else:
            print(f"\n❌ ERROR: {e}\n")
            traceback.print_exc()
    except KeyboardInterrupt:
        print("\n\n🛑 Servidor detenido")
        # Detener bot servidor si está activo
        if database.esta_activo_bot_servidor():
            print("🛑 Deteniendo bot servidor...")
            database.detener_bot_servidor()
            if bot_servidor_thread and bot_servidor_thread.is_alive():
                bot_servidor_thread.join(timeout=10)
        
        # Limpiar todas las sesiones
        active_sessions.clear()
        session_tokens.clear()
        httpd.server_close()
    except Exception as e:
        print(f"\n❌ ERROR INESPERADO: {e}\n")
        traceback.print_exc()

if __name__ == '__main__':
    run_server()
