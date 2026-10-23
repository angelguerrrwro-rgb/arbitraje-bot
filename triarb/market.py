"""Datos de mercado en tiempo real por websocket.

- fuente "libro": <par>@bookTicker, mejor bid/ask al instante (como harjus).
- fuente "profundidad": <par>@depth{5,10,20}@100ms, varios niveles cada 100 ms (como bmino).

Nada de pedir precios por REST en bucle (lo que hacen Hephyrius, Roibal, t0mm4rx y Cherecho):
es lento y consume el límite de peticiones.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from collections import deque

import aiohttp

from .binance import loads
from .models import Book

log = logging.getLogger("triarb.mercado")

STREAMS_PER_CONNECTION = 300  # Binance admite 1024; repartimos la carga
SUBSCRIBE_BATCH = 100
SUBSCRIBE_GAP_S = 0.3  # máximo 5 mensajes de control por segundo y conexión


class MarketData:
    """Libros locales. @bookTicker solo envía cambios, así que un par tranquilo puede pasar
    minutos sin mensajes: por eso al conectar (y al reconectar) se carga una foto inicial por
    REST con `snapshot`, que devuelve la lista de /api/v3/ticker/bookTicker.

    Cada conexión lleva además el stream @trade de un par líquido. Sus mensajes traen la hora
    del evento en Binance, y comparándola con la hora local (corregida con `clock_offset_ms`)
    se mide con cuánto retraso llegan de verdad los datos."""

    def __init__(self, session: aiohttp.ClientSession, ws_url: str, symbols: list[str], source: str, levels: int,
                 snapshot=None, clock_offset_ms: float = 0.0):
        self.session = session
        self.ws_url = ws_url
        self.symbols = sorted(symbols)
        self.source = source
        self.levels = levels
        self.snapshot = snapshot
        self.clock_offset_ms = clock_offset_ms
        self.books: dict[str, Book] = {}
        self.dirty: set[str] = set()
        self.event = asyncio.Event()
        self.msg_count = 0
        self.reconnects = 0
        self.connected = 0
        self.lags: deque[float] = deque(maxlen=500)
        self.lag_symbol = "BTCUSDT" if "BTCUSDT" in self.symbols else (self.symbols[0] if self.symbols else "")
        self._stream_symbol: dict[str, str] = {}

    @property
    def lag_ms(self) -> float | None:
        """Mediana del retraso evento en Binance → llegada aquí, en las últimas muestras."""
        return statistics.median(self.lags) if self.lags else None

    def _stream(self, symbol: str) -> str:
        if self.source == "libro":
            name = f"{symbol.lower()}@bookTicker"
        else:
            name = f"{symbol.lower()}@depth{self.levels}@100ms"
        self._stream_symbol[name] = symbol
        return name

    async def run(self) -> None:
        chunks = [self.symbols[i : i + STREAMS_PER_CONNECTION] for i in range(0, len(self.symbols), STREAMS_PER_CONNECTION)]
        await asyncio.gather(*(self._connection(chunk) for chunk in chunks))

    async def _connection(self, symbols: list[str]) -> None:
        streams = [self._stream(s) for s in symbols]
        if self.lag_symbol:
            streams.append(f"{self.lag_symbol.lower()}@trade")
        backoff = 1.0
        while True:
            try:
                async with self.session.ws_connect(self.ws_url, autoping=True, heartbeat=None, max_msg_size=0) as ws:
                    self.connected += 1
                    for i in range(0, len(streams), SUBSCRIBE_BATCH):
                        await ws.send_str(
                            json.dumps({"method": "SUBSCRIBE", "params": streams[i : i + SUBSCRIBE_BATCH], "id": i + 1})
                        )
                        await asyncio.sleep(SUBSCRIBE_GAP_S)
                    await self._seed(symbols)
                    backoff = 1.0
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self._on_text(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as e:  # red caída, DNS, cierre de Binance a las 24 h...
                log.warning("Websocket desconectado: %s", e)
            finally:
                self.connected = max(0, self.connected - 1)
            # Sin conexión los precios dejan de ser válidos: se borran hasta que vuelvan
            for s in symbols:
                self.books.pop(s, None)
            self.reconnects += 1
            log.info("Reconectando %d pares en %.0f s", len(symbols), backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _seed(self, symbols: list[str]) -> None:
        if self.snapshot is None:
            return
        try:
            tickers = await self.snapshot()
        except Exception as e:
            log.warning("No se pudo cargar la foto inicial de libros: %s", e)
            return
        wanted = set(symbols)
        now = time.monotonic()
        for t in tickers:
            sym = t["symbol"]
            if sym not in wanted or sym in self.books:
                continue
            bid, bid_q, ask, ask_q = float(t["bidPrice"]), float(t["bidQty"]), float(t["askPrice"]), float(t["askQty"])
            if bid > 0 and ask > 0 and bid_q > 0 and ask_q > 0:
                self.books[sym] = Book([(bid, bid_q)], [(ask, ask_q)], now)
                self.dirty.add(sym)
        self.event.set()

    def _on_text(self, text: str) -> None:
        msg = loads(text)
        data = msg.get("data")
        if data is None:
            return  # confirmación de SUBSCRIBE
        stream = msg.get("stream", "")
        if stream.endswith("@trade"):
            self.lags.append(time.time() * 1000 + self.clock_offset_ms - data["E"])
            return
        self.msg_count += 1
        now = time.monotonic()
        if self.source == "libro":
            sym = data["s"]
            bid, bid_q, ask, ask_q = float(data["b"]), float(data["B"]), float(data["a"]), float(data["A"])
            if bid <= 0 or ask <= 0 or bid_q <= 0 or ask_q <= 0:
                self.books.pop(sym, None)
                return
            bids, asks = [(bid, bid_q)], [(ask, ask_q)]
        else:
            sym = self._stream_symbol.get(stream)
            if sym is None:
                return
            bids = [(float(p), float(q)) for p, q in data["bids"]]
            asks = [(float(p), float(q)) for p, q in data["asks"]]
            if not bids or not asks:
                self.books.pop(sym, None)
                return
        book = self.books.get(sym)
        if book is None:
            self.books[sym] = Book(bids, asks, now)
        else:
            book.bids, book.asks, book.ts = bids, asks, now
        self.dirty.add(sym)
        self.event.set()

    def take_dirty(self) -> set[str]:
        dirty, self.dirty = self.dirty, set()
        return dirty

    async def wait_ready(self, timeout: float, ratio: float = 0.95) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.books) >= ratio * len(self.symbols):
                break
            await asyncio.sleep(0.5)
        return len(self.books)
