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
from database import (
    init_database,
    load_database,
    save_database,
    agregar_operacion,
    obtener_historial,
    actualizar_estadisticas_bot,
    obtener_estadisticas_bot,
    guardar_config_bot,
    detener_bot_servidor,
    esta_activo_bot_servidor,
    guardar_credenciales_bot,
    obtener_credenciales_bot,
    limpiar_credenciales_bot,
    guardar_ultima_operacion_bot,
    obtener_ultima_operacion_bot
)

PORT = 8000
CWD = os.path.dirname(os.path.abspath(__file__))

# Sistema de sesiones mejorado
active_sessions = {}
session_tokens = {}

# Cargar estado del bot desde la base de datos al iniciar
db_data = load_database()

# 🔥 SISTEMA BOT 24/7 MEJORADO - PERSISTENTE EN SERVIDOR
bot_servidor_activo = db_data['bot_servidor']['activo']
bot_servidor_config = db_data['bot_servidor']['config']
bot_servidor_thread = None
bot_servidor_estadisticas = db_data['bot_servidor']['estadisticas']



class SessionManager:
    SESSION_TIMEOUT = 24 * 3600  # 24 horas
    
    @staticmethod
    def generate_token():
        return str(uuid.uuid4())
    
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
        return token
    
    @staticmethod
    def get_session(token):
        if token in active_sessions:
            session = active_sessions[token]
            # Verificar expiración
            if time.time() - session['last_activity'] > SessionManager.SESSION_TIMEOUT:
                SessionManager.delete_session(token)
                return None
            
            session['last_activity'] = time.time()
            return session
        return None
    
    @staticmethod
    def delete_session(token):
        if token in active_sessions:
            email = active_sessions[token]['email']
            if email in session_tokens:
                del session_tokens[email]
            del active_sessions[token]
    
    @staticmethod
    def delete_session_by_email(email):
        if email in session_tokens:
            token = session_tokens[email]
            SessionManager.delete_session(token)
    
    @staticmethod
    def cleanup_expired_sessions():
        """Limpiar sesiones expiradas"""
        current_time = time.time()
        expired_tokens = []
        
        for token, session_data in active_sessions.items():
            if current_time - session_data['last_activity'] > SessionManager.SESSION_TIMEOUT:
                expired_tokens.append(token)
        
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
def ejecutar_bot_servidor():
    """Ejecuta el bot automático en el servidor de forma continua y precisa"""
    global bot_servidor_thread
    
    db_data = load_database()
    bot_config = db_data['bot_servidor']['config']
    bot_stats = db_data['bot_servidor']['estadisticas']
    bot_credenciales = obtener_credenciales_bot()

    if not bot_credenciales or not bot_credenciales.get('email') or not bot_credenciales.get('password'):
        print("❌ ERROR: Credenciales del bot no configuradas. Deteniendo bot.")
        detener_bot_servidor()
        return

    try:
        print(f"🤖 Conectando bot ({bot_credenciales['email']}) a IQ Option...")
        iq_session = _connect(bot_credenciales['email'], bot_credenciales['password'])
        print("✅ Bot conectado exitosamente.")
    except Exception as e:
        print(f"❌ ERROR FATAL al conectar el bot: {e}")
        detener_bot_servidor()
        return

    session_activa = {
        'email': bot_credenciales['email'],
        'iq': iq_session
    }

    print(f"\n🎯 INICIANDO BOT SERVIDOR 24/7 - INDEPENDIENTE DEL CLIENTE")
    print(f"⏰ Intervalo: {bot_config.get('intervalo', 5)} minutos")
    print(f"🎮 Modo: {bot_config.get('modo', 'demo').upper()}")
    print(f"📊 Configuración riesgo: {bot_config.get('riesgo_porcentaje', 2)}%")
    
    intervalo_segundos = bot_config.get('intervalo', 5) * 60
    siguiente_ciclo = time.time()
    
    bot_stats['inicio_timestamp'] = time.time()
    actualizar_estadisticas_bot(bot_stats)
    
    ciclo_numero = 0
    
    while esta_activo_bot_servidor():
        try:
            ciclo_numero += 1
            tiempo_actual = time.time()
            
            print(f"👤 Usuario: {session_activa['email']}")
            
            stop_loss_diario = bot_config.get('stop_loss_diario', 15)
            if (bot_stats['ganancia_total'] < -abs(stop_loss_diario) and 
                bot_stats['operaciones_ejecutadas'] > 0):
                print(f"🛑 STOP LOSS DIARIO ACTIVADO: ${bot_stats['ganancia_total']:.2f}")
                print("🔴 El bot se detendrá automáticamente")
                detener_bot_servidor()
                break
            
            resultado = ejecutar_operacion(
                session_activa['iq'],
                modo=bot_config.get('modo', 'demo'),
                monto=bot_config.get('monto'),
                ejecutar_auto=True,
                forzar_operacion=False,
                config_riesgo={
                    'riesgo_porcentaje': bot_config.get('riesgo_porcentaje', 2.0),
                    'max_perdidas_consecutivas': bot_config.get('max_perdidas_consecutivas', 3),
                    'stop_loss_diario': bot_config.get('stop_loss_diario', 15),
                    'monto_maximo': bot_config.get('monto_maximo', 10)
                }
            )
            
            bot_stats['operaciones_ejecutadas'] += 1
            ultima_operacion = {
                'timestamp': time.time(),
                'resultado': resultado,
                'numero_operacion': bot_stats['operaciones_ejecutadas'],
                'ciclo': ciclo_numero
            }
            guardar_ultima_operacion_bot(ultima_operacion)
            
            if resultado.get('ejecutado'):
                bot_stats['operaciones_exitosas'] += 1
                if resultado.get('resultado_trade') and resultado['resultado_trade'].get('finalizada'):
                    ganancia = resultado['resultado_trade'].get('ganancia', 0)
                    bot_stats['ganancia_total'] += ganancia
                    print(f"💰 Resultado: {'✅ GANANCIA' if ganancia > 0 else '❌ PÉRDIDA'} - ${abs(ganancia):.2f}")
            
            bot_stats['ultima_operacion_timestamp'] = time.time()
            actualizar_estadisticas_bot(bot_stats)
            agregar_operacion(resultado)
            
            print(f"📊 ESTADÍSTICAS BOT 24/7:")
            print(f"   Operaciones totales: {bot_stats['operaciones_ejecutadas']}")
            print(f"   Operaciones exitosas: {bot_stats['operaciones_exitosas']}")
            print(f"   Ganancia total: ${bot_stats['ganancia_total']:.2f}")
            print(f"   Stop loss diario: ${stop_loss_diario}")
            print(f"⏳ Próxima operación: {time.strftime('%H:%M:%S', time.localtime(siguiente_ciclo))}")
            print(f"{'='*60}\n")
        except Exception as e:
            print(f"❌ ERROR en bot servidor: {e}")
            traceback.print_exc()
            siguiente_ciclo = time.time() + 120
                
    print("🛑 BOT SERVIDOR DETENIDO")
    
class MyHttpRequestHandler(http.server.BaseHTTPRequestHandler):
    
    def log_message(self, format, *args):
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")
    
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        super().end_headers()
    
    def do_OPTIONS(self):
        self.send_response(200)
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
            
            db_data = load_database()
            bot_stats = db_data['bot_servidor']['estadisticas']
            bot_config = db_data['bot_servidor']['config']
            
            proxima_operacion = bot_stats.get('proxima_operacion_timestamp')
            tiempo_restante = None
            if proxima_operacion:
                tiempo_restante = max(0, proxima_operacion - time.time())
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': True,
                'bot_activo': db_data['bot_servidor']['activo'],
                'config': bot_config,
                'estadisticas': bot_stats,
                'ultima_operacion': obtener_ultima_operacion_bot(),
                'ultima_operacion_timestamp': bot_stats['ultima_operacion_timestamp'],
                'proxima_operacion_timestamp': proxima_operacion,
                'tiempo_restante_segundos': tiempo_restante,
                'intervalo': bot_config.get('intervalo', 5)
            }).encode('utf-8'))
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
                limit = int(query_components.get("limit", [50])[0])

                historial = obtener_historial(limit=limit)
                
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
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'active_sessions_count': len(active_sessions),
                'session_tokens_count': len(session_tokens),
                'bot_activo': esta_activo_bot_servidor(),
                'bot_tiene_credenciales': obtener_credenciales_bot() is not None
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
                    
                    print(f"\n{'='*70}")
                    print(f"🔥 LOGIN REQUEST")
                    print(f"{'='*70}")
                    print(f"📧 Email: {email}")
                    print(f"{'='*70}\n")

                    # Verificar si ya hay sesión activa
                    if email in session_tokens:
                        existing_token = session_tokens[email]
                        SessionManager.delete_session(existing_token)
                        print(f"🔄 Sesión anterior eliminada para {email}")

                    # Conectar a IQ Option
                    print("⏳ Conectando a IQ Option...")
                    iq_session = _connect(email, password)
                    print("✅ Conexión establecida.")
                    
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
                    
                    print(f"✅ LOGIN EXITOSO para {email}")
                    print(f"💰 Balances - Real: ${real_balance}, Demo: ${demo_balance}\n")

                except Exception as e:
                    error_msg = str(e)
                    print(f"❌ ERROR en login: {error_msg}")
                    traceback.print_exc()
                    
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False, 
                        'error': error_msg
                    }).encode('utf-8'))
            
            elif self.path == '/logout':
                try:
                    session = get_authenticated_session(self)
                    if session:
                        SessionManager.delete_session_by_email(session['email'])
                        print(f"✅ Sesión cerrada para {session['email']}")
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'message': 'Sesión cerrada correctamente'
                    }).encode('utf-8'))
                    
                except Exception as e:
                    error_msg = str(e)
                    print(f"❌ ERROR en logout: {error_msg}")
                    
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': error_msg
                    }).encode('utf-8'))
            
            elif self.path == '/force_logout':
                try:
                    content_length = int(self.headers.get('Content-Length', 0))
                    if content_length > 0:
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        email = data.get('email', '').strip()
                        
                        if email:
                            SessionManager.delete_session_by_email(email)
                            print(f"🔄 Sesión forzada cerrada para {email}")
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'message': 'Sesiones cerradas en todos los dispositivos'
                    }).encode('utf-8'))
                    
                except Exception as e:
                    error_msg = str(e)
                    print(f"❌ ERROR en force_logout: {error_msg}")
                    
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': False,
                        'error': error_msg
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
                    ejecutar_auto = config.get('ejecutar_auto', False)
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
                            'max_perdidas_consecutivas': config.get('max_perdidas_consecutivas', 3),
                            'stop_loss_diario': config.get('stop_loss_diario', 15),
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
                    agregar_operacion(resultado)
                    
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
                    
                    if esta_activo_bot_servidor():
                        raise Exception("El bot servidor ya está activo")
                    
                    content_length = int(self.headers.get('Content-Length', 0))
                    post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
                    config = json.loads(post_data.decode('utf-8'))
                    
                    credenciales = {
                        'email': session['email'],
                        'password': config.get('password')
                    }
                    
                    if not credenciales['password']:
                        raise Exception("Se requiere password para el bot 24/7")
                    
                    guardar_credenciales_bot(credenciales)
                    guardar_config_bot(config)
                    
                    # Reiniciar estadísticas en la base de datos
                    nuevas_estadisticas = {
                        'operaciones_ejecutadas': 0,
                        'operaciones_exitosas': 0,
                        'ganancia_total': 0.0,
                        'ultima_operacion_timestamp': None,
                        'inicio_timestamp': time.time(),
                        'proxima_operacion_timestamp': None
                    }
                    actualizar_estadisticas_bot(nuevas_estadisticas)
                    
                    bot_servidor_thread = threading.Thread(target=ejecutar_bot_servidor)
                    bot_servidor_thread.daemon = True
                    bot_servidor_thread.start()
                    
                    print(f"🚀 BOT 24/7 INICIADO para {session['email']}")
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
                    
                    if not esta_activo_bot_servidor():
                        raise Exception("El bot servidor no está activo")
                    
                    detener_bot_servidor()
                    limpiar_credenciales_bot()  # Limpiar credenciales
                    
                    print(f"🛑 BOT 24/7 DETENIDO por {session['email']}")
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'success': True,
                        'message': 'Bot servidor detenido',
                        'estadisticas_finales': obtener_estadisticas_bot()
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

def run_server(port=PORT):
    global bot_servidor_thread

    # Inicializar la base de datos
    init_database()

    # Si el bot estaba activo, reiniciar el thread
    if esta_activo_bot_servidor():
        print("🤖 Reiniciando el bot servidor...")
        bot_servidor_thread = threading.Thread(target=ejecutar_bot_servidor)
        bot_servidor_thread.daemon = True
        bot_servidor_thread.start()

    # Iniciar limpieza de sesiones
    cleanup_thread = threading.Thread(target=cleanup_sessions_periodically)
    cleanup_thread.daemon = True
    cleanup_thread.start()
    
    server_address = ('', port)
    
    try:
        httpd = ThreadedHTTPServer(server_address, MyHttpRequestHandler)
        
        print("\n" + "="*70)
        print(f"🚀 SERVIDOR HTTP INICIADO")
        print("="*70)
        print(f"🌐 URL: http://localhost:{port}")
        print(f"📂 Directorio: {CWD}")
        print(f"🔐 Sistema de sesiones activado")
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
        if esta_activo_bot_servidor():
            print("🛑 Deteniendo bot servidor...")
            detener_bot_servidor()
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
