#!/usr/bin/env python3
import os, sys, tempfile, time
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
td = tempfile.mkdtemp(prefix='synapse_idem_')
os.chdir(td)
import database
database.init_database()
activo='EURUSD-OTC'
candle=int(time.time()//300)*300
claimed,key=database.claim_trade(activo,candle,'call',{'test':True})
assert claimed
database.finalize_idempotency(key,'in_flight',phase='pre_buy')
marked=database.reconcile_orphan_idempotency(min_age_sec=0)
assert key in marked
assert database.get_idempotency(key)['status']=='uncertain_crash'
assert not database.claim_trade(activo,candle,'call')[0]
entry=database.resolve_idempotency(key,'resolved_no_trade',note='chaos test')
assert entry['status']=='resolved_no_trade'
assert database.obtener_estado_vivo().get('idempotencia_alerta') in (None, {})
# chaos arm consume
database.arm_chaos('after_in_flight', armed_by='test')
assert database.consume_chaos_arm('after_in_flight') is True
assert database.consume_chaos_arm('after_in_flight') is False
print('OK fire-test + resolve + chaos arm')
