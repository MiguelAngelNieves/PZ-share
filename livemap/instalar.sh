#!/usr/bin/env bash
# Instala o actualiza el mapa en vivo en la Raspberry Pi (Docker + Tailscale).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/^PZMAP_TOKEN=.*/PZMAP_TOKEN=$(openssl rand -hex 24)/" .env
  sed -i "s/^PZMAP_VIEW_KEY=.*/PZMAP_VIEW_KEY=$(openssl rand -hex 6)/" .env
  echo "Creé .env con token y llave nuevos."
fi
mkdir -p data
docker compose up -d --build
sleep 2
curl -fsS http://127.0.0.1:8095/config.json >/dev/null && echo "✓ El servicio responde en http://127.0.0.1:8095"
echo
echo "Para verlo desde fuera de casa (como la casita), publícalo en una ruta propia:"
echo "  sudo tailscale funnel --bg --set-path /pzmap http://127.0.0.1:8095"
echo "Solo para tu tailnet, cambia 'funnel' por 'serve'."
echo
echo "Datos para PZ Share → Mapa en vivo:"
echo "  Dirección: https://$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || echo TU-PI.ts.net)/pzmap/"
grep -E '^PZMAP_(TOKEN|VIEW_KEY)=' .env | sed 's/^PZMAP_TOKEN=/  Token: /; s/^PZMAP_VIEW_KEY=/  Llave para ver: /'
