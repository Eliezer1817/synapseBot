import http.server
import socketserver
import json
import os
import sys
import traceback
import time
import hashlib
import uuid
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
from conexion import _connect
from operar import ejecutar_operacion, GestorRiesgoInteligente

PORT = 8000
CWD = os.path.dirname(os.path.abspath(__file__))

# Archivo para persistencia de datos
STORAGE_FILE = os.path.join(CWD, "session_storage.json")

# Sesiones activas en memoria
active_sessions = {}

# Storage persistente
def load_storage():
    """Carga datos persistentes del disco"""
    try:
        if os.path.exists(STORAGE_FILE):
            with open(STORAGE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ Error cargando storage: {e}", file=sys.stderr)
    return {}

def save_storage(data):
    """Guarda datos persistentes al disco"""
    try:
        with open(STORAGE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Error guardando storage: {e}", file=sys.stderr)

def generate_session_token(email, device_id):
    """Genera un token único de sesión"""
    data = f"{email}_{device_id}_{time.time()}"
    return hashlib.sha256(data.encode()).hexdigest()

def get_device_id(request):
    """Obtiene o genera un ID único de dispositivo"""
    # Buscar en cookies
    cookie_header = request.headers.get('Cookie', '')
    if 'device_id=' in cookie_header:
        for cookie in cookie_header.split(';'):
            if 'device_id=' in cookie:
                return cookie.split('=')[1].strip()
    
    # Generar nuevo ID basado en User-Agent e IP
    user_agent = request.headers.get('User-Agent', '')
    client_ip = request.client_address[0]
    device_data = f"{user_agent}_{client_ip}"
    return hashlib.md5(device_data.encode()).hexdigest()

def get_profile_data(iq):
    """Obtiene datos del perfil"""
    try:
        profile = iq.get_profile_ansyc()
        time.sleep(1)
        return profile
    except:
        return {}

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
        if self.path == '/check_session':
            # Verificar si hay sesión activa
            try:
                auth_header = self.headers.get('Authorization', '')
                token = auth_header.replace('Bearer ', '')
                
                if token and token in active_sessions:
                    session = active_sessions[token]
                    
                    # Verificar que la sesión no haya expirado
                    if session.get('expires_at', 0) > time.time():
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        
                        response = {
                            'success': True,
                            'session_valid': True,
                            'user_data': session.get('user_data')
                        }
                        self.wfile.write(json.dumps(response).encode('utf-8'))
                        return
                
                # Sesión inválida o expirada
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'session_valid': False
                }).encode('utf-8'))
                
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': str(e)
                }).encode('utf-8'))
            return
        
        # Resto del código GET original
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
                    # Enviar cookie de device_id
                    device_id = get_device_id(self)
                    self.send_header('Set-Cookie', f'device_id={device_id}; Path=/; Max-Age=31536000')
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
                post_data = self.rfile.read(content_length)
                credentials = json.loads(post_data.decode('utf-8'))
                
                email = credentials.get('email', '').strip()
                password = credentials.get('password', '').strip()
                device_id = get_device_id(self)
                
                if not email or not password:
                    raise Exception("Email y password son requeridos")
                
                print(f"\n{'='*70}")
                print(f"🔥 LOGIN REQUEST")
                print(f"{'='*70}")
                print(f"📧 Email: {email}")
                print(f"📱 Device ID: {device_id}")
                print(f"{'='*70}\n")

                # Verificar si ya hay una sesión activa para este email
                storage = load_storage()
                active_device = storage.get('active_devices', {}).get(email)
                
                if active_device and active_device != device_id:
                    # Hay otro dispositivo conectado
                    raise Exception("⚠️ Esta cuenta ya está activa en otro dispositivo. Cierra sesión allí primero.")
                
                # Conectar a IQ Option
                print("⏳ Conectando a IQ Option...")
                iq_session = _connect(email, password)
                print("✅ Conexión establecida.")
                
                # Generar token de sesión
                session_token = generate_session_token(email, device_id)
                
                # Obtener balances
                username = email.split("@")[0]
                user_id = None
                currency = "USD"
                real_balance = 0.0
                practice_balance = 0.0
                real_id = None
                practice_id = None

                try:
                    profile = get_profile_data(iq_session)
                    if profile:
                        username = profile.get("name") or profile.get("username") or username
                        user_id = profile.get("user_id") or profile.get("id")
                        currency = profile.get("currency") or "USD"
                except Exception as e:
                    print(f"⚠️ No se pudo obtener perfil: {e}")
                
                # Obtener balances
                try:
                    balances_data = iq_session.get_balances()
                    if balances_data and isinstance(balances_data, dict):
                        balances_list = balances_data.get('msg', [])
                        
                        for bal in balances_list:
                            if isinstance(bal, dict):
                                bal_type = bal.get('type')
                                amount = bal.get('amount', 0)
                                bal_id = bal.get('id')
                                
                                if bal_type == 1:
                                    real_balance = float(amount)
                                    real_id = bal_id
                                elif bal_type == 4:
                                    practice_balance = float(amount)
                                    practice_id = bal_id
                except Exception as e:
                    print(f"⚠️ Error obteniendo balances: {e}")
                    practice_balance = 10000.0

                user_data = {
                    "user": {
                        "username": username,
                        "email": email,
                        "userId": user_id
                    },
                    "real": {
                        "balance": real_balance,
                        "accountId": real_id,
                        "currency": currency,
                        "type": "REAL"
                    },
                    "practice": {
                        "balance": practice_balance,
                        "accountId": practice_id,
                        "currency": currency,
                        "type": "PRACTICE"
                    }
                }

                # Guardar sesión activa (expira en 24 horas)
                active_sessions[session_token] = {
                    'iq': iq_session,
                    'email': email,
                    'device_id': device_id,
                    'gestor_riesgo': GestorRiesgoInteligente(),
                    'user_data': user_data,
                    'expires_at': time.time() + (24 * 60 * 60),
                    'trades': [],
                    'stats': {'wins': 0, 'losses': 0, 'profit': 0}
                }

                # Guardar en storage persistente
                if 'active_devices' not in storage:
                    storage['active_devices'] = {}
                storage['active_devices'][email] = device_id
                
                # Restaurar trades si existen
                if email in storage.get('user_trades', {}):
                    active_sessions[session_token]['trades'] = storage['user_trades'][email]
                
                # Restaurar stats si existen
                if email in storage.get('user_stats', {}):
                    active_sessions[session_token]['stats'] = storage['user_stats'][email]
                
                save_storage(storage)

                response_data = {
                    "success": True,
                    "session_token": session_token,
                    "data": user_data
                }

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                # Enviar cookie con device_id
                self.send_header('Set-Cookie', f'device_id={device_id}; Path=/; Max-Age=31536000')
                self.end_headers()
                self.wfile.write(json.dumps(response_data).encode('utf-8'))
                
                print(f"✅ LOGIN EXITOSO para {email}\n")

            except Exception as e:
                error_msg = str(e)
                print(f"❌ ERROR: {error_msg}")
                traceback.print_exc()
                
                self.send_response(500 if "otro dispositivo" not in error_msg else 403)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False, 
                    'error': error_msg
                }).encode('utf-8'))
        
        elif self.path == '/logout':
            try:
                auth_header = self.headers.get('Authorization', '')
                token = auth_header.replace('Bearer ', '')
                
                if token in active_sessions:
                    session = active_sessions[token]
                    email = session.get('email')
                    
                    # Guardar datos antes de cerrar
                    storage = load_storage()
                    
                    if 'user_trades' not in storage:
                        storage['user_trades'] = {}
                    if 'user_stats' not in storage:
                        storage['user_stats'] = {}
                    
                    storage['user_trades'][email] = session.get('trades', [])
                    storage['user_stats'][email] = session.get('stats', {})
                    
                    # Liberar dispositivo
                    if email in storage.get('active_devices', {}):
                        del storage['active_devices'][email]
                    
                    save_storage(storage)
                    
                    # Cerrar sesión IQ
                    try:
                        session['iq'].api.close()
                    except:
                        pass
                    
                    # Eliminar de memoria
                    del active_sessions[token]
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'message': 'Sesión cerrada correctamente'
                }).encode('utf-8'))
                
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': str(e)
                }).encode('utf-8'))
        
        elif self.path == '/operar':
            try:
                auth_header = self.headers.get('Authorization', '')
                token = auth_header.replace('Bearer ', '')
                
                if token not in active_sessions:
                    raise Exception("Sesión no válida. Inicie sesión nuevamente.")
                
                session = active_sessions[token]
                
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length)
                config = json.loads(post_data.decode('utf-8'))
                
                # Ejecutar operación (código original)
                resultado = ejecutar_operacion(
                    session['iq'],
                    modo=config.get('modo', 'demo'),
                    monto=config.get('monto'),
                    ejecutar_auto=config.get('ejecutar_auto', False),
                    forzar_operacion=config.get('forzar_operacion', False),
                    config_riesgo={
                        'riesgo_porcentaje': config.get('riesgo_porcentaje', 2.0),
                        'max_perdidas_consecutivas': config.get('max_perdidas_consecutivas', 3),
                        'stop_loss_diario': config.get('stop_loss_diario', 15),
                        'monto_maximo': config.get('monto_maximo', 10)
                    }
                )
                
                # Guardar operación en sesión
                if resultado.get('ejecutado'):
                    session['trades'].append({
                        'timestamp': time.time(),
                        'data': resultado
                    })
                    
                    # Actualizar stats
                    if resultado.get('resultado_trade', {}).get('finalizada'):
                        if resultado['resultado_trade']['win']:
                            session['stats']['wins'] += 1
                            session['stats']['profit'] += resultado['resultado_trade']['ganancia']
                        else:
                            session['stats']['losses'] += 1
                            session['stats']['profit'] += resultado['resultado_trade']['ganancia']
                    
                    # Guardar en storage
                    storage = load_storage()
                    email = session['email']
                    if 'user_trades' not in storage:
                        storage['user_trades'] = {}
                    if 'user_stats' not in storage:
                        storage['user_stats'] = {}
                    
                    storage['user_trades'][email] = session['trades']
                    storage['user_stats'][email] = session['stats']
                    save_storage(storage)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(resultado).encode('utf-8'))
                
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
        
        elif self.path == '/reset_riesgo':
            try:
                auth_header = self.headers.get('Authorization', '')
                token = auth_header.replace('Bearer ', '')
                
                if token in active_sessions:
                    active_sessions[token]['gestor_riesgo'] = GestorRiesgoInteligente()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'message': 'Estadísticas reseteadas'
                }).encode('utf-8'))
                
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': str(e)
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
        print(f"💾 Storage: {STORAGE_FILE}")
        print("="*70)
        print("\n✅ Servidor listo")
        print("⌨️ Presiona Ctrl+C para detener\n")
        
        httpd.serve_forever()
        
    except KeyboardInterrupt:
        print("\n\n🛑 Servidor detenido")
        httpd.server_close()
    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")
        traceback.print_exc()


if __name__ == '__main__':
    run_server()
