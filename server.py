import http.server
import socketserver
import json
import os
import sys
import traceback
import time
from urllib.parse import urlparse, parse_qs
from conexion import _connect
from operar import ejecutar_operacion

PORT = 8000
CWD = os.path.dirname(os.path.abspath(__file__))

# Variable global para mantener la sesión activa
active_session = {
    'iq': None,
    'email': None,
    'gestor_riesgo': None  # Nuevo: Gestor de riesgo persistente
}

def get_profile_data(iq):
    """Obtiene datos del perfil usando los métodos correctos de la API"""
    try:
        # Intentar obtener el perfil
        profile = iq.get_profile_ansyc()
        time.sleep(1)  # Esperar a que se complete la petición
        return profile
    except:
        return {}

class MyHttpRequestHandler(http.server.BaseHTTPRequestHandler):
    
    def log_message(self, format, *args):
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")
    
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
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

                # --- CONEXIÓN Y GUARDAR SESIÓN ---
                print("⏳ Conectando a IQ Option...")
                iq_session = _connect(email, password)
                print("✅ Conexión establecida.")
                
                # Guardar sesión activa
                active_session['iq'] = iq_session
                active_session['email'] = email
                # Inicializar gestor de riesgo (se mantiene entre operaciones)
                from operar import GestorRiesgoInteligente
                active_session['gestor_riesgo'] = GestorRiesgoInteligente()

                # Intentar obtener información de perfil
                username = email.split("@")[0]  # Valor por defecto
                user_id = None
                currency = "USD"
                
                try:
                    profile = get_profile_data(iq_session)
                    if profile:
                        username = profile.get("name") or profile.get("username") or username
                        user_id = profile.get("user_id") or profile.get("id")
                        currency = profile.get("currency") or profile.get("currency_char") or "USD"
                        print(f"👤 Usuario: {username}")
                except Exception as e:
                    print(f"⚠️  No se pudo obtener perfil completo: {e}")
                
                # --- SOLUCIÓN CORREGIDA PARA OBTENER BALANCES ---
                real_balance = 0.0
                practice_balance = 0.0
                real_id = None
                practice_id = None

                try:
                    print("💰 Obteniendo información de balances...")
                    
                    # Método 1: Intentar con get_balances() (método principal)
                    try:
                        balances_data = iq_session.get_balances()
                        print(f"📊 Respuesta completa de get_balances(): {balances_data}")
                        
                        # CORRECCIÓN: balances_data es un dict con clave 'msg'
                        if balances_data and isinstance(balances_data, dict):
                            balances_list = balances_data.get('msg', [])
                            print(f"📋 Lista de balances encontrada: {len(balances_list)} elementos")
                            
                            for bal in balances_list:
                                if isinstance(bal, dict):
                                    print(f"🔍 Procesando balance: {bal}")
                                    
                                    # Identificar tipo por campo 'type'
                                    bal_type = bal.get('type')
                                    amount = bal.get('amount', 0)
                                    bal_id = bal.get('id')
                                    bal_currency = bal.get('currency', 'USD')
                                    
                                    # Tipo 1 = REAL, Tipo 4 = PRACTICE
                                    if bal_type == 1:
                                        real_balance = float(amount)
                                        real_id = bal_id
                                        currency = bal_currency
                                        print(f"✅ Balance REAL: ${real_balance} ({bal_currency})")
                                    elif bal_type == 4:
                                        practice_balance = float(amount)
                                        practice_id = bal_id
                                        print(f"✅ Balance PRACTICE: ${practice_balance} ({bal_currency})")
                                    elif bal_type == 5:
                                        # Cuentas de cripto, podemos ignorar o mostrar
                                        print(f"🔗 Balance Crypto ({bal_currency}): ${amount}")
                        
                        else:
                            print("⚠️  No se encontraron datos de balances en formato esperado")
                            
                    except Exception as e:
                        print(f"⚠️  Error con get_balances(): {e}")

                    # Método 2: Si no se encontraron balances, intentar método alternativo
                    if real_balance == 0 and practice_balance == 0:
                        print("🔄 Intentando método alternativo...")
                        try:
                            # Cambiar a REAL y obtener balance
                            if iq_session.change_balance('REAL'):
                                time.sleep(1)
                                real_balance_raw = iq_session.get_balance()
                                if real_balance_raw:
                                    real_balance = float(real_balance_raw)
                                    print(f"💰 Balance REAL (alternativo): ${real_balance}")
                            
                            # Cambiar a PRACTICE y obtener balance
                            if iq_session.change_balance('PRACTICE'):
                                time.sleep(1)
                                practice_balance_raw = iq_session.get_balance()
                                if practice_balance_raw:
                                    practice_balance = float(practice_balance_raw)
                                    print(f"🎯 Balance PRACTICE (alternativo): ${practice_balance}")
                            
                            # Volver a REAL para operaciones por defecto
                            iq_session.change_balance('REAL')
                            time.sleep(0.5)
                            
                        except Exception as e2:
                            print(f"❌ Error en método alternativo: {e2}")

                    # Resumen final
                    print(f"\n📊 RESUMEN FINAL DE BALANCES:")
                    print(f"   REAL: ${real_balance} (ID: {real_id})")
                    print(f"   PRACTICE: ${practice_balance} (ID: {practice_id})")
                    print(f"   Moneda: {currency}")

                except Exception as e:
                    print(f"❌ Error general obteniendo balances: {e}")
                    # Establecer valores por defecto como fallback
                    real_balance = 0.0
                    practice_balance = 10000.0
                    print(f"💰 Usando valores por defecto:")
                    print(f"   REAL: ${real_balance}")
                    print(f"   PRACTICE: ${practice_balance}")

                # --- FIN SOLUCIÓN BALANCES ---
                
                response_data = {
                    "success": True,
                    "data": {
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
                }

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response_data).encode('utf-8'))
                
                print(f"✅ LOGIN EXITOSO para {email}\n")

            except Exception as e:
                error_msg = str(e)
                print(f"❌ ERROR: {error_msg}")
                traceback.print_exc()
                
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False, 
                    'error': error_msg
                }).encode('utf-8'))
        
        elif self.path == '/operar':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
                config = json.loads(post_data.decode('utf-8'))
                
                modo = config.get('modo', 'demo')  # 'demo' o 'real'
                monto = config.get('monto')  # Puede ser None para cálculo automático
                ejecutar_auto = config.get('ejecutar_auto', False)
                forzar_operacion = config.get('forzar_operacion', False)
                
                # Nueva configuración de riesgo inteligente
                config_riesgo = {
                    'riesgo_porcentaje': config.get('riesgo_porcentaje', 2.0),
                    'max_perdidas_consecutivas': config.get('max_perdidas_consecutivas', 3),
                    'stop_loss_diario': config.get('stop_loss_diario', 15),
                    'monto_maximo': config.get('monto_maximo', 10),
                    'modo_inteligente': config.get('modo_inteligente', 'auto')
                }
                
                print(f"\n{'='*70}")
                print(f"🎯 OPERACIÓN SOLICITADA")
                print(f"{'='*70}")
                print(f"Modo: {modo.upper()}")
                print(f"Monto: {'AUTO' if monto is None else f'${monto}'}")
                print(f"Auto: {'SÍ' if ejecutar_auto else 'NO'}")
                print(f"Forzar: {'SÍ' if forzar_operacion else 'NO'}")
                print(f"Riesgo: {config_riesgo['riesgo_porcentaje']}%")
                print(f"Máx pérdidas: {config_riesgo['max_perdidas_consecutivas']}")
                print(f"Stop diario: {config_riesgo['stop_loss_diario']}%")
                print(f"{'='*70}\n")
                
                # Verificar sesión activa
                if not active_session.get('iq'):
                    raise Exception("No hay sesión activa. Inicie sesión primero.")
                
                # Usar gestor de riesgo persistente si existe
                gestor_persistente = active_session.get('gestor_riesgo')
                if gestor_persistente:
                    # Actualizar configuración del gestor existente
                    gestor_persistente.config.update(config_riesgo)
                    print(f"🔄 Usando gestor de riesgo persistente", file=sys.stderr)
                
                # Ejecutar operación
                resultado = ejecutar_operacion(
                    active_session['iq'],
                    modo=modo,
                    monto=monto,
                    ejecutar_auto=ejecutar_auto,
                    forzar_operacion=forzar_operacion,
                    config_riesgo=config_riesgo
                )
                
                # Actualizar gestor de riesgo persistente con los resultados
                if gestor_persistente and resultado.get('estadisticas_riesgo'):
                    gestor_persistente.racha_perdidas = resultado['estadisticas_riesgo'].get('racha_perdidas', 0)
                    gestor_persistente.racha_ganancias = resultado['estadisticas_riesgo'].get('racha_ganancias', 0)
                    gestor_persistente.profit_diario = resultado['estadisticas_riesgo'].get('profit_diario', 0)
                    gestor_persistente.operaciones_hoy = resultado['estadisticas_riesgo'].get('operaciones_hoy', 0)
                
                # Enviar respuesta
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(resultado).encode('utf-8'))
                
                print(f"✅ Operación completada\n")
                
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
                # Resetear estadísticas de riesgo
                if active_session.get('gestor_riesgo'):
                    from operar import GestorRiesgoInteligente
                    active_session['gestor_riesgo'] = GestorRiesgoInteligente()
                    print("🔄 Estadísticas de riesgo reseteadas")
                
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
        print(f"🧪 Test: http://localhost:{port}/test")
        print("="*70)
        print("\n✅ Servidor listo para recibir conexiones")
        print("⌨️  Presiona Ctrl+C para detener\n")
        
        httpd.serve_forever()
        
    except OSError as e:
        if "address already in use" in str(e).lower():
            print(f"\n❌ ERROR: El puerto {port} ya está en uso")
            print(f"💡 Solución: Cambia PORT en server.py o cierra el proceso que usa el puerto")
            print(f"\nPara ver qué usa el puerto {port}:")
            print(f"  Windows: netstat -ano | findstr :{port}")
            print(f"  Linux/Mac: lsof -i :{port}\n")
        else:
            print(f"\n❌ ERROR: {e}\n")
            traceback.print_exc()
    except KeyboardInterrupt:
        print("\n\n🛑 Servidor detenido")
        # Cerrar sesión activa
        if active_session.get('iq'):
            try:
                active_session['iq'].api.close()
            except:
                pass
        httpd.server_close()
    except Exception as e:
        print(f"\n❌ ERROR INESPERADO: {e}\n")
        traceback.print_exc()


if __name__ == '__main__':
    run_server()