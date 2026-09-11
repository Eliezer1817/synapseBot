#!/usr/bin/env python3
"""Prueba de fuego local: claim → in_flight → crash → reconcile → no recompra."""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

td = tempfile.mkdtemp(prefix='synapse_idem_')
os.chdir(td)

import database

database.init_database()
activo = 'EURUSD-OTC'
candle = int(time.time() // 300) * 300

claimed, key = database.claim_trade(activo, candle, 'call', {'test': True})
assert claimed, 'first claim must succeed'
database.finalize_idempotency(key, 'in_flight', phase='pre_buy')
# simula muerte del proceso sin placed/failed
marked = database.reconcile_orphan_idempotency(min_age_sec=0)
assert key in marked, marked
entry = database.get_idempotency(key)
assert entry['status'] == 'uncertain_crash', entry
claimed2, key2 = database.claim_trade(activo, candle, 'call')
assert (not claimed2) and key2 == key, 'at-most-once must block second claim'
alerta = database.load_database()['bot_servidor'].get('estado_vivo', {}).get('idempotencia_alerta')
assert alerta and alerta.get('count') >= 1
print('OK fire-test: in_flight crash → uncertain_crash → no retry')
print('key=', key)
print('alerta=', alerta)
