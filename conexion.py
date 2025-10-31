import requests
import json
import time
import random
import logging

def _connect(email, password):
    """Conexión simple a IQ Option - Versión Demo"""
    
    class IQOptionSimple:
        def __init__(self, email, password):
            self.email = email
            self.password = password
            self.logged_in = False
            self.balance_type = 'PRACTICE'
            self.demo_balance = 10000.0
            print(f"🔐 Conectando como: {self.email}")
            
        def login(self):
            """Simular login exitoso"""
            time.sleep(1)
            self.logged_in = True
            print("✅ Login exitoso")
            return True
        
        def change_balance(self, balance_type):
            """Cambiar tipo de balance"""
            self.balance_type = balance_type
            print(f"💰 Cambiando a cuenta: {balance_type}")
            return True
        
        def get_balance(self):
            """Obtener balance"""
            balance = self.demo_balance if self.balance_type == 'PRACTICE' else 0.0
            print(f"📊 Balance {self.balance_type}: ${balance}")
            return balance
            
        def get_balances(self):
            """Obtener balances"""
            return {
                'msg': [
                    {
                        'id': 1,
                        'type': 1,
                        'amount': 0,
                        'currency': 'USD',
                        'type_string': 'REAL'
                    },
                    {
                        'id': 2, 
                        'type': 4,
                        'amount': self.demo_balance,
                        'currency': 'USD',
                        'type_string': 'PRACTICE'
                    }
                ]
            }
        
        def buy(self, amount, asset, direction, expiration):
            """Ejecutar operación DEMO"""
            print(f"💰 EJECUTANDO {direction.upper()} por ${amount} en {asset}")
            
            # Simular resultado (70% win rate para demo)
            trade_id = f"demo_trade_{int(time.time())}_{random.randint(1000,9999)}"
            
            # Actualizar balance demo
            if random.random() > 0.3:  # 70% win rate
                win_amount = amount * 0.8  # 80% payout
                self.demo_balance += win_amount
                print(f"✅ WIN: +${win_amount:.2f}")
            else:
                self.demo_balance -= amount
                print(f"❌ LOSS: -${amount:.2f}")
                
            print(f"📊 Nuevo balance: ${self.demo_balance:.2f}")
            return True, trade_id
            
        def check_win_v3(self, trade_id):
            """Verificar resultado - Versión simple"""
            # En esta versión demo, el resultado ya se aplicó en buy()
            # Retornar un valor positivo para indicar éxito
            return 0.8  # 80% payout
            
        def get_profile_ansyc(self):
            """Obtener perfil demo"""
            return {
                "name": self.email.split("@")[0],
                "username": self.email.split("@")[0],
                "user_id": random.randint(100000, 999999),
                "currency": "USD",
                "currency_char": "$"
            }
            
        def get_candles(self, asset, interval, count, timestamp):
            """Obtener velas demo"""
            # Generar velas demo aleatorias
            candles = []
            base_price = 1.1000
            
            for i in range(count):
                open_price = base_price + random.uniform(-0.0020, 0.0020)
                close_price = open_price + random.uniform(-0.0010, 0.0010)
                high_price = max(open_price, close_price) + random.uniform(0, 0.0005)
                low_price = min(open_price, close_price) - random.uniform(0, 0.0005)
                
                candles.append({
                    'open': open_price,
                    'close': close_price,
                    'min': low_price,
                    'max': high_price,
                    'volume': random.randint(1000, 5000),
                    'from': timestamp - (count - i) * interval,
                    'to': timestamp - (count - i - 1) * interval
                })
            
            return candles

    # Crear y retornar instancia
    iq = IQOptionSimple(email, password)
    if iq.login():
        return iq
    else:
        raise Exception("Error de conexión a IQ Option")

# Para testing
if __name__ == "__main__":
    iq = _connect("demo@demo.com", "password")
    print("✅ Conexión demo exitosa")
    print(f"💰 Balance: ${iq.get_balance()}")
