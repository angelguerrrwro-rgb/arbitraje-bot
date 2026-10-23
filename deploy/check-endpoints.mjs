// ¿Qué exchanges responden desde ESTA máquina? Sin dependencias (Node 18+).
// Uso: node deploy/check-endpoints.mjs
// Un 451/403 suele ser bloqueo por ubicación o por rango de IPs del proveedor (p. ej. Railway).

const checks = [
  ["Binance spot", "https://api.binance.com/api/v3/ping"],
  ["Binance futuros", "https://fapi.binance.com/fapi/v1/ping"],
  ["OKX", "https://www.okx.com/api/v5/public/time"],
  ["Bybit", "https://api.bybit.com/v5/market/time"],
  ["Hyperliquid", "https://api.hyperliquid.xyz/info", { type: "meta" }],
];

for (const [name, url, body] of checks) {
  const times = [];
  let status = "?";
  let note = "";
  for (let i = 0; i < 4; i++) {
    const t = performance.now();
    try {
      const res = await fetch(url, body
        ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }
        : {});
      status = res.status;
      const text = await res.text();
      if (res.status >= 400) note = text.slice(0, 120).replace(/\s+/g, " ");
      times.push(performance.now() - t);
    } catch (e) {
      status = "error";
      note = String(e.cause?.code ?? e.message);
      break;
    }
  }
  times.sort((a, b) => a - b);
  const ok = status === 200;
  const ms = times.length > 1 ? `${times[1].toFixed(0)} ms` : "—";
  console.log(`${ok ? "OK " : "MAL"}  ${name.padEnd(16)} HTTP ${String(status).padEnd(5)} ping ${ms}${note ? "  · " + note : ""}`);
}
