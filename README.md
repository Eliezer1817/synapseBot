# SynapseBot

Bot de trading algorítmico para **IQ Option** (binarias). Estrategia por **reglas** (no ML en producción): **EMA 9/21 + MACD + Bollinger**, con confirmación de vela, modo manual o **Núcleo 24/7**, dashboard y despliegue en Fly.io.

> Proyecto en prueba · Uso educativo / investigación · Alto riesgo de pérdida

**Demo en vivo:** https://synapsebot.fly.dev/

---

## Qué hace (hoy)

- Analiza **EURUSD-OTC** en velas de 5 minutos
- Decide **CALL / PUT / SKIP** según score de componentes (EMA, MACD, BB, vela)
- Opera en **DEMO** o **REAL** (recomendado: DEMO hasta validar edge)
- Dashboard: Resumen, Operar, Núcleo 24/7, Riesgo, Cuentas, Historial
- **Investigación:** registra señales (incl. SKIP) y resultado hipotético a 5 min para medir edge por score **sin** cambiar las reglas
- **Idempotencia at-most-once** en 24/7 (no duplicar trades ante restart/crash)
- Credenciales IQ cifradas en reposo (Fernet + secret de Fly)
- Persistencia en volumen Fly (`/data`)
- Paywall opcional: licencia por **USDT TRC20** con verificación on-chain automática

---

## Qué *no* es

- **No** usa LightGBM en el flujo real de trading (el README viejo lo decía; era una idea). Puede haber un `lgbm_model.txt` legado en el repo: **no** gobierna las operaciones actuales.
- **No** hay API oficial de IQ Option: se usa un wrapper no oficial (`iqoptionapi`). Riesgo principal: ToS / ban / inestabilidad, además del riesgo de mercado.
- **No** es consejo financiero ni promesa de rentabilidad.

---

## Stack

| Pieza | Uso |
|--------|-----|
| Python 3 | Motor (`operar.py`), API (`server.py`), persistencia (`database.py`) |
| `ta` / pandas / numpy | Indicadores y datos |
| Flask (HTTP server embebido) | API + servir `index.html` |
| cryptography | Cifrado de credenciales |
| iqoptionapi | Conexión no oficial a IQ Option |
| Docker + Fly.io | Deploy + volumen persistente |

---

## Estructura (relevante)

```text
synapseBot/
├── operar.py          # Señales EMA/MACD/BB, ejecución, investigación hip.
├── conexion.py        # Sesión IQ Option
├── database.py        # trading_data.json, idempotencia, licencias, research
├── server.py          # HTTP API, sesión, Núcleo 24/7, paywall
├── payment_tron.py    # Verificación USDT TRC20 (TronGrid)
├── crypto_util.py     # Fernet + sanitizado de errores
├── stats_util.py      # Stats de operaciones cerradas
├── index.html         # Landing + dashboard
├── Dockerfile
├── fly.toml           # App + mount /data
└── requirements.txt
```

---

## Variables de entorno (Fly secrets)

| Variable | Rol |
|----------|-----|
| `SYNAPSE_CREDENTIALS_KEY` | Clave Fernet para passwords IQ |
| `SYNAPSE_DATA_DIR` | Persistencia (en Fly: `/data`) |
| `SYNAPSE_USDT_ADDRESS` | Address TRC20 para paywall (opcional) |
| `SYNAPSE_LICENSE_ALLOWLIST` | Emails sin paywall (coma-separados) |
| `SYNAPSE_PAYMENT_USDT` | Monto base (default `100`) |
| `SYNAPSE_LICENSE_DAYS` | `0` = licencia sin vencimiento |
| `TRONGRID_API_KEY` | Opcional, rate limit TronGrid |
| `SYNAPSE_DEBUG` | `1` habilita endpoints de debug |

Nunca subas passwords, keys ni addresses de pago al repo. Usá secrets de Fly.

---

## Deploy (Fly)

```bash
fly deploy -a synapsebot
fly secrets set SYNAPSE_CREDENTIALS_KEY="..."
# paywall (opcional)
fly secrets set SYNAPSE_USDT_ADDRESS="T..." SYNAPSE_LICENSE_ALLOWLIST="tu@email.com"
```

Requiere volumen montado en `/data` (ver `fly.toml`).

---

## Seguridad del repo / cuenta

Checklist recomendado para este proyecto:

1. **2FA** en GitHub (obligatorio en la práctica).
2. Repo **sin** `.env`, passwords ni keys en el historial.
3. Añadir **`.gitignore`** (venv, `__pycache__`, `.env`, datos locales).
4. Preferir **secrets en Fly**, no en GitHub Actions ni en el código.
5. Revisar tokens PAT: mínimo scope, vencimiento corto; rotar si se filtró alguno.
6. Si el bot es comercial / con paywall: valorar repo **privado** (el código público facilita forks y abuso).
7. No compartir ACCESS_KEY de IQ ni seeds/wallets en issues, commits ni chats públicos.

---

## Aviso de riesgo

Trading de opciones binarias puede llevar a **pérdida total** del capital. SynapseBot es un proyecto experimental. Validá cualquier edge en DEMO con la tabla de investigación por score antes de considerar dinero real. IQ Option puede restringir o banear automatización no oficial.

---

## Licencia / autor

Uso bajo tu propia responsabilidad. Repo: [Eliezer1817/synapseBot](https://github.com/Eliezer1817/synapseBot).
