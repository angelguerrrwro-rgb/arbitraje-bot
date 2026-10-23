"""Avisos por Telegram (idea de t0mm4rx). Si no está activo, no hace nada."""

from __future__ import annotations

import asyncio
import logging

import aiohttp

log = logging.getLogger("triarb.telegram")


class Telegram:
    def __init__(self, session: aiohttp.ClientSession, token: str, chat_id: str, enabled: bool):
        self.session = session
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled and bool(token) and bool(chat_id)
        self._tasks: set[asyncio.Task] = set()

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        task = asyncio.create_task(self._send(text))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            async with self.session.post(url, data={"chat_id": self.chat_id, "text": text}) as resp:
                if resp.status >= 400:
                    log.warning("Telegram respondió %s", resp.status)
        except Exception as e:
            log.warning("No se pudo enviar el aviso de Telegram: %s", e)
