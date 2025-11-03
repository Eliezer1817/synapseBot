import http.server
import socketserver
import json
import os
import sys
import traceback
import time
import uuid
import threading  # 🔥 CORRECCIÓN: Import faltante
from urllib.parse import urlparse, parse_qs
from conexion import _connect
from operar import ejecutar_operacion
from datetime import datetime

PORT = 8000
CWD = os.path.dirname(os.path.abspath(__file__))

# Sistema de sesiones mejorado
active_sessions = {}
session_tokens = {}

# 🔥 NUEVO: Sistema de bots en servidor
bot_servidor_activo = False
bot_servidor_config = {}
bot_servidor_thread = None
bot_servidor_ultima_operacion = None
bot_servidor_estadisticas = {
    'operaciones_ejecutadas': 0,
    'operaciones_exitosas': 0,
    'ganancia_total': 0.0,
    'ultima_operacion_timestamp': None
}

class SessionManager:
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
            active_sessions[token]['last_activity'] = time.time()
            return active_sessions[token]
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

def get_profile_data(iq):
    """Obtiene datos del perfil usando los métodos correctos de la API"""
    try:
        profile = iq.get_profile_ansyc()
        time.sleep(1)
        return profile
    except:
        return {}

def get_authenticated_session(handler):
    """Obtener sesión autenticada desde headers"""
    auth_header = handler.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    
    token = auth_header.replace('Bearer ', '').strip()
    return SessionManager.get_session(token)

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
            print(f"📊 Respuesta de get_balances(): {balances_data}")
            
            if balances_data and isinstance(balances_data, dict):
                balances_list = balances_data.get('msg', [])
                print(f"📋 Balances encontrados: {len(balances_list)}")
                
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
                        elif bal_type == 5:
                            print(f"🔗 Balance Crypto: ${amount}")
            
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

        print(f"📊 RESUMEN FINAL:")
        print(f"   REAL: ${real_balance}")
        print(f"   DEMO: ${demo_balance}")
        
    except Exception as e:
        print(f"❌ Error general obteniendo balances: {e}")
        real_balance = 0.0
        demo_balance = 10000.0
    
    return real_balance, demo_balance, real_id, demo_id

# 🔥 NUEVA FUNCIÓN: Bot servidor 24/7
def ejecutar_bot_servidor():
    """Ejecuta el bot automático en el servidor de forma continua"""
    # 🔥 CORRECCIÓN: Declaración global al inicio
    global bot_servidor_activo, bot_servidor_ultima_operacion, bot_servidor_estadisticas
    
    print(f"\n🎯 INICIANDO BOT SERVIDOR 24/7")
    print(f"⏰ Intervalo: {bot_servidor_config.get('intervalo', 5)} minutos")
    print(f"🎮 Modo: {bot_servidor_config.get('modo', 'demo').upper()}")
    print(f"📊 Configuración riesgo: {bot_servidor_config.get('riesgo_porcentaje', 2)}%")
    
    while bot_servidor_activo:
        try:
            # Obtener sesión activa (usar la primera sesión disponible)
            session_activa = None
            for token, session_data in active_sessions.items():
                session_activa = session_data
                break
            
            if not session_activa:
                print("❌ No hay sesiones activas para el bot servidor")
                time.sleep(60)  # Esperar 1 minuto y reintentar
                continue
            
            print(f"\n{'='*50}")
            print(f"🤖 BOT SERVIDOR - CICLO {bot_servidor_estadisticas['operaciones_ejecutadas'] + 1}")
            print(f"{'='*50}")
            print(f"👤 Usuario: {session_activa['email']}")
            print(f"⏰ Hora: {time.strftime('%H:%M:%S')}")
            print(f"{'='*50}")
            
            # Ejecutar operación usando la MISMA lógica de operar.py
            resultado = ejecutar_operacion(
                session_activa['iq'],
                modo=bot_servidor_config.get('modo', 'demo'),
                monto=bot_servidor_config.get('monto'),
                ejecutar_auto=True,
                forzar_operacion=False,
                config_riesgo={
                    'riesgo_porcentaje': bot_servidor_config.get('riesgo_porcentaje', 2.0),
                    'max_perdidas_consecutivas': bot_servidor_config.get('max_perdidas_consecutivas', 3),
                    'stop_loss_diario': bot_servidor_config.get('stop_loss_diario', 15),
                    'monto_maximo': bot_servidor_config.get('monto_maximo', 10)
                }
            )
            
            # Actualizar estadísticas
            bot_servidor_estadisticas['operaciones_ejecutadas'] += 1
            bot_servidor_ultima_operacion = {
                'timestamp': time.time(),
                'resultado': resultado,
                'numero_operacion': bot_servidor_estadisticas['operaciones_ejecutadas']
            }
            
            if resultado.get('ejecutado'):
                bot_servidor_estadisticas['operaciones_exitosas'] += 1
                if resultado.get('resultado_trade') and resultado['resultado_trade'].get('finalizada'):
                    ganancia = resultado['resultado_trade'].get('ganancia', 0)
                    bot_servidor_estadisticas['ganancia_total'] += ganancia
                    print(f"💰 Resultado: {'✅ GANANCIA' if ganancia > 0 else '❌ PÉRDIDA'} - ${abs(ganancia):.2f}")
            
            bot_servidor_estadisticas['ultima_operacion_timestamp'] = time.time()
            
            print(f"📊 Estadísticas servidor:")
            print(f"   Operaciones: {bot_servidor_estadisticas['operaciones_ejecutadas']}")
            print(f"   Exitosas: {bot_servidor_estadisticas['operaciones_exitosas']}")
            print(f"   Ganancia total: ${bot_servidor_estadisticas['ganancia_total']:.2f}")
            print(f"⏳ Próxima operación en {bot_servidor_config.get('intervalo', 5)} minutos...")
            print(f"{'='*50}\n")
            
            # Esperar el intervalo configurado
            intervalo_segundos = bot_servidor_config.get('intervalo', 5) * 60
            for i in range(intervalo_segundos):
                if not bot_servidor_activo:
                    break
                time.sleep(1)
                
        except Exception as e:
            print(f"❌ ERROR en bot servidor: {e}")
            traceback.print_exc()
            # Esperar 1 minuto antes de reintentar en caso de error
            for i in range(60):
                if not bot_servidor_activo:
                    break
                time.sleep(1)
    
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
        
        # 🔥 NUEVO: Endpoint para verificar estado del bot servidor
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
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': True,
                'bot_activo': bot_servidor_activo,
                'config': bot_servidor_config,
                'estadisticas': bot_servidor_estadisticas,
                'ultima_operacion': bot_servidor_ultima_operacion,
                'ultima_operacion_timestamp': bot_servidor_estadisticas['ultima_operacion_timestamp'],
                'intervalo': bot_servidor_config.get('intervalo', 5)
            }).encode('utf-8'))
            return

        elif self.path == '/check_session':
            session = get_authenticated_session(self)
            if session:
                # Obtener balances actualizados
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
                    'session_valid': False
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
                    raise Exception("No hay sesión activa. Inicie sesión primero.")
                
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
                config = json.loads(post_data.decode('utf-8'))
                
                modo = config.get('modo', 'demo')
                monto = config.get('monto')
                ejecutar_auto = config.get('ejecutar_auto', False)
                forzar_operacion = config.get('forzar_operacion', False)
                
                print(f"\n{'='*70}")
                print(f"🎯 OPERACIÓN SOLICITADA")
                print(f"{'='*70}")
                print(f"Usuario: {session['email']}")
                print(f"Modo: {modo.upper()}")
                print(f"Monto: {'AUTO' if monto is None else f'${monto}'}")
                print(f"Auto: {'SÍ' if ejecutar_auto else 'NO'}")
                print(f"Forzar: {'SÍ' if forzar_operacion else 'NO'}")
                print(f"{'='*70}\n")
                
                # EJECUTAR OPERACIÓN REAL con el módulo operar
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
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(resultado).encode('utf-8'))
                
                print(f"✅ Operación REAL completada\n")
                
            except Exception as e:
                error_msg = str(e)
                print(f"❌ ERROR en operación: {error_msg}")
                traceback.print_exc()
                
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': error_msg
                }).encode('utf-8'))
        
        elif self.path == '/iniciar_bot':
            try:
                session = get_authenticated_session(self)
                if not session:
                    raise Exception("No hay sesión activa")
                
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
                config = json.loads(post_data.decode('utf-8'))
                
                print(f"🤖 Bot iniciado para {session['email']}")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'message': 'Bot automático iniciado',
                    'interval_minutes': config.get('interval_minutes', 5)
                }).encode('utf-8'))
                
            except Exception as e:
                error_msg = str(e)
                print(f"❌ ERROR iniciando bot: {error_msg}")
                
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': error_msg
                }).encode('utf-8'))
        
        elif self.path == '/detener_bot':
            try:
                session = get_authenticated_session(self)
                if not session:
                    raise Exception("No hay sesión activa")
                
                print(f"🛑 Bot detenido para {session['email']}")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'message': 'Bot automático detenido'
                }).encode('utf-8'))
                
            except Exception as e:
                error_msg = str(e)
                print(f"❌ ERROR deteniendo bot: {error_msg}")
                
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': error_msg
                }).encode('utf-8'))
        
        # 🔥 NUEVO: Endpoints para bot servidor 24/7
        
        elif self.path == '/iniciar_bot_servidor':
            try:
                session = get_authenticated_session(self)
                if not session:
                    raise Exception("No hay sesión activa")
                
                # 🔥 CORRECCIÓN: Declaración global al inicio
                global bot_servidor_activo, bot_servidor_config, bot_servidor_thread, bot_servidor_estadisticas
                
                if bot_servidor_activo:
                    raise Exception("El bot servidor ya está activo")
                
                content_length = int(self.headers.get('Content-Length', 0))
                # 🔥 CORRECCIÓN: b'{}}' corregido a b'{}'
                post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
                config = json.loads(post_data.decode('utf-8'))
                
                # Guardar configuración
                bot_servidor_config = config
                bot_servidor_activo = True
                
                # Reiniciar estadísticas
                bot_servidor_estadisticas = {
                    'operaciones_ejecutadas': 0,
                    'operaciones_exitosas': 0,
                    'ganancia_total': 0.0,
                    'ultima_operacion_timestamp': None
                }
                
                # Iniciar thread del bot
                bot_servidor_thread = threading.Thread(target=ejecutar_bot_servidor)
                bot_servidor_thread.daemon = True
                bot_servidor_thread.start()
                
                print(f"🚀 BOT SERVIDOR INICIADO para {session['email']}")
                print(f"📋 Configuración: {config}")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'message': 'Bot 24/7 iniciado en servidor',
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
                # 🔥 CORRECCIÓN: Declaración global al inicio
                global bot_servidor_activo, bot_servidor_estadisticas
                
                session = get_authenticated_session(self)
                if not session:
                    raise Exception("No hay sesión activa")
                
                if not bot_servidor_activo:
                    raise Exception("El bot servidor no está activo")
                
                bot_servidor_activo = False
                
                print(f"🛑 BOT SERVIDOR DETENIDO por {session['email']}")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'message': 'Bot servidor detenido',
                    'estadisticas_finales': bot_servidor_estadisticas
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


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def run_server(port=PORT):
    server_address = ('', port)
    
    try:
        httpd = ThreadedHTTPServer(server_address, MyHttpRequestHandler)
        
        print("\n" + "="*70)
        print(f"🚀 SERVIDOR HTTP INICIADO")
        print("="*70)
        print(f"🌐 URL: http://localhost:{port}")
        print(f"📂 Directorio: {CWD}")
        print(f"🔐 Sistema de sesiones activado")
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
        global bot_servidor_activo
        if bot_servidor_activo:
            print("🛑 Deteniendo bot servidor...")
            bot_servidor_activo = False
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
