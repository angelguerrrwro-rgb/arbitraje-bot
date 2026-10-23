# Análisis de rentabilidad — 11-10-2026

Pregunta: ¿qué haría falta para que el bot gane dinero, en Binance o en cualquier otra
plataforma? Respuesta corta:

- **El arbitraje triangular no es rentable para un cliente normal en ninguno de los 9 exchanges
  medidos**, y ningún ajuste de software lo cambia.
- Lo más sólido es el **funding entre plataformas**: corto en Hyperliquid y largo en Binance, OKX o
  Bybit. Dio +4–7 % anual en los últimos 90 días, sin depender de la velocidad, pero el rendimiento
  es modesto y escala con el capital.
- Hay dos pistas más, por validar: la **estrategia maker en pares cruzados** y el **arbitraje de
  traslado** de monedas pequeñas entre exchanges medianos.

Abajo están los datos (§1–6) y el plan (§7).

Todo es reproducible con los scripts de `research/` (ver el final).

---

## 1. Arbitraje triangular taker (lo que hace el bot y lo que vende Netrunbot)

**Datos:** 1.171 fotos completas del mercado, una por segundo durante 20 minutos (domingo
11-10-2026 por la noche, hora de Europa). Cada foto incluye los 1.375 pares a la vez, sacados por
REST para que sean coherentes entre sí y no les afecte el problema del websocket de tu PC. En total
son 1.728 triángulos, 3.456 rutas contando los dos sentidos.

**Ventaja bruta** (antes de comisiones) de la **mejor ruta de todo el mercado** en cada foto:
mediana **0,109 %**, p90 0,262 %, p99 0,281 %, máximo 0,439 %.

| Comisión por tramo | Fotos con alguna ruta rentable | Episodios con ≥10 $ de capacidad | Beneficio máx. teórico* |
|---|---|---|---|
| 0,100 % (tu cuenta hoy) | 0,2 % | 3 | 1,45 $/h |
| 0,075 % (con BNB) | 21,6 % | 4 | 3,12 $/h |
| 0,075 % + promos U a 0 % | 21,6 % | 4 | 3,12 $/h (las promos no aportan nada) |
| ~0,042 % (VIP3) | 30,9 % | 15 | 7,0 $/h |
| ~0,023 % (VIP9, los profesionales) | 88,4 % | 69 | 14,7 $/h |

\* *Capturando el 100 % de cada oportunidad en su mejor momento y con toda la liquidez al mejor
precio. Es un techo imposible, no una previsión.*

Las 4 oportunidades con BNB:

| Ruta | Neto | Capacidad | Beneficio | Duración |
|---|---|---|---|---|
| MUBARAK → USDC → TRY → MUBARAK | 0,213 % | 188 $ | 0,40 $ | ≤ 1 s |
| MUBARAK → USDT → TRY → MUBARAK | 0,123 % | 267 $ | 0,33 $ | ≤ 1 s |
| TRY → W → USDC → TRY | 0,153 % | 121 $ | 0,18 $ | ≤ 1 s |
| API3 → USDT → TRY → API3 | 0,047 % | 259 $ | 0,12 $ | ≤ 1 s |

Lo que dicen estos datos:

- **Desde tu cuenta, tal como está, no hay negocio:** 3 oportunidades en 20 minutos, de un segundo
  o menos cada una.
- **Todas pasan por la lira turca (TRY)** y monedas pequeñas. Comprueba que tu cuenta puede operar
  pares TRY, porque depende del país.
- **Te las disputan empresas que pagan ~0,023 %.** Para ellas, cualquier ruta que a ti te deja un
  0,225 % bruto es muy rentable, y están a 0,5 ms de Binance. harjus, con ese mismo despliegue,
  capturó 1 de 156.
- **Estimación realista con un VPS en Tokio:** capturar entre el 1 y el 5 % del techo, es decir,
  **entre 0,75 y 3,75 $ al día antes de tramos fallidos**. Es más o menos lo que cuesta el servidor.
  Una noche de domingo es poca muestra; la fase 1 del plan lo mide con una semana entera.
- **Ningún cambio de software lo arregla.** Lo que decide es el nivel de comisión y llegar primero.

**Veredicto:** el bot actual (y lo que vende Netrunbot) no es rentable para un cliente normal. Se
conserva como escáner, simulador y base para la estrategia de §5.

## 2. Comisiones: la palanca que más pesa

Cada ciclo paga tres comisiones. Ese es el umbral que la ventaja bruta tiene que superar:

| Comisión por tramo | Umbral del ciclo | ¿Alcanzable? |
|---|---|---|
| 0,100 % (VIP0) | 0,300 % | Es la tuya hoy |
| 0,075 % (VIP0 pagando con BNB) | 0,225 % | Sí: basta activar "pagar comisiones con BNB" y tener algo de BNB |
| ~0,042 % (VIP3) | ~0,126 % | No: exige decenas de millones de USD de volumen al mes |
| ~0,023 % (VIP9) | ~0,069 % | No: es donde operan las empresas de alta frecuencia con las que compites |

Promociones de comisión cero vigentes (a 11-10-2026, según anuncios de Binance; verifica en tu
cuenta, la API lo dice con `GET /api/v3/account/commission`):

- **Maker 0 %** en BTC, ETH, BNB, SOL, XRP, DOGE y LINK contra FDUSD. El taker volvió a la
  comisión normal el 29-01-2026. Solo sirve para órdenes pasivas (estrategia maker, §5).
- **U/USDT y U/USDC** (la stablecoin "United Stables") sin comisión desde el 13-01-2026,
  "hasta nuevo aviso". En la muestra no generaron ninguna oportunidad.
- Maker 0 % en algunos pares nuevos (bStocks hasta el 31-10-2026, ADA/U, DOGE/U, PEPE/U).

Conclusión: activar BNB es obligatorio (baja el umbral del 0,30 % al 0,225 %) y conviene que el bot
lea las comisiones reales de cada par desde la API para aprovechar las promociones. Pero ni así
llegas al nivel de quienes se llevan las oportunidades.

## 3. Latencia: tu PC frente a un servidor

| Medida | Tu PC (11-10-2026) | VPS en Tokio (referencia: harjus) |
|---|---|---|
| Ping REST a Binance | 260–530 ms | 1–2 ms (0,5 ms en la misma zona de AWS) |
| Retraso de los datos en tiempo real | **crece sin parar: de 0,5 s a 25 s** | estable, unos pocos ms |
| Reloj | +276 s de desfase | sincronizado con chrony |

En tu PC, el websocket de Binance no pasa de ~15–40 KB/s y los mensajes se acumulan en cola. Pasa
con dos librerías y dos servidores distintos, así que es la red: antivirus que inspecciona HTTPS,
VPN o proveedor. **Desde casa no se puede arbitrar nada**, ni con este bot ni con ninguno.

**Servidor 24/7.** Tu otro proyecto está en Railway, pero en abril de 2026 Binance bloqueaba
Railway en **todas** sus regiones (Singapur y Países Bajos incluidas) con el error *"Service
unavailable from a restricted location"*. Según el personal de Railway, sus IPs están en rangos que
Binance rechaza y ellos no lo pueden cambiar. Además, su región más cercana (Singapur) está a
~70 ms de Tokio.

Recomendación: un **VPS pequeño en Tokio** (AWS `ap-northeast-1`, Vultr o Linode: 5–12 $/mes),
que es donde despliega harjus. Antes de pagar nada, ejecuta en el servidor candidato
`python -m research.check_server`: en un minuto dice si Binance bloquea la IP, el ping y si el
retraso de los datos se acumula. Puedes probarlo también en Railway; no cuesta nada. Recuerda que
lo que decide si puedes usar Binance es tu país de residencia, no dónde está el servidor.

## 4. Funding rate (spot + futuros, "cash and carry")

Comprar en spot y abrir un corto del mismo tamaño en el perpetuo: la posición es neutral al precio
y cobras el funding cuando es positivo. No depende de la velocidad, así que se podría hacer incluso
desde casa. Probado con **un año completo** de historial oficial de los 40 perpetuos más
negociados, con comisiones de entrada y salida (spot 0,075 %, futuros 0,05 %) y capital de 1,5×
el nocional (corto con apalancamiento 2×):

| Estrategia (último año) | Rentabilidad anual sobre capital |
|---|---|
| BTC siempre dentro | **+2,0 %** |
| ETH siempre dentro | +1,5 % |
| Mejor par de los 40 siempre dentro (MUBARAK) | +4,6 % |
| Entrar solo cuando el funding medio supera un umbral (barrido de 20 combinaciones) | entre −0,03 % y +0,07 % de media: casi nunca entra |
| Rotar cada 8 h a los 1/3/5 pares con mejor funding | **−29 % / −24 % / −20 %** (las comisiones se lo comen todo) |

El funding de BTC solo ha superado el 0,01 % cada 8 h (≈11 % anual) en momentos puntuales del
último año, y hoy está casi a cero. **Ahora mismo no compensa**: rinde menos que dejar los USDT en
una cuenta de ahorro. Es una estrategia de mercado alcista: en fases de euforia el funding sube
muy por encima de ese 11 % (no lo he medido aquí; el script lo comprueba con `--dias` más largos).
Lo útil es una **alerta** que avise cuando el funding de BTC/ETH se mantenga alto varios días, y
activarla entonces.

## 5. Estrategia maker en pares cruzados

En vez de competir en velocidad cazando huecos, el bot **deja una orden pasiva** en un par poco
líquido, por ejemplo vender METIS por TRY 1 tick mejor que el mejor precio. Solo cotiza si, en caso
de llenarse, el ciclo se puede cerrar en los otros dos pares líquidos (TRY → USDT → METIS) con al
menos un margen objetivo después de comisiones.

**Ventajas:** no hace falta llegar el primero, porque pones tú el precio. La orden maker paga
0,075 % (0 % en los pares FDUSD con promoción) y además capturas parte del diferencial
compra-venta del par poco líquido.

**El riesgo es la selección adversa:** a menudo te ejecutan justo porque el precio se acaba de
mover en tu contra. Si tardas en cubrir, pierdes.

**Backtest con operaciones reales** (aggTrades de Binance del mismo periodo de 20 min):
- Órdenes de 50 $ en los 1.286 candidatos (par × lado) con al menos 20.000 $/día de volumen y
  pares de cobertura de más de 500.000 $/día.
- Una orden se cuenta como llenada solo si hubo una operación agresora a ese precio o peor.

| Margen objetivo | Ejecuciones | Cobertura lenta (foto siguiente, 0,3–1,3 s) | Cobertura instantánea (precios de antes de la ejecución) |
|---|---|---|---|
| 0,05 % | 70 | **−2,09 $** (−6,3 $/h), 50/70 ganadoras | **+2,98 $** (+8,9 $/h), 69/70 ganadoras |
| 0,10 % | 27 | −1,05 $ (−3,2 $/h), 17/27 | +1,92 $ (+5,8 $/h), 26/27 |
| 0,20 % | 11 | −0,46 $ (−1,4 $/h), 8/11 | +1,17 $ (+3,5 $/h), 11/11 |

- **Todo depende de la velocidad de cobertura.** Cubriendo con 1 s de retraso pierde, sobre todo
  por unas pocas ejecuciones malas: STRK/USDC −1,9 %, W/USDC −0,6 %, MUBARAK/USDC. Cubriendo al
  instante gana casi siempre.
- **Un VPS en Tokio cubriría en 10–50 ms, en medio de los dos extremos.** Ojo: la columna
  "instantánea" es optimista, porque usa precios de antes de la ejecución. Cuando la ejecución la
  provoca un movimiento brusco, ni el servidor más rápido llega a tiempo.
- **Los pares con más tiempo cotizable** son cruces con TRY (METIS, AI, SNX, G, CYBER) y con BTC
  (ICP, ATOM, DOGE).
- **La muestra es pequeña:** 20 minutos, de 11 a 70 ejecuciones. No basta para decidir, pero es la
  **única pista con una ventaja estructural** para alguien que no es una empresa de alta frecuencia.

**Veredicto:** prometedora pero sin demostrar. Merece la fase 2 del plan (simulación en un servidor
con datos en tiempo real) antes de arriesgar nada.

## 6. Fuera de Binance: otras plataformas y otras estrategias

### 6.1 Arbitraje triangular en otros exchanges

Medido con 498 rondas de fotos (cada ~3 s durante 25 min) de 9 exchanges a la vez: Binance, OKX,
Bybit, KuCoin, Gate, MEXC, Bitget, HTX y Kraken. Son 13.300 pares en total. Se usa la comisión
taker de cliente normal con el descuento por token propio y solo cuentan las rutas con al menos
10 $ en cada tramo:

| Exchange | Comisión | Triángulos | Mejor bruto (máx.) | Umbral (3 comisiones) | Episodios rentables |
|---|---|---|---|---|---|
| Binance | 0,075 % | 1.728 | 0,152 % | 0,225 % | 0 |
| Bitget | 0,08 % | 98 | 0,175 % | 0,24 % | 0 |
| KuCoin | 0,08 % | 226 | 0,113 % | 0,24 % | 0 |
| MEXC | 0,05 % | 481 | 0,118 % | 0,15 % | 0 |
| Bybit | 0,10 % | 180 | 0,124 % | 0,30 % | 0 |
| OKX | 0,10 % | 1.151 | 0,099 % | 0,30 % | 0 |
| HTX | 0,20 % | 15 | 0,146 % | 0,60 % | 0 |
| Kraken | 0,40 % | 1.178 | 0,198 % | 1,20 % | 0 |

**El triangular está muerto en todas las plataformas** para un cliente normal. MEXC es el que más se
acerca. Con su taker al 0,025 % (descuento por MX) el umbral bajaría al 0,075 % y la mediana de la
mejor ruta (0,084 %) lo superaría por muy poco, pero con márgenes de céntimos.

### 6.2 Arbitraje entre exchanges (mismo par, distinto precio)

1.811 pares cotizan en 2 o más de esos exchanges. Diferencias después de las dos comisiones, con
al menos 10 $ al mejor precio y menos de un 5 % (lo que pasa de ahí casi siempre son tokens
distintos con el mismo nombre):

| Duración | Episodios en 25 min | Neto mediano | Capacidad mediana | Con transferencias cerradas |
|---|---|---|---|---|
| ≤ 3 s | 1.305 | 0,125 % | 36 $ | 153 |
| 3–30 s | 1.114 | 0,210 % | 33 $ | 150 |
| > 30 s | 406 | 0,201 % | 39 $ | 13 |

A primera vista hay mucho dinero (el "teórico" suma miles de dólares por hora), pero los datos
muestran tres trampas:

1. **El flujo va en una sola dirección.** En los mejores pares, el exchange barato es siempre el
   mismo: AI/USDT de MEXC a Binance 15/0, DYM de MEXC a HTX 33/0, LUMIA de Binance a MEXC 68/0. No
   es arbitraje que se compense solo, sino **arbitraje de traslado**: compras en A, mueves la
   moneda a B y vendes. El hueco existe precisamente porque moverla es lento, caro o arriesgado.
2. **Las comisiones de retiro se comen el margen.** Ejemplo real: OGN en HTX se retira por ERC20 y
   cuesta 218 OGN (~7 $), y a su mejor precio solo había ~9 $ a la venta. Hacen falta varias
   operaciones solo para pagar un traslado.
3. **No sabes si es la misma moneda ni si puedes moverla.** Con los datos públicos de 5 exchanges,
   ~1 de cada 7 casos tenía depósitos o retiros cerrados. Binance, OKX, MEXC y Bybit solo dan ese
   dato con claves de API. Diferencias del 2–3 % que duran minutos (AI, EGLD, SYN) huelen a token
   distinto o a retiros congelados.

Candidatos que merecería la pena verificar (25 min, domingo por la noche):

| Par | Comprar en → vender en | Neto medio | Capacidad mediana | Veces |
|---|---|---|---|---|
| ULTIMA/USDT | KuCoin → HTX | 0,44 % | 317 $ | 22 |
| RVN/USDT | Binance → MEXC | 0,24 % | 593 $ | 7 |
| METIS/USDT | Binance → MEXC | 0,79 % | 304 $ | 4 |
| GMT/USDT | Binance → KuCoin | 0,44 % | 94 $ | 17 |
| OGN/USDT | HTX → MEXC | 0,94 % | 145 $ | 12 |
| DYM/USDT | MEXC → HTX | 1,65 % | 61 $ | 23 |
| AI/USDT | MEXC → Binance | 2,58 % (¿mismo token?) | 110 $ | 10 |

**Veredicto:** hay dinero real en la "cola larga" (monedas pequeñas en exchanges medianos), pero
es un negocio de logística. Exige:

- cuentas verificadas en 4–6 exchanges y el capital repartido;
- comprobar moneda a moneda el contrato, la red, la comisión y el tiempo de retiro;
- asumir el riesgo de contraparte de exchanges como MEXC y HTX, conocidos por congelar retiros.

Se puede automatizar en parte, con un escáner que use tus claves para leer comisiones y estados de
retiro, pero no es dinero automático. Potencial estimado: unos pocos dólares al día por par
verificado, con mucho trabajo.

### 6.3 Funding entre plataformas (lo más sólido que he encontrado)

Corto en el perpetuo de la plataforma que paga más funding y largo en la que paga menos. La
posición es neutral al precio y cobras la diferencia. Son 90 días de historial oficial de Binance,
Bybit, OKX e **Hyperliquid** (un exchange descentralizado de perpetuos), 28 monedas, comisiones
taker de entrada y salida incluidas y capital igual al nocional (cada pata a 2×):

| Moneda | Corto en → largo en | Diferencia media/año | Tramos con el mismo signo | Estrategia (después de comisiones) |
|---|---|---|---|---|
| TAO | Hyperliquid → Binance | 8,8 % | 100 % | **+7,1 %/año** |
| WLD | Hyperliquid → Binance | 10,3 % | 92 % | +5,9 % |
| ONDO | Hyperliquid → Binance | 9,7 % | 94 % | +5,5 % |
| UNI | Hyperliquid → Binance | 6,7 % | 90 % | +4,7 % |
| NEAR | Hyperliquid → Binance | 9,4 % | 94 % | +4,6 % |
| DOGE | Hyperliquid → Bybit | 6,7 % | 90 % | +4,4 % |
| SUI | Hyperliquid → Binance / Bybit | 6–7 % | 86–92 % | +3,9 % |

- **Hyperliquid paga sistemáticamente más funding** que los exchanges centralizados en las altcoins.
  Sus usuarios están más inclinados a ir largos. En 62 de las 168 combinaciones la estrategia sale
  positiva; las 10 mejores dan **+4,8 % anual de media**.
- **Ventajas:** no depende de la velocidad (se toca la posición unas pocas veces por semana), admite
  miles de dólares y la diferencia es muy estable (90–100 % de los tramos con el mismo signo).
- **Riesgos:** liquidación de una pata si el precio se dispara (hay que dejar margen y reequilibrar
  entre plataformas); riesgo de plataforma y del puente de Hyperliquid; que el régimen de funding
  cambie; y el umbral óptimo de cada fila está elegido sobre los mismos datos (algo optimista).
- **Cuánto da:** +4–7 % anual sobre el capital. Con 1.000 $ son ~40–70 $ al año; con 10.000 $,
  ~400–700 $. Es un rendimiento, no un negocio: escala con el capital, no con la astucia.

### 6.4 Arbitraje atómico en DEX de Solana

Ahí el ciclo es atómico: si no da beneficio, la transacción se revierte y no pierdes los tramos.
Pero es la misma carrera de velocidad que en Binance:

- en un año hubo 90 millones de arbitrajes de **1,58 $ de media**;
- más del 98 % de los intentos fallan;
- se cede el 50–70 % del beneficio en propinas a los validadores;
- unos pocos bots se llevan ~60 % del mercado.

No es un buen punto de entrada ([Helius](https://www.helius.dev/blog/solana-mev-report),
[Extropy](https://academy.extropy.io/pages/articles/mev-crosschain-analysis-2025.html),
[Dysnix](https://dysnix.com/blog/solana-arbitrage-bot-setup)).

### 6.5 Clasificación final

| Estrategia | Resultado con datos | Depende de la velocidad | Veredicto |
|---|---|---|---|
| Funding entre plataformas (Hyperliquid ↔ CEX) | +4–7 %/año, estable | No | **La más sólida** (rendimiento modesto) |
| Maker en pares cruzados (Binance) | −1,4 a +8,9 $/h según velocidad | Sí, de la cobertura | Prometedora, sin demostrar |
| Traslado entre exchanges (cola larga) | Hay huecos reales, flujo en un solo sentido | Poco | Posible, mucho trabajo y riesgo |
| Funding en una sola plataforma | ~0–2 %/año ahora | No | Esperar a un mercado alcista (alerta) |
| Triangular taker (9 exchanges) | 0 episodios capturables | Mucho | No |
| Arbitraje atómico en Solana | Dominado por pocos bots | Extrema | No |

### 6.6 Fiat, oro y plata

- **Oro y plata tokenizados entre exchanges** (PAXG y XAUT en 9 exchanges, XAUM, KAG): solo 8
  episodios en 25 min, de 0,06–0,07 % (0,10–0,17 $ por operación de 250 $), siempre por pares con
  lira turca y en un solo sentido. Nada aprovechable.
- **Fiat entre exchanges:** 481 episodios en 25 min, pero los "buenos" son COP, ARS o BRL entre MEXC,
  Bitget y Binance, **siempre en el mismo sentido** (56/0, 30/0). Moverlos exige transferencias
  bancarias en cada país: no se puede automatizar ni repetir.
- **Funding de perpetuos de metales entre plataformas:** 0 de 11 combinaciones positivas.
- **Forex y metales con brokers tradicionales:** el broker es tu contraparte. Detecta el arbitraje,
  rechaza órdenes con "last look", anula ganancias y cierra cuentas
  ([daytrading.com](https://www.daytrading.com/safety/price-feed-latency-arbitrage),
  [Forex Peace Army](https://www.forexpeacearmy.com/community/goto/post?id=394212)).

### 6.7 Una sola plataforma, sin triangular

| Estrategia (Binance salvo indicación) | Datos | Resultado |
|---|---|---|
| Comprar spot y vender futuro trimestral (rentabilidad fijada) | Precios de hoy, Binance/Bybit/OKX | **+3,3 %/año** como máximo (BTC a mar.–sep. 2027) |
| Spot + corto en perpetuo cobrando funding, BTC/ETH | 1 año | +1,5–2 %/año |
| Igual pero con **oro tokenizado** (PAXG o XAUT spot + su perpetuo) | 90 días: funding +5,4–6 %/año, positivo el 86–93 % de los pagos | **+3,4–3,8 %/año** sobre el capital; ~5–8 % con margen unificado (Bybit) |
| Diferencia momentánea spot–perpetuo | 20 min, 357 pares | 3 entradas, **3 pérdidas** (−1,84 $) |
| Microoperaciones en stablecoins sin comisión (U/USDT, U/USDC) | 7 días de velas de 1 min | **+0,02 $/día**: casi no se mueven (4 ticks/día) |
| Igual con comisión (USDC/USDT, FDUSD/USDT) | 7 días | Pierde (−0,15 a −1,7 $/día) |

### 6.8 Sobre "aunque deje 0,10 $ por cada 100 $"

Contar oportunidades ya incluía **cualquier margen positivo, por pequeño que sea** (incluso
0,001 %). El problema no es pedir demasiado margen: dentro de una plataforma el mercado casi nunca
deja un hueco que supere las comisiones.

Para quedarte con 0,10 $ por cada 100 $, el hueco bruto tiene que ser de:

| Estrategia | Hueco bruto necesario |
|---|---|
| Triangular con BNB | 0,325 % (0,225 % de comisiones + 0,1 %) |
| Spot–perpetuo | 0,35 % |

En Binance, la mejor ruta de todo el mercado tuvo una mediana del 0,11 % y solo superó el 0,30 %
unas pocas veces por hora, durante menos de 1–2 segundos. Entre exchanges sí hay huecos de 0,1–0,2 %
que duran más, pero van en un solo sentido y obligan a mover fondos.

Lo que sí existe dentro de una plataforma son **rendimientos tipo "interés"** (carry con oro o BTC,
futuros con vencimiento): unos 3–6 % anual, sin depender de la velocidad. Con 500 $ son
**~15–30 $ al año**.

### 6.9 Comprar barato y vender más caro (especulación, no arbitraje)

**BTC, 2 años en velas de 5 min** (`research.directional_backtest`), con 500 $ y comisión del
0,075 % por lado:

| Estrategia | 2 años | 1.er año | 2.º año | Peor caída | $/día |
|---|---|---|---|---|---|
| Comprar y mantener | +34 % | +77 % | −24 % | −54 % | 0,23 |
| Rejilla: comprar cada −1–5 %, vender con +1–3 % | +11 a +17 % | +29 a +39 % | −7 a −19 % | −34 % | 0,08–0,12 |
| Comprar tras −2 % y vender con +1 % | −51 a −73 % (con un 77 % de aciertos) | | | −69 a −82 % | pierde |
| Comprar tras −4 % y vender con +2 % | +23 a +28 % | +27 a +31 % | −5 % | −33 % | 0,16–0,19 |
| Al azar con +1 % / −1 % | −52 % | | | −57 % | pierde |

Las rejillas acaban con todos los tramos atrapados cuando el mercado baja (−110 a −147 $ latentes,
alguno durante 370 días): en la práctica son "comprar y mantener" en pequeño.

**Señales de oro tipo "BUY 4047 · TP 4053/4055/4057 · SL 4040"** (`research.signal_backtest`), con
305 días de XAUUSDT en velas de 5 min, 3 objetivos y stop:

| Entrada | Señales | Acierto | Media sin costes | Con spread MT5 (0,30 $) | Con Binance |
|---|---|---|---|---|---|
| Comprar al azar | 1.171 | 45 % | −0,39 $ | −0,69 $ (−807 $/año) | −3,6 a −4,9 $ |
| Dirección al azar | 1.208 | 47 % | −0,08 $ | −0,38 $ (−456 $/año) | −3,3 a −4,6 $ |
| Retroceso en tendencia | 1.640 | 47 % | −0,01 $ | −0,31 $ (−508 $/año) | −3,3 a −4,7 $ |

Sin costes, estas señales valen lo mismo que tirar una moneda (media ≈ 0); con costes, pierden.
Los vendedores de señales enseñan las rachas buenas: sin un historial verificado de meses, no hay
forma de distinguirlos del azar.

## 7. Plan recomendado

Por fases, cada una con un criterio claro para seguir o parar. Nada de dinero real hasta la
fase 3.

**Prioridad nueva tras el estudio multiplataforma: funding entre plataformas (fase F)**

Es lo único que ya sale positivo con datos y que no depende de la velocidad, así que puede ir
antes que todo lo demás:

1. Comprobar que tu país te permite usar Hyperliquid y los futuros de Binance, OKX o Bybit.
2. Añadir al bot un módulo que vigile la diferencia de funding entre plataformas, con el mismo
   `funding_cross` cada 8 h en vivo, y simule en papel la entrada y salida durante 2–4 semanas.
3. Después, real con poco: 200–500 $ por pata, apalancamiento 2× como máximo y vigilancia
   automática del margen de cada pata. Antes de que una pata se acerque a la liquidación, el bot
   reduce la posición o pasa margen entre plataformas.
4. **Criterio:** si en papel la diferencia cobrada menos comisiones no supera el ~3 % anual,
   no compensa frente a dejar los USDT en un producto de ahorro.

**Fase 0 — hoy, gratis**
- Activa en Binance "usar BNB para pagar comisiones" y guarda unos pocos dólares en BNB. El umbral
  baja del 0,30 % al 0,225 % y en el bot es `comision_pct = 0.075`.
- Ejecuta `python -m research.check_server` en tu servidor de Railway. Lo más probable es que
  Binance lo bloquee, pero no cuesta nada comprobarlo.

**Fase 1 — una semana con un VPS en Tokio (5–12 $)**
- `bash deploy/setup-vps.sh` instala todo y pasa el comprobador. Luego el bot queda como servicio
  24/7 en **modo papel**, sin lista de activos (todo el mercado) y con varias bases (USDT, USDC,
  BTC, FDUSD).
- Deja también `research.funding_alert` corriendo, para enterarte si el funding se calienta.
- Al cabo de la semana, `python -m triarb analizar` responde: cuántas oportunidades con capacidad
  real aparecen al día, cuánto duran y cuántas captura la simulación con la latencia del servidor.
- **Criterio:** si el beneficio simulado al día no supera con holgura el coste del servidor
  (~0,3 $/día), se abandona el arbitraje taker. Es el resultado más probable según los datos de
  arriba y la experiencia de harjus.

**Fase 2 — estrategia maker en papel (solo si la fase 1 o el backtest maker lo justifican)**
- Exige código nuevo en el bot: órdenes que se quedan en el libro, recolocarlas en cada
  actualización, cancelarlas si el margen desaparece, cubrir al instante al llenarse y gestionar
  inventario.
- Se mide 1–2 semanas en papel en el servidor, con la simulación de llenados de §5.
- **Criterio:** al menos 100 ejecuciones simuladas, beneficio positivo después de comisiones y
  pérdidas por selección adversa controladas.

**Fase 3 — testnet y luego real con poco**
- Testnet 1 semana (prueba la mecánica, no la rentabilidad: sus libros no son los reales).
- Real con 100–200 $, `max_ejecuciones` bajo, `perdida_max_diaria_usd` pequeño y la clave sin
  retiros y restringida a la IP del servidor. Subir solo si el resultado real coincide con el
  simulado.

**Funding:** con la alerta corriendo, cuando salte se repasa `research.funding_study` y, si
compensa, se añade al bot un modo cash and carry (es la estrategia más sencilla y la única que no
depende de la velocidad).

**Qué no hacer**
- Pagar Netrunbot u otro bot "llave en mano": venden esta misma estrategia, que los datos
  muestran sin margen para un cliente normal.
- Operar en real desde tu PC: los datos te llegan con segundos de retraso.
- Meter más capital para ganar más. El límite lo pone la liquidez al mejor precio (decenas o
  cientos de dólares por oportunidad), no tu saldo.

**Expectativa honesta:** incluso en el mejor caso, esto da unos pocos dólares al día y exige
mucho trabajo. Merece la pena como proyecto para aprender o como base para la estrategia maker,
no como fuente de ingresos.

## 8. Cómo reproducir

```powershell
# 20 min de fotos completas del mercado (REST, coherentes entre pares)
.venv\Scripts\python -m research.collect_books --minutos 20
# Arbitraje triangular: ventaja bruta, capacidad, duración, comisiones
.venv\Scripts\python -m research.triangle_study
# Backtest maker con operaciones reales (descarga aggTrades del mismo periodo)
.venv\Scripts\python -m research.maker_backtest
# Funding: un año de historial y simulación de estrategias
.venv\Scripts\python -m research.funding_study --top 40 --dias 365
# ¿Sirve este servidor?
.venv\Scripts\python -m research.check_server
# 9 exchanges a la vez: triangular en cada uno y diferencias entre ellos
.venv\Scripts\python -m research.collect_multi --minutos 25
.venv\Scripts\python -m research.multi_study
# Funding entre Binance, Bybit, OKX e Hyperliquid (90 días); con --monedas XAU XAG PAXG XAUT XPT para metales
.venv\Scripts\python -m research.funding_cross --dias 90
# Fiat, oro y plata entre exchanges + micro-operaciones con tu capital
.venv\Scripts\python -m research.assets_study --capital 500
# Una sola plataforma: futuros con vencimiento, spot–perpetuo, stablecoins
.venv\Scripts\python -m research.dated_basis
.venv\Scripts\python -m research.collect_basis --minutos 20
.venv\Scripts\python -m research.basis_study
.venv\Scripts\python -m research.stable_grid --dias 7
# ¿Qué exchanges responden desde un servidor? (Node, sin dependencias)
node deploy/check-endpoints.mjs
```

Fuentes: resultados de harjus en producción ([1](https://shufflingbytes.com/posts/binance-triangular-arbitrage/),
[2](https://shufflingbytes.com/posts/harjus-release-4.0.0/)) · comisiones
([Bitget Academy](https://www.bitget.com/academy/binance-fees-2026), [FDUSD maker 0 %](https://www.treeofalpha.com/preview_article?id=1768460403370),
[U sin comisión](https://www.cryptoninjas.net/news/binance-announces-united-stables-u-with-new-spot-trading-pairs-on-jan-13/)) ·
Railway bloqueado por Binance ([foro de Railway](https://station.railway.com/questions/deployment-is-crashing-7b190d57)) ·
regiones de Railway ([docs](https://docs.railway.com/reference/deployment-regions)) · comisiones de otros exchanges
([Coin Bureau](https://coinbureau.com/review/bybit-vs-okx), [KuCoin](https://rononcrypto.com/en/blog/kucoin-fees-2026/),
[MEXC](https://www.mexc.com/crypto-pulse/article/crypto-exchange-fees-in-2026-110091), [comparativa](https://jason-finance.com/en/exchange-fee-comparison/)) ·
MEV en Solana ([Helius](https://www.helius.dev/blog/solana-mev-report), [Extropy](https://academy.extropy.io/pages/articles/mev-crosschain-analysis-2025.html)) ·
arbitraje entre exchanges en la literatura ([resumen](https://medium.com/@gwrx2005/high-frequency-arbitrage-and-profit-maximization-across-cryptocurrency-exchanges-4842d7b7d4d9)).
Datos de mercado: APIs públicas de Binance, OKX, Bybit, KuCoin, Gate, MEXC, Bitget, HTX, Kraken e Hyperliquid (11–12-10-2026).
