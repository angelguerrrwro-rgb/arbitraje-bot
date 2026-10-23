#!/usr/bin/env bash
# Prepara un VPS Ubuntu 22.04/24.04 para el bot (recomendado: Tokio, junto a Binance).
# Uso, con el proyecto copiado en ~/arbitraje-bot:  bash deploy/setup-vps.sh
set -euo pipefail
cd "$(dirname "$0")/.."

sudo apt-get update -y
sudo apt-get install -y python3 python3-venv chrony
sudo systemctl enable --now chrony          # reloj sincronizado (Binance rechaza firmas con la hora mal)

python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
[ -f config.toml ] || cp config.example.toml config.toml

echo
echo "== ¿Sirve este servidor? =="
.venv/bin/python -m research.check_server

echo
echo "Si el resultado es bueno, instala el servicio (modo papel, 24/7):"
echo "  sudo cp deploy/triarb.service /etc/systemd/system/ && sudo sed -i \"s#__DIR__#$PWD#g; s#__USER__#$USER#g\" /etc/systemd/system/triarb.service"
echo "  sudo systemctl daemon-reload && sudo systemctl enable --now triarb"
echo "  journalctl -u triarb -f        # ver el registro"
