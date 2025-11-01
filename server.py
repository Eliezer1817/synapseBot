import http.server
import socketserver
import json
import os
import sys
import traceback
import time
import uuid
from urllib.parse import urlparse, parse_qs
from conexion import _connect
from operar import ejecutar_operacion

PORT = 8000
CWD = os.path.dirname(os.path.abspath(__file__))

# Sistema de sesiones mejorado
active_sessions = {}
session_tokens = {}

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
        
        elif self.path == '/check_session':
            session = get_authenticated_session(self)
            if session:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'session_valid': True,
                    'user_data': {
                        'user': {'username': session['email'].split('@')[0]},
                        'real': {'balance': 1000.0, 'accountId': 'real_123'},
                        'practice': {'balance': 10000.0, 'accountId': 'demo_123'}
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
                
                # Crear nueva sesión
                token = SessionManager.create_session(email, iq_session)
                print(f"🔑 Token de sesión generado: {token[:8]}...")

                # Obtener datos de perfil (simplificado para prueba)
                username = email.split("@")[0]
                
                # Respuesta exitosa
                response_data = {
                    "success": True,
                    "session_token": token,
                    "data": {
                        "user": {
                            "username": username,
                            "email": email,
                            "userId": "user_123"
                        },
                        "real": {
                            "balance": 1000.0,
                            "accountId": "real_123",
                            "currency": "USD",
                            "type": "REAL"
                        },
                        "practice": {
                            "balance": 10000.0,
                            "accountId": "demo_123",
                            "currency": "USD",
                            "type": "PRACTICE"
                        }
                    }
                }

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response_data).encode('utf-8'))
                
                print(f"✅ LOGIN EXITOSO para {email}\n")

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
                
                # SIMULAR OPERACIÓN (para pruebas)
                # En producción, usarías: ejecutar_operacion()
                time.sleep(1)  # Simular procesamiento
                
                # Resultado simulado
                resultado_simulado = {
                    'success': True,
                    'ejecutado': True,
                    'decision': 'CALL',
                    'probabilidad': 75.5,
                    'trade_id': f'trade_{int(time.time())}',
                    'monto_calculado': monto or 2.0,
                    'resultado_trade': {
                        'finalizada': True,
                        'win': True,
                        'ganancia': 1.80
                    },
                    'estadisticas_riesgo': {
                        'racha_actual': 1,
                        'profit_diario': 5.40,
                        'operaciones_hoy': 3
                    }
                }
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(resultado_simulado).encode('utf-8'))
                
                print(f"✅ Operación simulada completada\n")
                
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
        # Limpiar todas las sesiones
        active_sessions.clear()
        session_tokens.clear()
        httpd.server_close()
    except Exception as e:
        print(f"\n❌ ERROR INESPERADO: {e}\n")
        traceback.print_exc()


if __name__ == '__main__':
    run_server()
