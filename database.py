import json
import os
import time
import threading
from datetime import datetime

import crypto_util

_DB_LOCK = threading.RLock()

# Persistencia: en Fly montamos un volume en /data (SYNAPSE_DATA_DIR).
DATA_DIR = os.environ.get('SYNAPSE_DATA_DIR') or (
    '/data' if os.path.isdir('/data') else os.path.dirname(os.path.abspath(__file__)) or '.'
)
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, 'trading_data.json')

def _maybe_migrate_legacy_db():
    """Si el JSON legacy quedó en /app, copiarlo una vez al volume."""
    legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trading_data.json')
    if legacy == DB_FILE:
        return
    if os.path.exists(DB_FILE) or not os.path.exists(legacy):
        return
    try:
        import shutil
        shutil.copy2(legacy, DB_FILE)
        print(f"📦 Migrado trading_data.json → {DB_FILE}")
    except Exception as e:
        print(f"⚠️ No se pudo migrar trading_data.json: {e}")

_maybe_migrate_legacy_db()

def init_database():
    """Inicializar la base de datos si no existe"""
    if not os.path.exists(DB_FILE):
        data = {
            'operaciones': [],
            'estadisticas': {},
            'bot_servidor': {
                'activo': False,
                'config': {},
                'credenciales': None,
                'ultima_operacion': None,
                'estadisticas': {
                    'operaciones_ejecutadas': 0,
                    'operaciones_exitosas': 0,
                    'ganancia_total': 0.0,
                    'ultima_operacion_timestamp': None
                },
                'estado_vivo': {
                    'fase': 'detenido',
                    'mensaje': 'El núcleo está detenido.',
                    'heartbeat': None
                },
                # keys: v1:{activo}:{candle_open}:{CALL|PUT|CYCLE}
                'idempotencia': {}
            }
        }
        save_database(data)

def load_database():
    """Cargar la base de datos"""
    with _DB_LOCK:
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            init_database()
            return load_database()

def save_database(data):
    """Guardar la base de datos (bajo lock)."""
    with _DB_LOCK:
        tmp = DB_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, DB_FILE)

def _normalize_operacion(operacion):
    """Aplana wrappers {resultado: ...} y asegura timestamp."""
    op = dict(operacion or {})
    if isinstance(op.get('resultado'), dict) and op['resultado'].get('decision') and not op.get('decision'):
        inner = dict(op['resultado'])
        inner.setdefault('timestamp', op.get('timestamp') or time.time())
        op = inner
    if 'timestamp' not in op:
        op['timestamp'] = time.time()
    if 'fecha_hora' not in op:
        op['fecha_hora'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return op

def agregar_operacion(operacion):
    """Agregar una operación al historial"""
    data = load_database()
    operacion = _normalize_operacion(operacion)
    data['operaciones'].append(operacion)
    
    # Mantener solo las últimas 100 operaciones
    if len(data['operaciones']) > 300:
        data['operaciones'] = data['operaciones'][-300:]
    
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

def obtener_credenciales_bot():
    """Obtener credenciales del bot (password descifrada en memoria)."""
    data = load_database()
    creds = data['bot_servidor'].get('credenciales')
    if not creds:
        return None
    out = dict(creds)
    stored_pw = creds.get('password') or ''
    encrypted = str(stored_pw).startswith(crypto_util.PREFIX)
    if out.get('password'):
        out['password'] = crypto_util.decrypt_secret(out['password'])
        out['password_encrypted'] = encrypted
        # Migración: si quedó en texto plano y hay clave, re-cifrar en disco
        if (not encrypted) and crypto_util.credentials_key_configured() and out['password']:
            try:
                guardar_credenciales_bot({'email': out.get('email'), 'password': out['password']})
                out['password_encrypted'] = True
            except Exception:
                pass
    return out

def guardar_credenciales_bot(credenciales):
    """Guardar credenciales cifrando la password en reposo."""
    data = load_database()
    to_store = dict(credenciales or {})
    if to_store.get('password'):
        to_store['password'] = crypto_util.encrypt_secret(to_store['password'])
    data['bot_servidor']['credenciales'] = to_store
    save_database(data)

def obtener_ultima_operacion_bot():
    """Obtener la última operación del bot servidor"""
    data = load_database()
    return data['bot_servidor'].get('ultima_operacion')

def guardar_ultima_operacion_bot(operacion):
    """Guardar la última operación del bot servidor"""
    data = load_database()
    data['bot_servidor']['ultima_operacion'] = operacion
    save_database(data)

def limpiar_credenciales_bot():
    """Limpiar credenciales del bot servidor"""
    data = load_database()
    data['bot_servidor']['credenciales'] = None
    save_database(data)

def actualizar_estado_vivo(fase, mensaje='', **extra):
    """Fase en vivo del daemon 24/7 para el dashboard."""
    data = load_database()
    estado = data['bot_servidor'].setdefault('estado_vivo', {})
    estado['fase'] = fase
    estado['mensaje'] = mensaje
    estado['heartbeat'] = time.time()
    for k, v in extra.items():
        estado[k] = v
    data['bot_servidor']['estado_vivo'] = estado
    save_database(data)
    return estado

def obtener_estado_vivo():
    data = load_database()
    return data['bot_servidor'].get('estado_vivo') or {
        'fase': 'detenido',
        'mensaje': 'El núcleo está detenido.',
        'heartbeat': None,
    }

def detener_bot_servidor():
    """Detener el bot servidor en la base de datos"""
    data = load_database()
    data['bot_servidor']['activo'] = False
    estado = data['bot_servidor'].setdefault('estado_vivo', {})
    if estado.get('fase') not in ('error', 'riesgo'):
        estado['fase'] = 'detenido'
        estado['mensaje'] = 'El núcleo está detenido.'
    estado['heartbeat'] = time.time()
    data['bot_servidor']['estado_vivo'] = estado
    save_database(data)

def esta_activo_bot_servidor():
    """Verificar si el bot servidor está activo"""
    data = load_database()
    return data['bot_servidor']['activo']


# ---------------------------------------------------------------------------
# Idempotencia 24/7 — política AT-MOST-ONCE (nunca recompra la misma key)
# Formato: v1:{activo}:{candle_open_unix}:{CALL|PUT|CYCLE}
# Estados trade: claimed → in_flight → placed | failed | uncertain_crash
# - claimed: key tomada, aún no se llamó a IQ buy
# - in_flight: buy enviado / a punto de enviarse (crash aquí = no reintentar)
# - placed / failed: respuesta conocida de IQ
# - uncertain_crash: crash o respuesta perdida tras in_flight → NO recomprar
# CYCLE: vela ya analizada; tampoco se reabre.
# ---------------------------------------------------------------------------

IDEM_POLICY = "at-most-once"
IDEM_ORPHAN_STATUSES = frozenset({"claimed", "in_flight"})

def _ensure_idempotencia(data):
    bot = data.setdefault('bot_servidor', {})
    if 'idempotencia' not in bot or not isinstance(bot['idempotencia'], dict):
        bot['idempotencia'] = {}
    return bot['idempotencia']


def claim_idempotency(key, meta=None):
    """Intenta reclamar una key. True = primera vez; False = ya existía."""
    with _DB_LOCK:
        data = load_database()
        store = _ensure_idempotencia(data)
        if key in store:
            return False
        entry = {
            'status': 'claimed',
            'ts': time.time(),
        }
        if meta:
            entry.update(meta)
        store[key] = entry
        # podar: mantener últimas ~200 keys
        if len(store) > 200:
            ordered = sorted(store.items(), key=lambda kv: kv[1].get('ts', 0))
            for old_k, _ in ordered[:-200]:
                del store[old_k]
        save_database(data)
        return True


def finalize_idempotency(key, status, **extra):
    with _DB_LOCK:
        data = load_database()
        store = _ensure_idempotencia(data)
        entry = store.get(key) or {'ts': time.time()}
        entry['status'] = status
        entry['updated_at'] = time.time()
        entry.update(extra)
        store[key] = entry
        save_database(data)
        return entry


def get_idempotency(key):
    data = load_database()
    return _ensure_idempotencia(data).get(key)


def candle_already_processed(activo, candle_open):
    key = f"v1:{activo}:{int(candle_open)}:CYCLE"
    return get_idempotency(key) is not None


def claim_candle_cycle(activo, candle_open, meta=None):
    key = f"v1:{activo}:{int(candle_open)}:CYCLE"
    return claim_idempotency(key, meta), key


def claim_trade(activo, candle_open, direction, meta=None):
    direction = (direction or '').upper()
    key = f"v1:{activo}:{int(candle_open)}:{direction}"
    return claim_idempotency(key, meta), key


def list_idempotency(limit=50):
    store = _ensure_idempotencia(load_database())
    items = sorted(store.items(), key=lambda kv: kv[1].get('updated_at') or kv[1].get('ts') or 0, reverse=True)
    return [{'key': k, **(v or {})} for k, v in items[:limit]]


def _refresh_idempotencia_alerta(data):
    store = _ensure_idempotencia(data)
    uncertain = [
        k for k, v in store.items()
        if isinstance(v, dict) and v.get('status') == 'uncertain_crash'
    ]
    estado = data['bot_servidor'].setdefault('estado_vivo', {})
    if uncertain:
        n = len(uncertain)
        titulo = f"⚠️ {n} operación incierta" if n == 1 else f"⚠️ {n} operaciones inciertas"
        estado['idempotencia_alerta'] = {
            'policy': IDEM_POLICY,
            'uncertain_keys': uncertain[-10:],
            'count': n,
            'ts': time.time(),
            'titulo': titulo,
            'mensaje': 'El sistema bloqueó automáticamente una posible duplicación.',
            'accion': 'Requiere reconciliación.',
        }
    else:
        estado.pop('idempotencia_alerta', None)
    data['bot_servidor']['estado_vivo'] = estado


def list_uncertain_idempotency():
    return [
        item for item in list_idempotency(100)
        if item.get('status') == 'uncertain_crash'
    ]


def resolve_idempotency(key, resolution, note=''):
    """Cierra uncertain_crash sin permitir recompra.

    resolution: resolved_no_trade | resolved_placed
    """
    allowed = {'resolved_no_trade', 'resolved_placed'}
    if resolution not in allowed:
        raise ValueError('resolution inválida')
    with _DB_LOCK:
        data = load_database()
        store = _ensure_idempotencia(data)
        entry = store.get(key)
        if not entry:
            raise KeyError('key no encontrada')
        if entry.get('status') != 'uncertain_crash':
            raise ValueError(f"solo uncertain_crash se puede resolver (ahora: {entry.get('status')})")
        entry['status'] = resolution
        entry['resolved_at'] = time.time()
        entry['updated_at'] = time.time()
        entry['resolve_note'] = (note or '')[:200]
        entry['policy'] = IDEM_POLICY
        store[key] = entry
        _refresh_idempotencia_alerta(data)
        save_database(data)
        return entry


def arm_chaos(point, armed_by=''):
    """One-shot chaos en DEMO. point: after_cycle_claim|after_trade_claim|after_in_flight|after_buy"""
    allowed = {
        'after_cycle_claim',
        'after_trade_claim',
        'after_in_flight',
        'after_buy',
    }
    if point not in allowed:
        raise ValueError('chaos point inválido')
    with _DB_LOCK:
        data = load_database()
        data['bot_servidor']['chaos_arm'] = {
            'point': point,
            'armed_at': time.time(),
            'armed_by': armed_by,
        }
        save_database(data)
        return data['bot_servidor']['chaos_arm']


def clear_chaos_arm():
    with _DB_LOCK:
        data = load_database()
        data['bot_servidor']['chaos_arm'] = None
        save_database(data)


def consume_chaos_arm(point):
    """Si hay arm one-shot en este point, lo consume y retorna True."""
    with _DB_LOCK:
        data = load_database()
        arm = data['bot_servidor'].get('chaos_arm') or {}
        if not arm or arm.get('point') != point:
            return False
        data['bot_servidor']['chaos_arm'] = None
        save_database(data)
        return True


def get_chaos_arm():
    return (load_database().get('bot_servidor') or {}).get('chaos_arm')


def reconcile_orphan_idempotency(min_age_sec=5):
    """Tras restart: claimed/in_flight huérfanos → uncertain_crash (at-most-once).

    Nunca borra la key ni permite recompra. Devuelve lista de keys marcadas.
    """
    with _DB_LOCK:
        data = load_database()
        store = _ensure_idempotencia(data)
        now = time.time()
        marked = []
        for key, entry in list(store.items()):
            if not isinstance(entry, dict):
                continue
            # CYCLE keys claimed sin done: también cerrar como uncertain si quedaron a medias
            status = entry.get('status')
            if status not in IDEM_ORPHAN_STATUSES:
                continue
            ts = float(entry.get('updated_at') or entry.get('ts') or 0)
            if now - ts < min_age_sec:
                continue
            entry['status'] = 'uncertain_crash'
            entry['updated_at'] = now
            entry['policy'] = IDEM_POLICY
            entry['reconcile_reason'] = (
                'Orphan after restart/crash: at-most-once — no retry. '
                'BUY may or may not have reached IQ Option.'
            )
            store[key] = entry
            marked.append(key)
        if marked:
            data['bot_servidor']['idempotencia'] = store
            # Señal visible en el pulso del dashboard
            estado = data['bot_servidor'].setdefault('estado_vivo', {})
            _refresh_idempotencia_alerta(data)
            save_database(data)
        return marked


def observabilidad_stats():
    """Contadores para dashboard de observabilidad."""
    store = _ensure_idempotencia(load_database())
    counts = {
        'uncertain_crash': 0,
        'placed': 0,
        'failed': 0,
        'resolved_no_trade': 0,
        'resolved_placed': 0,
        'in_flight': 0,
        'claimed': 0,
        'blocked_duplicates': 0,  # keys que no son el primer claim exitoso implícito
    }
    for _k, v in store.items():
        if not isinstance(v, dict):
            continue
        st = v.get('status') or ''
        if st in counts:
            counts[st] += 1
        if st in ('uncertain_crash', 'resolved_no_trade', 'resolved_placed', 'placed', 'failed'):
            # toda key de trade (no CYCLE) cuenta como intento protegido
            if not str(_k).endswith(':CYCLE'):
                counts['blocked_duplicates'] += 0  # placeholder
    # blocked = uncertain + resolved_* (casos donde se evitó recompra o se cerró a mano)
    trade_keys = [k for k, v in store.items() if isinstance(v, dict) and not str(k).endswith(':CYCLE')]
    counts['trade_keys'] = len(trade_keys)
    counts['uncertain'] = counts['uncertain_crash']
    counts['requiere_reconciliacion'] = counts['uncertain_crash']
    return counts


def calcular_estadisticas_reales(limit=500):
    import stats_util
    ops = obtener_historial(limit=limit)
    # obtener_historial ya ordena desc; stats_util reordena
    data = load_database()
    all_ops = data.get('operaciones') or ops
    return stats_util.compute_stats(all_ops)


def unresolved_uncertain_count():
    return len(list_uncertain_idempotency())


def get_idempotency_entry(key):
    return get_idempotency(key)
