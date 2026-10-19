"""Ajusta conf/conf.yaml de pzmap2dzi para renderizar solo la vista superior (como el mapa del juego)."""
import re
import sys
from pathlib import Path

conf, pz_root, out, sqr = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
omit = sys.argv[5] if len(sys.argv) > 5 else None  # solo isométrica: niveles de zoom que se omiten (0 = máximo detalle)
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
    setting("image_save_options", "{webp: {quality: 80, method: 4}, jpg: {quality: 85}}")
    setting("omit_levels", omit)
conf.write_text(text, encoding="utf-8")
print(f"OK: juego en {pz_root}, salida en {out}, vista superior {sqr} px por cuadro"
      + (f", isométrica piso 0 omitiendo {omit} niveles" if omit is not None else ""))
