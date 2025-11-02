import json
import os
import time
from datetime import datetime

DB_FILE = 'trading_data.json'

def init_database():
    """Inicializar la base de datos si no existe"""
    if not os.path.exists(DB_FILE):
        data = {
            'operaciones': [],
            'estadisticas': {},
            'bot_servidor': {
                'activo': False,
                'config': {},
                'estadisticas': {
                    'operaciones_ejecutadas': 0,
                    'operaciones_exitosas': 0,
                    'ganancia_total': 0.0,
                    'ultima_operacion_timestamp': None
                }
            }
        }
        save_database(data)

def load_database():
    """Cargar la base de datos"""
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        init_database()
        return load_database()

def save_database(data):
    """Guardar la base de datos"""
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def agregar_operacion(operacion):
    """Agregar una operación al historial"""
    data = load_database()
    
    # Agregar timestamp si no existe
    if 'timestamp' not in operacion:
        operacion['timestamp'] = time.time()
        operacion['fecha_hora'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    data['operaciones'].append(operacion)
    
    # Mantener solo las últimas 100 operaciones
    if len(data['operaciones']) > 100:
        data['operaciones'] = data['operaciones'][-100:]
    
    save_database(data)
    return operacion

def obtener_historial(limit=50):
    """Obtener historial de operaciones"""
    data = load_database()
    operaciones = data.get('operaciones', [])
    # Ordenar por timestamp descendente
    operaciones.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
    return operaciones[:limit]

def actualizar_estadisticas_bot(estadisticas):
    """Actualizar estadísticas del bot servidor"""
    data = load_database()
    data['bot_servidor']['estadisticas'] = estadisticas
    save_database(data)

def obtener_estadisticas_bot():
    """Obtener estadísticas del bot servidor"""
    data = load_database()
    return data['bot_servidor']['estadisticas']

def guardar_config_bot(config):
    """Guardar configuración del bot servidor"""
    data = load_database()
    data['bot_servidor']['config'] = config
    data['bot_servidor']['activo'] = True
    save_database(data)

def detener_bot_servidor():
    """Detener el bot servidor en la base de datos"""
    data = load_database()
    data['bot_servidor']['activo'] = False
    save_database(data)

def esta_activo_bot_servidor():
    """Verificar si el bot servidor está activo"""
    data = load_database()
    return data['bot_servidor']['activo']
