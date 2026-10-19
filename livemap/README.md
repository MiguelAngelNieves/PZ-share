# PZ Live Map

Mapa web en vivo de la partida: dónde está cada jugador, su vida, si va en vehículo, rastros y actividad
(quién entró, salió o murió). Pensado para correr en una Raspberry Pi con Docker.

```
Juego (mod PZLiveMap) ──► Zomboid/Lua/pzlivemap.json ──► PZ Share (PC del host) ──► Pi /api/update ──► navegadores (en vivo)
```

## Instalar en la Pi

```bash
scp -r livemap arcangel@<ip-de-la-pi>:~/pzmap
ssh arcangel@<ip-de-la-pi>
cd ~/pzmap && ./instalar.sh
sudo tailscale funnel --bg --set-path /pzmap http://127.0.0.1:8095   # o "serve" para solo tu tailnet
```

`instalar.sh` crea `.env` con un token y una llave de vista aleatorios y te los muestra al final.

## Conectar PZ Share

PZ Share → **🗺 Mapa en vivo**: pega la dirección (`https://<tu-pi>.ts.net/pzmap/`), el token y la llave →
**Instalar el mod en la partida** → **⤒ Subir partida**. El mod y los ajustes viajan con la partida.

## Mapa real del juego (como el mapa dentro del juego)

El mapa se genera una vez desde los archivos del juego con [pzmap2dzi](https://github.com/cff29546/pzmap2dzi) (MIT),
en vista superior y con colores tipo mapa del juego.

1. En Windows, doble clic a `renderizar_mapa.bat` (revisa arriba la ruta del juego y la de salida).
   Descarga pzmap2dzi, lo configura y renderiza solo la vista superior. Tarda unas horas la primera vez.
2. Pásalo a la Pi:
   ```bash
   scp D:\pzmap_render\base_top.zip arcangel@<ip-de-la-pi>:~/pzmap/data/
   ssh arcangel@<ip-de-la-pi> "cd ~/pzmap/data && mkdir -p tiles && unzip -oq base_top.zip -d tiles && rm base_top.zip"
   ```
3. Recarga la página: el mapa detecta `data/tiles/base_top` solo y pone a los jugadores encima.

Para el modo «en este PC» de PZ Share, elige la carpeta de salida en **🗺 Mapa en vivo**.

## Ajustes (`.env`)

| Variable | Para qué |
|---|---|
| `PZMAP_TOKEN` | Obligatorio. Lo usa PZ Share para enviar posiciones |
| `PZMAP_VIEW_KEY` | Llave para ver (`?k=`). Vacía = cualquiera con el link |
| `PZMAP_TITLE` | Título de la página |
| `PZMAP_TILES` | Opcional: plantilla de tiles Leaflet si tienes un mapa en tiles |
| `PZMAP_IMAGE_BOUNDS` | Opcional: si pones `data/map.png`, sus límites en tiles del juego `x0,y0,x1,y1` |

Sin tiles, el mapa usa una cuadrícula de celdas (300 tiles) con los pueblos. Los pueblos están en
`static/towns.json` (coordenadas aproximadas); puedes poner los tuyos en `data/towns.json`.

El estado vive en memoria y se guarda en `data/state.json` cada 3 minutos (pocas escrituras para la microSD).
