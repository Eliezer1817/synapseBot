# ⚡ SynapseBot

**Bot de trading algorítmico autónomo** para IQ Option, impulsado por **LightGBM** + indicadores técnicos + gestión de riesgo inteligente.

> Precisión. Velocidad. Control total.

---

## 🧠 ¿Qué es SynapseBot?

SynapseBot es un sistema de trading automatizado que:

- Analiza el mercado en tiempo real (EURUSD-OTC)
- Usa un modelo **LightGBM** entrenado con features técnicos
- Decide **CALL / PUT / SKIP** según umbrales de probabilidad
- Gestiona el riesgo de forma inteligente (rachas, stop-loss diario, tamaño de posición)
- Expone una API + dashboard futurista

---

## ✨ Características principales

| Función | Descripción |
|---------|-------------|
| **Modelo LightGBM** | Predicción de dirección con features de alta calidad |
| **Indicadores técnicos** | RSI, EMA 20/50, MACD, Bollinger Bands, ATR, retornos, Harami |
| **Gestión de riesgo** | % del balance, ajuste por racha, stop-loss diario, límites por tamaño de cuenta |
| **Modo Demo / Real** | Cambia fácilmente entre PRACTICE y REAL |
| **Ejecución automática** | Puede operar solo o esperar confirmación |
| **API Flask** | Endpoints listos para el frontend |
| **Dashboard** | Landing futurista con video 3D y terminal de acceso |

---

## 🛠️ Stack tecnológico

- **Python 3**
- LightGBM
- `ta` (Technical Analysis)
- pandas + numpy
- Flask
- [iqoptionapi](https://github.com/Lu-Yi-Hsun/iqoptionapi)
- Docker / Fly.io / Railway

---

## 📁 Estructura del proyecto

```text
synapseBot/
├── operar.py          # Motor de trading + modelo + riesgo
├── conexion.py        # Conexión a IQ Option
├── database.py        # Persistencia
├── server.py          # API Flask
├── index.html         # Dashboard / Landing
├── lgbm_model.txt     # Modelo entrenado
├── requirements.txt
├── Dockerfile
├── fly.toml
└── railway.json

🚀 Instalación rápida
🤖BASH
# 1. Clonar
git clone https://github.com/Eliezer1817/synapseBot.git
cd synapseBot

# 2. Entorno virtual
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Dependencias
pip install -r requirements.txt

# 4. Variables de entorno (recomendado)
export IQ_EMAIL="tu@email.com"
export IQ_PASSWORD="tu_password"

--------------------------------------------------------------------

▶️ Uso básico
🐍PYTHON
from operar import ejecutar_operacion
from conexion import conectar   # ajusta según tu archivo

iq = conectar()

resultado = ejecutar_operacion(
    iq=iq,
    modo="demo",              # "demo" o "real"
    monto=None,               # None = cálculo automático
    ejecutar_auto=True,       # True = opera solo
    forzar_operacion=False
)

print(resultado)

--------------------------------------------------------------------

⚙️ Configuración de riesgo
El bot incluye un GestorRiesgoInteligente con:
•  Riesgo base por operación (por defecto 2%)
•  Ajuste automático según calidad de señal
•  Reducción tras rachas de pérdidas
•  Stop-loss diario
•  Límites según tamaño de cuenta

--------------------------------------------------------------------

📊 Features del modelo
rsi_14 · ema_20 · ema_50 · macd · macd_signal · macd_hist
bb_high · bb_low · bb_width · atr_14
ret_1 · ret_3 · ret_6 · vol_10 · harami

--------------------------------------------------------------------

⚠️ Aviso importante
Este software es solo para fines educativos y de investigación.
El trading de opciones binarias implica un alto riesgo de pérdida.
Usa siempre la cuenta DEMO primero.
El autor no se hace responsable de pérdidas financieras.

--------------------------------------------------------------------

## Demo en video

Mira la demo de SynapseBot aquí:  
[Ver video en Google Drive](https://drive.google.com/file/d/1WY7wDEHy-1ZkKCqh2QhoCtn9iILM3vRI/view?usp=drivesdk)


📄 Licencia
Proyecto privado / uso personal.
Contacto: Eliezer1817

