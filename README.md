# Bot de arbitraje triangular para Binance Spot

Busca ciclos de tres pares (por ejemplo USDT → BTC → ETH → USDT) en los que, después de
comisiones, terminas con más de lo que empezaste. Puede limitarse a vigilar, simular las órdenes
con tu latencia real u operar en testnet o con dinero real.

Nace del análisis de seis proyectos públicos (están en `..\arbitrage-refs\`) y se queda con lo
mejor de cada uno.

## Antes de nada: expectativas

**El arbitraje triangular en Binance casi nunca es rentable para un particular.** Los datos:

- **harjus**, el más sofisticado de los seis (C++, kernel bypass, servidor en la misma zona de AWS
  que Binance, 0,5 ms de latencia), ganó **0,13 $** en 69 horas. Detectó 156 oportunidades y
  ejecutó una. En su última versión, 84 oportunidades y ninguna ejecutada. Su autor abandonó el
  proyecto.
- Los README de Hephyrius y Cherecho reconocen que pierden dinero por las comisiones.
  t0mm4rx define su propio código como "dogshit" y pide que no se use con dinero.
- Las oportunidades duran milisegundos y se las llevan empresas como Jump o Jane Street, con
  comisiones VIP de ~0,017 % frente a tu 0,1 %.
- **En tu conexión** (medido el 11-10-2026): ping de 260 ms y, peor aún, los datos en
  tiempo real llegan con **segundos de retraso** que crecen sin parar (ver "Diagnóstico de tu
  conexión"). Con precios de hace 20 s no se puede arbitrar.

Por eso el modo por defecto es **papel**: simula con honestidad lo que pasaría y te enseña
cuánto duran las oportunidades frente a tu tiempo de reacción. Úsalo para aprender y medir
antes de arriesgar un euro.

## Qué se tomó de cada repositorio

| Repositorio | Lo que aporta aquí |
|---|---|
| **bmino/binance-triangle-arbitrage** (Node) | Recorrer la profundidad del libro, probar varios tamaños, estrategias lineal y paralela, tope de ejecuciones, recalcular cada tramo con lo recibido, validación exhaustiva de la configuración, pantalla en vivo, índice par → rutas afectadas |
| **ValtteriL/harjus** (C++) | Rutas descubiertas con un grafo (no escritas a mano), órdenes **LIMIT + FOK** en lugar de a mercado, cantidad limitada a lo que hay en el libro, `stepSize`/`minNotional`, beneficio normalizado a una moneda (USD), reserva de la ruta mientras se ejecuta, testnet |
| **t0mm4rx/binance_crypto_arbitrage** | Deshacer a la base si falla un tramo, avisos por Telegram, registro del balance |
| **Cherecho/Triangular-arbitrage** | (derivado de t0mm4rx) misma lógica de deshacer; la caché de libros por ciclo ya la sustituye el websocket |
| **Roibal/Cryptocurrency-Trading-Bots** | Simulación con cartera virtual y recogida de datos para análisis posterior |
| **Hephyrius/Binance-Triangular-Arbitrage-Bot** | Sobre todo qué no hacer: consultar precios por REST en bucle, decimales fijos, triángulos escritos a mano |

Y lo que no tenía ninguno:

- **Simulación con latencia real.** Cada orden se compara con el libro que verás tras
  RTT/2 + retraso de datos. Si para entonces la liquidez ya no está, la FOK caduca. harjus
  marcaba 60 000 $ en testnet y 0,13 $ en real; esta simulación intenta no mentir así.
- **Medición en vivo del retraso de los datos** y freno automático (`retraso_max_ms`).
- **Oportunidades registradas como episodios**, con su duración, y el comando `analizar`, que
  te dice qué porcentaje es imposible de capturar con tu latencia.

## Instalación (Windows)

```powershell
cd ruta\a\arbitraje-bot
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy config.example.toml config.toml
copy .env.example .env      # solo hace falta para testnet o real
```

## Uso

```powershell
# Simulación (por defecto). Ctrl+C para parar y ver el resumen.
.venv\Scripts\python -m triarb

# Solo vigilar y registrar, sin simular órdenes
.venv\Scripts\python -m triarb --modo escanear

# Durante un tiempo fijo y sin la tabla en vivo
.venv\Scripts\python -m triarb --duracion 600 --sin-pantalla

# Estadísticas de todo lo registrado en logs\
.venv\Scripts\python -m triarb analizar

# Testnet: órdenes reales en https://testnet.binance.vision (dinero ficticio)
.venv\Scripts\python -m triarb --modo testnet

# Real (dinero de verdad). Exige la bandera y espera 5 s antes de empezar.
.venv\Scripts\python -m triarb --modo real --confirmo-dinero-real
```

| Modo | Claves | Qué hace |
|---|---|---|
| `escanear` | no | Detecta y registra oportunidades |
| `papel` | no | Además simula las órdenes con tu latencia y una cartera ficticia |
| `testnet` | de testnet | Órdenes reales en la testnet de Binance (libros distintos a los reales) |
| `real` | sí | Dinero real. Empieza con `max_ejecuciones = 1` y cantidades mínimas |

## Cómo funciona

1. **Arranque.** Mide el ping y el desfase de reloj, descarga los pares y sus filtros, construye
   todas las rutas de 3 tramos que empiezan y acaban en tus bases y carga una foto inicial de los
   libros.
2. **Datos.** Un websocket con `@bookTicker` (mejor precio al instante) o `@depth` (varios niveles)
   por cada par vigilado, más un `@trade` para medir el retraso real.
3. **Cálculo.** Con cada actualización solo se recalculan las rutas de ese par. Primero un filtro
   barato (producto de precios × comisiones). Si pasa, se recorre el libro con redondeos y mínimos
   reales para varios tamaños y se elige el de más beneficio absoluto.
4. **Ejecución** (papel, testnet o real). Órdenes LIMIT FOK al precio calculado. En `lineal`, cada
   tramo usa lo recibido en el anterior; si uno falla, se vuelve a la base a mercado. Un solo ciclo
   a la vez, con enfriamiento por ruta.
5. **Seguridad.** Tope de ejecuciones, máximo por minuto, pérdida diaria máxima, tope por
   operación en USD y freno por retraso de datos. Si una orden queda en estado desconocido (sin
   respuesta) o Binance limita la IP (429/418), deja de operar.

### La pantalla

- **Neto al mejor precio**: beneficio del ciclo con los mejores precios y comisiones incluidas. Se
  muestran las mejores rutas aunque sean negativas, para ver lo cerca que está el mercado.
- **Beneficio / Tamaño**: solo cuando la ruta pasa el umbral; es el cálculo completo con
  profundidad, redondeos y mínimos.
- **Datos con X ms de retraso**: lo que tardan en llegarte los eventos de Binance. Si pasa de
  `retraso_max_ms`, no se ejecuta nada.

### Archivos de `logs\`

- `oportunidades.csv`: una fila por episodio (inicio, ruta, duración, pico de beneficio, tamaño).
- `ejecuciones.csv`: cada intento, con su resultado, tramo fallido, deltas por activo y PnL.
- `triarb.log`: registro técnico.

## Configuración

Todo está comentado en `config.example.toml`. Lo más importante:

- `mercado.activos`: limita los pares. Menos activos, menos datos (ver el diagnóstico de abajo).
- `[bases.X]`: activo de inicio y tamaño mínimo y máximo por operación. **Si vives en la UE**,
  Binance no permite operar USDT: usa USDC como base y quita USDT de `activos`.
- `estrategia.comision_pct`: 0,1 normal o 0,075 pagando comisiones con BNB.
  `comisiones_por_par` es para los pares con promoción.
- `riesgo.*`: los límites. No los quites en modo real.

## Claves de API (solo testnet o real)

En Binance → Gestión de API:

- Activa **solo** "Enable Spot & Margin Trading".
- **Nunca** "Enable Withdrawals".
- Restringe la clave a la IP de la máquina donde corre el bot.
- Las claves van en `.env`, que está en `.gitignore`. No las pegues en ningún otro sitio.

## Diagnóstico de tu conexión (11-10-2026)

| Medida | Resultado |
|---|---|
| Ping REST a api.binance.com | ~260 ms |
| Desfase del reloj de Windows | **+276 s** (el bot lo compensa; sincroniza la hora igualmente) |
| Descarga REST desde Binance | 4,8 MB/s (bien) |
| Retraso de datos con 1 stream | ~147 ms base, con picos de varios segundos |
| Retraso de datos con 120 pares | **crece sin parar: 0,5 s → 25 s en 30 s** |

El websocket de Binance no te pasa de ~15–20 KB/s y los mensajes se acumulan en cola. Pasa igual
con dos librerías distintas (aiohttp y websockets) y con dos servidores (`stream.binance.com` y
`data-stream.binance.vision`), así que no es el código. Causas típicas:

1. **Antivirus con análisis de HTTPS** (Kaspersky, Avast, AVG, ESET, Bitdefender…): intercepta las
   conexiones cifradas y retiene el tráfico continuo. Prueba a desactivar el "escudo web" un
   momento y vuelve a ejecutar el bot.
2. Una VPN o un proxy.
3. Tu proveedor de internet frenando las conexiones largas, o una mala ruta hasta Tokio.

Mientras tanto puedes reducir `mercado.activos` a 5–8 activos muy líquidos para mandar menos datos.
La solución de verdad, la de harjus, es ejecutar el bot en un servidor cerca de Binance (AWS
`ap-northeast-1`, Tokio), donde el ping baja a 1–2 ms.

## Carry con oro tokenizado (la estrategia recomendada)

Tras el análisis (ver abajo), lo único que da positivo de forma constante en una sola plataforma
es el **carry**: comprar el activo en spot y vender su perpetuo del mismo tamaño. El precio da
igual porque una pata compensa la otra, y se cobra el funding cuando es positivo (en oro
tokenizado, PAXG y XAUT, ~90 % de los pagos).

```powershell
# Probar las reglas con el funding real de los últimos 90 días
.venv\Scripts\python -m triarb carry --plataforma binance --backtest 90
# Modo papel 24/7 (datos públicos, ejecución simulada, sin claves). Ctrl+C para parar; retoma donde lo dejó.
.venv\Scripts\python -m triarb carry --plataforma binance --capital 500
```

Qué hace el bot:
- Elige entre PAXG, XAUT, BTC y ETH (en Bybit: XAUT, BTC y ETH) por su funding medio de los
  últimos 14 días.
- Entra si supera el 4 % anual y casi nunca cambia de activo: solo si otro paga un 10 % anual más,
  porque cada cambio cuesta 4 comisiones.
- Sale si el funding se vuelve negativo.
- Cobra cada pago con la tasa oficial publicada.
- Avisa si el corto se acercara a la liquidación (a 2x hace falta una subida de ~+49 %).
- Guarda el estado en `logs/carry_estado_<plataforma>.json` y los eventos en `logs/carry.csv`.

**Qué esperar:** el backtest de 90 días da **+2–3,3 % anual sobre el capital**, es decir, ~10–16 $
al año con 500 $. Es un rendimiento, no un negocio. Para desplegarlo: `deploy/triarb-carry.service`
(VPS Linux) o el `Dockerfile` (Railway, con un volumen en `/app/logs`). Antes, pasa
`node deploy/check-endpoints.mjs` en ese servidor.

## Análisis de rentabilidad e investigación

El análisis completo con datos reales está en [docs/ANALISIS-RENTABILIDAD.md](docs/ANALISIS-RENTABILIDAD.md).
Las herramientas de `research/`:

| Comando | Qué hace |
|---|---|
| `python -m research.check_server` | ¿Sirve este servidor? (bloqueo de Binance, ping, retraso de datos) |
| `python -m research.collect_books --minutos 20` | Graba fotos completas del mercado por REST |
| `python -m research.triangle_study` | Ventaja bruta, capacidad, duración y comisiones del arbitraje triangular |
| `python -m research.maker_backtest` | Backtest de la estrategia maker con operaciones reales |
| `python -m research.funding_study` | Un año de funding y simulación de cash and carry |
| `python -m research.funding_alert` | Avisa (Telegram) cuando el funding se calienta |
| `python -m research.collect_multi --minutos 25` | Fotos simultáneas de 9 exchanges (Binance, OKX, Bybit, KuCoin, Gate, MEXC, Bitget, HTX, Kraken) |
| `python -m research.multi_study` | Triangular en cada exchange y diferencias de precio entre ellos |
| `python -m research.funding_cross --dias 90` | Funding entre Binance, Bybit, OKX e Hyperliquid |

Para un VPS Linux: `bash deploy/setup-vps.sh` instala todo, pasa el comprobador y explica cómo
dejar el bot como servicio 24/7 (`deploy/triarb.service`).

## Pruebas

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

Cubren el cálculo (recorrido del libro, redondeos, mínimos, capacidad), el descubrimiento de
rutas, el simulador (FOK que se llena, caduca o se rechaza por saldo), el ejecutor (éxito,
deshacer en lineal, mantener, revertir en paralela, enfriamiento), los límites de riesgo, los
episodios, la configuración y el parser del websocket.

## Estructura

```
triarb/
  app.py        orquestación, bucle de escaneo, resumen
  binance.py    cliente REST firmado y filtros de los pares
  market.py     websockets, libros locales, medición del retraso
  paths.py      grafo de pares → rutas
  engine.py     cálculo de rentabilidad y tamaño
  venues.py     ejecución simulada (papel) y real (testnet/real)
  executor.py   lineal / paralela, FOK, deshacer
  risk.py       límites de seguridad
  journal.py    CSV de oportunidades y ejecuciones
  pricing.py    valor en USD de cualquier activo
  hud.py        pantalla en vivo
  analyze.py    comando analizar
  notify.py     Telegram
```
