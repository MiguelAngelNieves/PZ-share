"""Ajusta conf/conf.yaml de pzmap2dzi para renderizar solo la vista superior (como el mapa del juego)."""
import re
import sys
from pathlib import Path

conf, pz_root, out, sqr = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
omit = sys.argv[5] if len(sys.argv) > 5 else None  # solo isométrica: niveles de zoom que se omiten (0 = máximo detalle)
# Opcional: solo una zona, en celdas de 256×256 tiles: "x,y,ancho,alto" (Riverside ≈ 21,19,7,6). Vacío = todo el mapa.
region = sys.argv[6].strip() if len(sys.argv) > 6 else ""

# Zonas listas, en tiles del juego (x1, y1, x2, y2): las coordenadas que muestra la web al pasar el mouse.
ZONAS = {
    "riverside": (3600, 4800, 8250, 7600),
    "muldraugh": (9700, 8700, 11700, 10900),
    "westpoint": (11000, 6200, 12800, 7700),
    "rosewood": (7300, 10900, 8900, 12400),
    "marchridge": (9300, 12000, 10900, 13400),
    "louisville": (11400, 600, 14500, 4200),
    "valleystation": (12900, 4900, 14500, 6500),
}
CELL = 256  # B42: celdas de 256×256 tiles


def region_cells(text):
    """'riverside' | 't:x1,y1,x2,y2' (tiles) | 'x,y,ancho,alto' (celdas) → [x, y, ancho, alto] en celdas."""
    text = text.lower().replace(" ", "")
    if text in ZONAS or text.startswith("t:"):
        x1, y1, x2, y2 = ZONAS[text] if text in ZONAS else [int(float(v)) for v in text[2:].split(",")]
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        cx, cy = x1 // CELL, y1 // CELL
        return [cx, cy, x2 // CELL - cx + 1, y2 // CELL - cy + 1]
    cells = [int(v) for v in text.split(",")]
    if len(cells) != 4:
        raise SystemExit("Zona inválida: usa un nombre, t:x1,y1,x2,y2 (tiles) o x,y,ancho,alto (celdas)")
    return cells
text = conf.read_text(encoding="utf-8")


def block(key, value):
    global text
    text, n = re.subn(rf"(?m)^({key}: \|-[^\n]*\n)\s+[^\n]*", lambda m: m.group(1) + "    " + value, text)
    if not n:
        raise SystemExit(f"No encontré '{key}' en {conf}")


def setting(key, value):
    global text
    text, n = re.subn(rf"(?m)^(\s+{key}:)\s*[^\n#]*", lambda m: f"{m.group(1)} {value}", text, count=1)
    if not n:
        raise SystemExit(f"No encontré '{key}' en {conf}")


block("pz_root", pz_root.replace("\\", "/"))
block("output_root", out.replace("\\", "/"))
setting("top_view_square_size", sqr)
setting("top_view_color_mode", "carto-zed")
if omit is not None:
    # Isométrica ligera: solo el piso de la calle (sin techos ni pisos altos), en webp y sin los niveles más finos.
    setting("layer_range", "[0, 1]")
    setting("image_fmt_base_layer0", "webp")   # ~40% menos que jpg con la misma pinta
    setting("image_save_options", "{webp: {quality: 50, method: 4}, jpg: {quality: 85}}")
    setting("omit_levels", omit)
    if region:
        cells = region_cells(region)
        setting("render_cell_range", "[[" + ", ".join(map(str, cells)) + "]]")
    else:
        setting("render_cell_range", "all")
conf.write_text(text, encoding="utf-8")
print(f"OK: juego en {pz_root}, salida en {out}, vista superior {sqr} px por cuadro"
      + (f", isométrica piso 0 omitiendo {omit} niveles" if omit is not None else "")
      + (f", solo la zona {region}" if region else ""))
