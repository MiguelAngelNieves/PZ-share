"""
PZ Share — partida compartida de Project Zomboid entre amigos, sin servidor.

Cualquiera hace Host desde su PC. La partida vive en un repo privado de GitHub
y esta app la baja antes de jugar y la sube al cerrar el juego. Sin comandos.
"""
import datetime
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import io
import urllib.parse
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

# ───────────────────────── Constantes ─────────────────────────
APP_NAME = "PZ Share"
STEAM_APP_ID = "108600"
GAME_EXES = ("ProjectZomboid64.exe", "ProjectZomboid32.exe")
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "PZShare"
CONFIG_FILE = CONFIG_DIR / "config.json"
ZOMBOID_HOME = Path.home() / "Zomboid"
LOCK_FILE = "host.lock"
GROUP_FILE = "pzshare.json"  # ajustes compartidos del grupo (viaja con la partida)
GIT_DOWNLOAD = "https://git-scm.com/download/win"

GITIGNORE = """# PZ Share: solo se comparte la partida. Graficos, teclas y logs quedan locales.
/*
!/.gitignore
!/host.lock
!/pzshare.json
!/mods/
!/Saves/
/Saves/*
!/Saves/Multiplayer/
!/Server/
!/db/
"""

C = {
    "bg": "#0F1311",
    "card": "#181E1B",
    "card2": "#202824",
    "border": "#2A332E",
    "text": "#E8EDEA",
    "muted": "#8A968F",
    "accent": "#5CC26B",
    "accent_h": "#4AA859",
    "warn": "#E3B341",
    "danger": "#E5534B",
    "danger_h": "#C9423B",
}
FONT = "Segoe UI" if IS_WIN else "Helvetica"


# ───────────────────────── Config ─────────────────────────
def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ───────────────────────── Git ─────────────────────────
class GitError(Exception):
    pass


def git_installed():
    return shutil.which("git") is not None


def git(args, cwd, check=True):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"  # nunca colgarse esperando input en consola
    p = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", creationflags=NO_WINDOW, env=env,
    )
    if check and p.returncode != 0:
        raise GitError((p.stderr or p.stdout).strip() or f"git {' '.join(args)} falló")
    return p.stdout.strip()


def friendly(err):
    """Traduce errores de git a algo entendible, sin ocultar el detalle real."""
    raw = str(err).strip()
    s = raw.lower()
    if "publickey" in s or "host key verification" in s:
        msg = ("El repo está conectado por SSH y no tienes llave configurada. "
               "Usa el link HTTPS (https://github.com/...) en Ajustes.")
    elif ("authentication" in s or "terminal prompts disabled" in s or "403" in s
          or "permission to" in s or "denied" in s):
        msg = ("GitHub no te dejó entrar. Revisa que aceptaste la invitación al repo "
               "y que iniciaste sesión con la cuenta correcta.")
    elif "not found" in s and "repository" in s:
        msg = "No se encontró el repo. Revisa el link y que aceptaste la invitación."
    elif "could not resolve host" in s or "unable to access" in s:
        msg = "Sin conexión con GitHub. Revisa tu internet."
    elif "conflict" in s:
        msg = "Hubo un conflicto: alguien jugó al mismo tiempo. Avísale al grupo antes de seguir."
    else:
        return raw
    return f"{msg}\n\nDetalle: {raw.splitlines()[-1][:300]}"


class Repo:
    def __init__(self, folder: Path, player: str):
        self.folder = Path(folder)
        self.player = player

    # — util —
    def g(self, *args, check=True):
        return git(list(args), self.folder, check)

    def remote_has_commits(self):
        return bool(self.g("ls-remote", "--heads", "origin"))

    def has_commits(self):
        return bool(self.g("rev-parse", "--verify", "HEAD", check=False))

    def ensure_identity(self):
        # El nombre del jugador va en la config local del repo (así se ve en "Última vez").
        self.g("config", "user.name", self.player)
        if not self.g("config", "user.email", check=False):
            self.g("config", "user.email", f"{self.player.replace(' ', '.')}@pzshare.local")

    def ensure_gitignore(self):
        gi = self.folder / ".gitignore"
        if not gi.exists():
            gi.write_text(GITIGNORE, encoding="utf-8")
            return True
        text = gi.read_text(encoding="utf-8", errors="replace")
        missing = [ln for ln in (f"!/{GROUP_FILE}", "!/mods/") if ln not in text.splitlines()]
        if missing:  # repos creados con versiones viejas
            gi.write_text(text.rstrip("\n") + "\n" + "\n".join(missing) + "\n", encoding="utf-8")
            return True
        return False

    # — ajustes del grupo (pzshare.json, se comparte con todos) —
    def group(self):
        try:
            return json.loads((self.folder / GROUP_FILE).read_text(encoding="utf-8"))
        except Exception:
            return {}

    def set_group(self, **kw):
        data = self.group()
        data.update(kw)
        (self.folder / GROUP_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # — historial —
    def history(self, limit=300):
        """Sesiones a partir de los commits: [{who, start, end, minutes}] de la más nueva a la más vieja."""
        if not self.has_commits():
            return []
        out = self.g("log", f"-{limit}", "--format=%an\x1f%at\x1f%s", check=False)
        rows = []
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3 and parts[1].isdigit():
                rows.append((parts[0], int(parts[1]), parts[2]))
        rows.reverse()  # de la más vieja a la más nueva
        sessions, open_start = [], {}
        for who, ts, subj in rows:
            if subj.endswith("empieza a jugar"):
                open_start[who] = ts
            elif subj.startswith("Partida de ") or "no llegó a jugar" in subj:
                start = open_start.pop(who, None)
                if "no llegó a jugar" in subj:
                    continue
                minutes = round((ts - start) / 60) if start and 0 < ts - start < 24 * 3600 else None
                sessions.append({"who": who, "start": start or ts, "end": ts, "minutes": minutes})
        sessions.reverse()
        return sessions

    # — sync —
    def pull(self):
        if self.remote_has_commits():
            self.g("pull", "--rebase", "--autostash")

    def commit_all(self, msg):
        self.g("add", "-A")
        if self.g("status", "--porcelain"):
            self.g("commit", "-m", msg)
            return True
        return False

    def push(self):
        if not self.has_commits():
            return
        try:
            self.g("push", "-u", "origin", "HEAD")
        except GitError:
            self.pull()
            self.g("push", "-u", "origin", "HEAD")

    # — lock —
    def lock_owner(self):
        f = self.folder / LOCK_FILE
        return f.read_text(encoding="utf-8").strip() if f.exists() else None

    def take_lock(self):
        (self.folder / LOCK_FILE).write_text(self.player, encoding="utf-8")
        self.commit_all(f"{self.player} empieza a jugar")
        self.push()

    def release_lock(self, msg=None):
        f = self.folder / LOCK_FILE
        if f.exists():
            f.unlink()
        stamp = datetime.datetime.now().strftime("%d/%m %H:%M")
        self.commit_all(msg or f"Partida de {self.player} · {stamp}")
        self.push()

    def last_session(self):
        if not self.has_commits():
            return None
        out = self.g("log", "-1", "--format=%an|%cd", "--date=format:%d/%m %H:%M", check=False)
        if "|" in out:
            who, when = out.split("|", 1)
            return who, when
        return None


# ───────────────────────── Juego ─────────────────────────
def steam_exe():
    if IS_WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                path = winreg.QueryValueEx(k, "SteamExe")[0]
                if path and Path(path).exists():
                    return path
        except OSError:
            pass
        for p in (r"C:\Program Files (x86)\Steam\steam.exe", r"C:\Program Files\Steam\steam.exe"):
            if Path(p).exists():
                return p
    return shutil.which("steam")


def game_running():
    if not IS_WIN:
        return False
    out = subprocess.run(["tasklist", "/NH"], capture_output=True, text=True,
                         errors="replace", creationflags=NO_WINDOW).stdout.lower()
    return any(exe.lower() in out for exe in GAME_EXES)


def launch_game(folder: Path):
    exe = steam_exe()
    if not exe:
        raise RuntimeError("No encontré Steam. ¿Está instalado?")
    # -cachedir hace que el juego use la carpeta compartida: no hay que tocar Steam.
    subprocess.Popen([exe, "-applaunch", STEAM_APP_ID, f"-cachedir={folder}"],
                     creationflags=NO_WINDOW)


def steam_workshop_dirs():
    """Carpetas donde Steam guarda los mods del Workshop de PZ (una por biblioteca)."""
    exe = steam_exe()
    if not exe:
        return []
    steam = Path(exe).parent
    libs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        text = vdf.read_text(encoding="utf-8", errors="replace")
        libs += [Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', text)]
    return [d for d in (lib / "steamapps" / "workshop" / "content" / STEAM_APP_ID for lib in libs) if d.exists()]


def read_mod_info(path: Path):
    """(id, nombre) de un mod.info, o None."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    mid = re.search(r"^\s*id\s*=\s*(.+?)\s*$", text, re.M)
    name = re.search(r"^\s*name\s*=\s*(.+?)\s*$", text, re.M)
    if not mid:
        return None
    return mid.group(1), (name.group(1) if name else mid.group(1))


def scan_mods(mods_dir: Path):
    """{id_mod: nombre} de una carpeta 'mods' (mod.info puede estar en subcarpetas de version)."""
    found = {}
    for pattern in ("*/mod.info", "*/*/mod.info"):
        for info in mods_dir.glob(pattern):
            res = read_mod_info(info)
            if res:
                found[res[0]] = res[1]
    return found


def mod_report(folder: Path):
    """Cruza Mods= y WorkshopItems= de la partida compartida con lo instalado en este PC."""
    active, wanted_ws = [], []  # active: (id_mod, id_workshop_o_None)
    for ini in sorted((Path(folder) / "Server").glob("*.ini")):
        for line in ini.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, val = line.partition("=")
            items = [i.strip() for i in val.split(";") if i.strip()]
            if key == "Mods":
                for raw in items:
                    wid, sep, mid = raw.replace("\\", "/").rpartition("/")
                    active.append((mid, wid or None))
            elif key == "WorkshopItems":
                wanted_ws += [i for i in items if i.isdigit()]
    wanted_ws = list(dict.fromkeys(wanted_ws))

    names = {}                                        # id_mod -> nombre
    ws_mods = {}                                      # id_workshop -> {id_mod}
    for d in steam_workshop_dirs():
        for item in d.iterdir():
            found = scan_mods(item / "mods")
            ws_mods.setdefault(item.name, set()).update(found)
            names.update(found)
    local = {}
    for d in (Path(folder) / "mods", ZOMBOID_HOME / "mods"):
        local.update(scan_mods(d))
    names.update(local)
    installed_ids = set(names)

    active_ids = {m for m, _ in active}
    missing_ws = [w for w in wanted_ws if w not in ws_mods]
    ws_of = {m: w for w, ms in ws_mods.items() for m in ms}
    rep = {
        "active": len(active),
        "wanted_ws": len(wanted_ws),
        "wanted_ids": wanted_ws,
        "names": names,
        "missing_ws": missing_ws,
        # activos que no estan en este PC y que no se explican por un item de Workshop faltante
        "missing_mods": [m for m, w in active if m not in installed_ids and w not in missing_ws],
        # item de Workshop descargado (y pedido) pero ninguno de sus mods esta activado
        "unused_ws": [w for w in wanted_ws if w in ws_mods and ws_mods[w] and not (ws_mods[w] & active_ids)],
        # mod activo que viene de un item de Workshop que no esta en WorkshopItems: los demas no lo bajaran
        "unlisted": [m for m, _ in active if m in ws_of and ws_of[m] not in wanted_ws and m not in local],
        "unlisted_ws": sorted({ws_of[m] for m, _ in active
                               if m in ws_of and ws_of[m] not in wanted_ws and m not in local}),
        "ws_names": {w: next(iter(sorted(names[m] for m in ms if m in names)), w)
                     for w, ms in ws_mods.items() if ms},
    }
    # Sin el mapa mod->item no se sabe de cual item viene cada mod faltante: si ya faltan items de
    # Workshop, esos mods se explican por ellos y no se cuentan aparte.
    rep["lacking"] = len(missing_ws) + (0 if missing_ws else len(rep["missing_mods"]))
    rep["problems"] = rep["lacking"] + len(rep["unlisted"]) + len(rep["unused_ws"])
    return rep


def add_workshop_items(folder: Path, ids):
    """Agrega IDs de Workshop a WorkshopItems= de los .ini de la partida compartida. Devuelve cuantos."""
    added = 0
    for ini in sorted((Path(folder) / "Server").glob("*.ini")):
        with open(ini, encoding="utf-8", newline="") as f:
            lines = f.read().splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.startswith("WorkshopItems="):
                body = line.rstrip("\r\n")
                have = [x for x in body.split("=", 1)[1].split(";") if x.strip()]
                new = [w for w in ids if w not in have]
                lines[i] = "WorkshopItems=" + ";".join(have + new) + line[len(body):]
                added += len(new)
                break
        else:
            continue
        with open(ini, "w", encoding="utf-8", newline="") as f:
            f.writelines(lines)
    return added


def open_workshop(wid):
    """Abre la pagina del mod en el cliente de Steam; sin Steam, en el navegador."""
    if steam_exe():
        webbrowser.open(f"steam://url/CommunityFilePage/{wid}")
    else:
        webbrowser.open(f"https://steamcommunity.com/sharedfiles/filedetails/?id={wid}")


WS_API = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
WS_CACHE = CONFIG_DIR / "cache"


def clean_bbcode(text):
    """Descripción del Workshop sin BBCode ni links de imágenes, lista para mostrar."""
    text = re.sub(r"\[img\].*?\[/img\]", "", text or "", flags=re.I | re.S)
    text = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", text, flags=re.I | re.S)
    text = re.sub(r"\[\*\]", "• ", text)
    text = re.sub(r"\[/?[a-z0-9]+(=[^\]]*)?\]", "", text, flags=re.I)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def load_ws_cache():
    try:
        return json.loads((WS_CACHE / "workshop.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def fetch_workshop_details(ids):
    """Título, descripción, imagen y datos de cada item, desde la API pública de Steam (sin clave)."""
    ids = [i for i in ids if str(i).isdigit()]
    if not ids:
        return {}
    data = {"itemcount": len(ids)}
    for i, wid in enumerate(ids):
        data[f"publishedfileids[{i}]"] = wid
    req = urllib.request.Request(WS_API, data=urllib.parse.urlencode(data).encode(),
                                 headers={"User-Agent": "PZShare"})
    j = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace"))
    out = {}
    for d in j.get("response", {}).get("publishedfiledetails", []):
        if d.get("result") != 1:
            continue
        out[d["publishedfileid"]] = {
            "title": d.get("title") or d["publishedfileid"],
            "desc": clean_bbcode(d.get("description", "")),
            "preview": d.get("preview_url", ""),
            "updated": d.get("time_updated"),
            "subs": d.get("lifetime_subscriptions") or d.get("subscriptions") or 0,
            "tags": [t.get("tag", "") for t in d.get("tags", [])],
        }
    cache = load_ws_cache()
    cache.update(out)
    WS_CACHE.mkdir(parents=True, exist_ok=True)
    (WS_CACHE / "workshop.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return out


def fetch_thumb(wid, url, size=72):
    """Miniatura del mod (PIL.Image) con caché en disco. Devuelve None si no se puede."""
    try:
        from PIL import Image
    except ImportError:
        return None
    f = WS_CACHE / f"{wid}.img"
    try:
        if not f.exists():
            if not url:
                return None
            WS_CACHE.mkdir(parents=True, exist_ok=True)
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "PZShare"}),
                                         timeout=20).read()
            f.write_bytes(raw)
        img = Image.open(io.BytesIO(f.read_bytes())).convert("RGB")
        w, h = img.size
        side = min(w, h)
        img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
        return img.resize((size, size))
    except Exception:  # noqa: BLE001
        return None


def fmt_subs(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} M"
    if n >= 1000:
        return f"{n / 1000:.0f} k"
    return str(n)


def collection_id(text):
    """Saca el ID de un link de colección de Steam (o acepta el número solo)."""
    m = re.search(r"(?:id=)?(\d{6,})", text or "")
    return m.group(1) if m else None


def open_steam_friends():
    webbrowser.open("steam://open/friends")


def fmt_minutes(m):
    if m is None:
        return "—"
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60:02d} min"


def fmt_when(ts):
    d = datetime.datetime.fromtimestamp(ts)
    today = datetime.date.today()
    if d.date() == today:
        return "hoy " + d.strftime("%H:%M")
    if d.date() == today - datetime.timedelta(days=1):
        return "ayer " + d.strftime("%H:%M")
    return d.strftime("%d/%m %H:%M")


def search_workshop(text):
    """Busqueda en el Workshop de PZ, para mods de los que no se conoce el ID."""
    webbrowser.open("https://steamcommunity.com/workshop/browse/?appid=" + STEAM_APP_ID
                    + "&browsesort=textsearch&searchtext=" + urllib.parse.quote(text))


def find_local_servers():
    srv = ZOMBOID_HOME / "Server"
    if not srv.exists():
        return []
    return sorted({p.stem for p in srv.glob("*.ini")})


def import_server(name: str, dest: Path):
    """Copia una partida hosteada existente (~/Zomboid) a la carpeta compartida."""
    copied = 0
    for sub, patterns in (("Server", [f"{name}.ini", f"{name}_*"]),
                          ("Saves/Multiplayer", [name, f"{name}_*"]),
                          ("db", [f"{name}.db"])):
        src_dir = ZOMBOID_HOME / sub
        if not src_dir.exists():
            continue
        for pat in patterns:
            for src in src_dir.glob(pat):
                target = dest / sub / src.name
                target.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, target)
                copied += 1
    return copied


# ───────────────────────── Mapa en vivo ─────────────────────────
LIVEMAP_MOD = "PZLiveMap"
LIVEMAP_FILES = ("pzlivemap.json", "pzlivemap_client.json")  # los escribe el mod en <carpeta>/Lua/


def resource(*parts):
    """Ruta a archivos que vienen con la app (también dentro del .exe de PyInstaller)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base.joinpath(*parts)


def livemap_installed(folder: Path):
    return (Path(folder) / "mods" / LIVEMAP_MOD / "42" / "mod.info").exists()


def install_livemap(folder: Path):
    """Copia el mod a <carpeta>/mods (viaja con la partida) y lo activa en Mods= del servidor."""
    src = resource("mod", LIVEMAP_MOD)
    if not src.exists():
        raise RuntimeError("No encontré el mod PZLiveMap junto a la app (carpeta mod/).")
    dest = Path(folder) / "mods" / LIVEMAP_MOD
    shutil.copytree(src, dest, dirs_exist_ok=True)
    activated = 0
    for ini in sorted((Path(folder) / "Server").glob("*.ini")):
        with open(ini, encoding="utf-8", newline="") as f:
            lines = f.read().splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.startswith("Mods="):
                body = line.rstrip("\r\n")
                mods = [m for m in body[5:].split(";") if m.strip()]
                if not any(m.strip().split("\\")[-1] == LIVEMAP_MOD for m in mods):
                    lines[i] = "Mods=" + ";".join(mods + [LIVEMAP_MOD]) + line[len(body):]
                    activated += 1
                break
        with open(ini, "w", encoding="utf-8", newline="") as f:
            f.writelines(lines)
    return activated


LOCAL_MAP_PORT = 8765


class LocalMap:
    """Mapa en vivo dentro de PZ Share: un servidor web mínimo en este PC (sin Pi, sin Docker).
    Solo corre si lo activas y casi no gasta nada: un hilo dormido y el estado en memoria."""
    ONLINE_SECS, TRAIL_MAX, TRAIL_STEP = 25, 600, 4.0

    def __init__(self):
        self.state = {"world": {}, "players": {}, "events": [], "updated": 0}
        self.cond = threading.Condition()
        self.version = 0
        self.httpd = None
        self.tiles_dir = None  # carpeta base_top renderizada con pzmap2dzi (opcional)

    @staticmethod
    def find_dzi(folder):
        """Acepta la carpeta de salida de pzmap2dzi, su html/, map_data/ o base_top/ directamente."""
        if not folder:
            return None
        base = Path(folder)
        for cand in (base, base / "base_top", base / "map_data" / "base_top", base / "html" / "map_data" / "base_top"):
            if (cand / "layer0.dzi").exists() and (cand / "map_info.json").exists():
                return cand
        return None

    @property
    def running(self):
        return self.httpd is not None

    @property
    def url(self):
        return f"http://127.0.0.1:{LOCAL_MAP_PORT}/"

    # — estado (misma lógica que el servidor de la Pi) —
    def event(self, text, kind="info"):
        self.state["events"].insert(0, {"t": time.time(), "text": text, "kind": kind})
        del self.state["events"][60:]

    def update(self, body):
        now = time.time()
        st = self.state
        w = body.get("world") or {}
        st["world"] = {"day": w.get("day"), "hour": w.get("hour"), "minute": w.get("minute"),
                       "host": body.get("host", "")}
        for raw in (body.get("players") or [])[:32]:
            name = str(raw.get("name", "")).strip()[:40]
            if not name:
                continue
            pl = st["players"].get(name)
            if pl is None or now - pl.get("seen", 0) > self.ONLINE_SECS:
                self.event(f"{name} se conectó", "join")
            pl = st["players"].setdefault(name, {"trail": []})
            if raw.get("dead") and not pl.get("dead"):
                self.event(f"💀 {name} murió", "death")
            if raw.get("vehicle") and not pl.get("vehicle"):
                self.event(f"🚗 {name} se subió a un vehículo")
            x, y = float(raw.get("x") or 0), float(raw.get("y") or 0)
            tr = pl["trail"]
            if not tr or abs(tr[-1][0] - x) + abs(tr[-1][1] - y) >= self.TRAIL_STEP:
                tr.append([round(x, 1), round(y, 1), int(now)])
                del tr[:-self.TRAIL_MAX]
            pl.update(x=x, y=y, z=int(raw.get("z") or 0), hp=float(raw.get("hp") or 100),
                      dead=bool(raw.get("dead")), vehicle=raw.get("vehicle"), seen=now)
        st["updated"] = now
        self.bump()

    def bump(self):
        with self.cond:
            self.version += 1
            self.cond.notify_all()

    def public(self):
        now = time.time()
        players = [{**{k: v for k, v in p.items()}, "name": n, "online": now - p.get("seen", 0) < self.ONLINE_SECS}
                   for n, p in self.state["players"].items()]
        players.sort(key=lambda p: (not p["online"], p["name"].lower()))
        return {"world": self.state["world"], "players": players, "events": self.state["events"][:30],
                "updated": self.state["updated"], "now": now}

    # — servidor —
    def start(self):
        if self.httpd:
            return
        local = self
        static = resource("livemap", "static")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silencio
                pass

            def send(self, code, body, ctype="application/json; charset=utf-8"):
                data = body if isinstance(body, bytes) else body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = urllib.parse.urlparse(self.path).path
                if path in ("/", "/index.html"):
                    return self.send(200, (static / "index.html").read_bytes(), "text/html; charset=utf-8")
                if path == "/towns.json":
                    return self.send(200, (static / "towns.json").read_bytes())
                if path == "/config.json":
                    return self.send(200, json.dumps({"dzi": "tiles/" if local.tiles_dir else "", "tiles": "",
                                                      "image": False, "image_bounds": None,
                                                      "title": "Mapa en vivo · este PC", "need_key": False}))
                if path.startswith("/tiles/") and local.tiles_dir:
                    rel = urllib.parse.unquote(path[len("/tiles/"):])
                    f = (local.tiles_dir / rel).resolve()
                    if local.tiles_dir.resolve() in f.parents and f.is_file():
                        ctype = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
                                 ".json": "application/json", ".dzi": "application/xml"}.get(f.suffix, "application/octet-stream")
                        return self.send(200, f.read_bytes(), ctype)
                    return self.send(404, "{}")
                if path == "/api/state":
                    return self.send(200, json.dumps(local.public(), ensure_ascii=False))
                if path == "/api/stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    seen = -1
                    try:
                        while local.httpd:
                            with local.cond:
                                if local.version == seen:
                                    local.cond.wait(timeout=15)
                                changed = local.version != seen
                                seen = local.version
                            msg = f"data: {json.dumps(local.public(), ensure_ascii=False)}\n\n" if changed else ": ping\n\n"
                            self.wfile.write(msg.encode("utf-8"))
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                    return
                self.send(404, "{}")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", LOCAL_MAP_PORT), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self.httpd:
            h, self.httpd = self.httpd, None
            self.bump()
            threading.Thread(target=h.shutdown, daemon=True).start()


class LiveRelay(threading.Thread):
    """Mientras el juego está abierto, manda al mapa en vivo lo que escribe el mod."""

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.status = "apagado"
        self.sent = 0
        self.last_ok = 0
        self.last_mtime = 0

    def config(self):
        try:
            repo = self.app.repo
            return repo, repo.group().get("livemap") or {}
        except Exception:  # noqa: BLE001
            return None, {}

    def pick_file(self, folder: Path):
        best = None
        for name in LIVEMAP_FILES:  # el del servidor primero: trae a todos los jugadores
            f = Path(folder) / "Lua" / name
            try:
                m = f.stat().st_mtime
            except OSError:
                continue
            if time.time() - m < 30 and (best is None or name == LIVEMAP_FILES[0] or m > best[1] + 10):
                best = (f, m)
                if name == LIVEMAP_FILES[0]:
                    break
        return best

    def run(self):
        while True:
            time.sleep(3)
            repo, cfg = self.config()
            local = self.app.localmap
            to_pi = bool(cfg.get("url") and cfg.get("token"))
            if not repo or (not to_pi and not local.running):
                self.status = "apagado"
                continue
            if not game_running():
                self.status = "esperando a que abras el juego"
                continue
            found = self.pick_file(repo.folder)
            if not found:
                self.status = "juego abierto, esperando datos del mod"
                continue
            f, mtime = found
            if mtime == self.last_mtime:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                continue  # el juego lo está escribiendo justo ahora; se lee en la próxima vuelta
            data["host"] = repo.player
            self.last_mtime = mtime
            n = len(data.get("players", []))
            if local.running:
                local.update(data)
                self.last_ok = time.time()
                self.status = f"en vivo en este PC · {n} jugadores"
            if not to_pi:
                continue
            try:
                req = urllib.request.Request(
                    cfg["url"].rstrip("/") + "/api/update", data=json.dumps(data).encode("utf-8"),
                    headers={"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json",
                             "User-Agent": "PZShare"})
                urllib.request.urlopen(req, timeout=8).read()
                self.sent += 1
                self.last_ok = time.time()
                self.status = f"en vivo{' (PC y Pi)' if local.running else ''} · {n} jugadores"
            except urllib.error.HTTPError as e:
                self.status = "token inválido" if e.code == 401 else f"error {e.code} del mapa"
            except Exception as e:  # noqa: BLE001
                self.status = f"sin conexión con la Pi ({type(e).__name__})"


# ───────────────────────── UI helpers ─────────────────────────
def font(size=13, weight="normal"):
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


def card(parent, **kw):
    return ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=14,
                        border_width=1, border_color=C["border"], **kw)


def ghost_button(parent, text, cmd, **kw):
    return ctk.CTkButton(parent, text=text, command=cmd, height=38, corner_radius=10,
                         fg_color=C["card2"], hover_color=C["border"], text_color=C["text"],
                         font=font(13), **kw)


# ───────────────────────── App ─────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title(APP_NAME)
        self.geometry("460x860")
        self.minsize(420, 760)
        self.configure(fg_color=C["bg"])
        self.cfg = load_config()
        self.busy = False
        self.playing = False
        self.view = None
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.localmap = LocalMap()
        self.localmap.tiles_dir = LocalMap.find_dzi(self.cfg.get("map_tiles"))
        if self.cfg.get("localmap"):
            try:
                self.localmap.start()
            except OSError:
                pass
        self.relay = LiveRelay(self)
        self.relay.start()
        if self.cfg.get("folder") and (Path(self.cfg["folder"]) / ".git").exists():
            self.show_main()
        else:
            self.show_setup(first=True)

    # — navegación —
    def swap(self, view):
        if self.view:
            self.view.destroy()
        self.view = view
        view.pack(fill="both", expand=True, padx=22, pady=20)

    def show_setup(self, first=False):
        self.swap(SetupView(self, first))

    def show_main(self):
        self.swap(MainView(self))

    @property
    def repo(self):
        return Repo(Path(self.cfg["folder"]), self.cfg.get("player") or getpass.getuser())

    # — tareas en segundo plano —
    def bg(self, fn, done=None, fail=None):
        def worker():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001
                err = e
                self.after(0, lambda: (fail or self.error)(err))
                return
            if done:
                self.after(0, lambda: done(res))
        threading.Thread(target=worker, daemon=True).start()

    def error(self, err):
        messagebox.showerror(APP_NAME, friendly(err), parent=self)

    def on_close(self):
        if self.playing:
            if not messagebox.askyesno(
                APP_NAME, "Estás jugando. Si cierras PZ Share, la partida NO se subirá sola.\n\n¿Cerrar igual?",
                icon="warning", parent=self):
                return
        self.destroy()


# ───────────────────────── Setup ─────────────────────────
class SetupView(ctk.CTkFrame):
    def __init__(self, app: App, first: bool):
        super().__init__(app, fg_color="transparent")
        self.app = app
        cfg = app.cfg

        ctk.CTkLabel(self, text="Bienvenido" if first else "Ajustes", font=font(24, "bold"),
                     text_color=C["text"]).pack(anchor="w")
        ctk.CTkLabel(self, text="Configura esto una sola vez y listo." if first else "Cambia lo que necesites.",
                     font=font(13), text_color=C["muted"]).pack(anchor="w", pady=(2, 16))

        # Git
        gcard = card(self)
        gcard.pack(fill="x", pady=(0, 12))
        self.git_lbl = ctk.CTkLabel(gcard, font=font(13), anchor="w")
        self.git_lbl.pack(side="left", padx=14, pady=12)
        self.git_btn = ctk.CTkButton(gcard, text="Descargar Git", width=120, height=30, corner_radius=8,
                                     fg_color=C["warn"], hover_color="#C99A2E", text_color="#1A1A1A",
                                     command=lambda: webbrowser.open(GIT_DOWNLOAD))
        self.refresh_git()

        form = card(self)
        form.pack(fill="x")
        self.player = self.field(form, "Tu nombre", "Así te verán tus amigos",
                                 cfg.get("player") or getpass.getuser())
        self.url = self.field(form, "Repo de GitHub", "https://github.com/usuario/pz-save",
                              cfg.get("repo", ""))

        ctk.CTkLabel(form, text="Carpeta de la partida", font=font(12, "bold"),
                     text_color=C["muted"]).pack(anchor="w", padx=14, pady=(10, 4))
        row = ctk.CTkFrame(form, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 6))
        self.folder = ctk.CTkEntry(row, height=38, corner_radius=8, font=font(13),
                                   placeholder_text=r"D:\pz-save", border_color=C["border"])
        self.folder.pack(side="left", fill="x", expand=True)
        if cfg.get("folder"):
            self.folder.insert(0, cfg["folder"])
        ghost_button(row, "Elegir…", self.pick_folder, width=86).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(form, text="Usa una carpeta vacía (se crea si no existe) o una ya conectada.",
                     font=font(11), text_color=C["muted"]).pack(anchor="w", padx=14, pady=(0, 14))

        self.status = ctk.CTkLabel(self, text="", font=font(12), text_color=C["muted"], wraplength=400)
        self.status.pack(fill="x", pady=(14, 6))

        self.save_btn = ctk.CTkButton(self, text="Conectar", height=48, corner_radius=12,
                                      font=font(15, "bold"), fg_color=C["accent"],
                                      hover_color=C["accent_h"], text_color="#0B0F0C",
                                      command=self.save)
        self.save_btn.pack(fill="x")
        if not first:
            ghost_button(self, "Volver", app.show_main).pack(fill="x", pady=(8, 0))

    def field(self, parent, label, placeholder, value):
        ctk.CTkLabel(parent, text=label, font=font(12, "bold"),
                     text_color=C["muted"]).pack(anchor="w", padx=14, pady=(12, 4))
        e = ctk.CTkEntry(parent, height=38, corner_radius=8, font=font(13),
                         placeholder_text=placeholder, border_color=C["border"])
        e.pack(fill="x", padx=14)
        if value:
            e.insert(0, value)
        return e

    def refresh_git(self):
        if git_installed():
            self.git_lbl.configure(text="✓  Git instalado", text_color=C["accent"])
            self.git_btn.pack_forget()
        else:
            self.git_lbl.configure(text="⚠  Falta Git (instálalo y vuelve a abrir)", text_color=C["warn"])
            self.git_btn.pack(side="right", padx=12)

    def pick_folder(self):
        d = filedialog.askdirectory(parent=self, title="Elige la carpeta de la partida")
        if d:
            self.folder.delete(0, "end")
            self.folder.insert(0, str(Path(d)))

    def save(self):
        player = self.player.get().strip()
        url = self.url.get().strip()
        folder = self.folder.get().strip()
        if not git_installed():
            return self.say("Primero instala Git.", "warn")
        if not player or not folder:
            return self.say("Completa tu nombre y la carpeta.", "warn")
        folder = Path(folder)
        if not (folder / ".git").exists() and not url:
            return self.say("Pega el link del repo de GitHub.", "warn")

        self.save_btn.configure(state="disabled", text="Conectando…")
        self.say("Conectando con GitHub… si se abre el navegador, inicia sesión.")

        def work():
            if not (folder / ".git").exists():
                if folder.exists() and any(folder.iterdir()):
                    # Carpeta con una partida ya creada: se conecta y se sube tal cual
                    # (solo si el repo de GitHub está vacío, para no mezclar partidas).
                    if git(["ls-remote", "--heads", url], folder):
                        raise GitError("Esta carpeta ya tiene archivos y el repo de GitHub también. "
                                       "Elige una carpeta vacía o nueva para descargar la partida del grupo.")
                    git(["init"], folder)
                    git(["checkout", "-b", "main"], folder, check=False)
                    git(["remote", "add", "origin", url], folder)
                else:
                    folder.parent.mkdir(parents=True, exist_ok=True)
                    git(["clone", url, str(folder)], folder.parent)
            repo = Repo(folder, player)
            repo.ensure_identity()
            repo.ensure_gitignore()
            if repo.commit_all("Partida inicial"):
                repo.push()
            return True

        def done(_):
            self.app.cfg.update(player=player, repo=url, folder=str(folder))
            save_config(self.app.cfg)
            self.app.show_main()

        def fail(err):
            self.save_btn.configure(state="normal", text="Conectar")
            self.say(friendly(err), "danger")

        self.app.bg(work, done, fail)

    def say(self, text, tone="muted"):
        self.status.configure(text=text, text_color=C[tone])


# ───────────────────────── Principal ─────────────────────────
class MainView(ctk.CTkFrame):
    def __init__(self, app: App):
        super().__init__(app, fg_color="transparent")
        self.app = app

        # Header
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x")
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(titles, text="PZ Share", font=font(24, "bold"), text_color=C["text"]).pack(anchor="w")
        ctk.CTkLabel(titles, text=f"Hola, {app.repo.player}", font=font(13),
                     text_color=C["muted"]).pack(anchor="w")
        ctk.CTkButton(head, text="⚙", width=40, height=40, corner_radius=10, font=font(18),
                      fg_color=C["card2"], hover_color=C["border"], text_color=C["text"],
                      command=lambda: app.show_setup(False)).pack(side="right")

        # Estado
        st = card(self)
        st.pack(fill="x", pady=(18, 14))
        inner = ctk.CTkFrame(st, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=18)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        self.dot = ctk.CTkLabel(top, text="●", font=font(18), text_color=C["muted"])
        self.dot.pack(side="left")
        self.state_lbl = ctk.CTkLabel(top, text="Revisando…", font=font(18, "bold"), text_color=C["text"])
        self.state_lbl.pack(side="left", padx=(8, 0))
        self.sub_lbl = ctk.CTkLabel(inner, text="", font=font(12), text_color=C["muted"],
                                    anchor="w", justify="left")
        self.sub_lbl.pack(fill="x", pady=(6, 0))
        self.mods_lbl = ctk.CTkLabel(inner, text="", font=font(12), text_color=C["muted"],
                                     anchor="w", justify="left", wraplength=360)
        self.mods_lbl.pack(fill="x", pady=(4, 0))
        self.join_btn = ctk.CTkButton(inner, text="Unirse", height=36, corner_radius=10,
                                      font=font(13, "bold"), fg_color=C["card2"], hover_color=C["border"],
                                      border_width=1, border_color=C["accent"], text_color=C["accent"],
                                      command=self.join)
        self.host_owner = None
        self.report = None

        # Botón principal
        self.play_btn = ctk.CTkButton(self, text="▶   Jugar", height=60, corner_radius=14,
                                      font=font(18, "bold"), fg_color=C["accent"],
                                      hover_color=C["accent_h"], text_color="#0B0F0C",
                                      command=self.play)
        self.play_btn.pack(fill="x")

        self.progress = ctk.CTkProgressBar(self, mode="indeterminate", height=4,
                                           progress_color=C["accent"], fg_color=C["card2"])
        self.step_lbl = ctk.CTkLabel(self, text="", font=font(12), text_color=C["muted"])
        self.step_lbl.pack(pady=(10, 0))

        # Secundarios
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))
        row.grid_columnconfigure((0, 1), weight=1)
        self.sync_btn = ghost_button(row, "⟳  Sincronizar", self.sync)
        self.sync_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ghost_button(row, "Abrir carpeta", self.open_folder).grid(row=0, column=1, sticky="ew", padx=(5, 0))
        self.upload_btn = ghost_button(row, "⤒  Subir partida", self.upload)
        self.upload_btn.grid(row=1, column=0, sticky="ew", padx=(0, 5), pady=(10, 0))
        self.import_btn = ghost_button(row, "⤓  Importar partida", self.import_dialog)
        self.import_btn.grid(row=1, column=1, sticky="ew", padx=(5, 0), pady=(10, 0))
        self.mods_btn = ghost_button(row, "Mods de la partida", self.show_mods)
        self.mods_btn.grid(row=2, column=0, sticky="ew", padx=(0, 5), pady=(10, 0))
        self.map_btn = ghost_button(row, "🗺  Mapa en vivo", self.show_livemap)
        self.map_btn.grid(row=2, column=1, sticky="ew", padx=(5, 0), pady=(10, 0))

        # Interruptor del mapa local
        sw = card(self)
        sw.pack(fill="x", pady=(12, 0))
        self.map_switch = ctk.CTkSwitch(sw, text="Mapa en vivo en este PC", font=font(13, "bold"),
                                        text_color=C["text"], progress_color=C["accent"],
                                        command=self.toggle_localmap)
        sw.grid_columnconfigure(0, weight=1)
        self.map_switch.grid(row=0, column=0, sticky="w", padx=14, pady=(10, 0))
        if app.localmap.running:
            self.map_switch.select()
        self.map_open = ctk.CTkButton(sw, text="Abrir", width=70, height=28, corner_radius=8, font=font(12),
                                      fg_color=C["card2"], hover_color=C["border"], text_color=C["accent"],
                                      command=lambda: webbrowser.open(app.localmap.url))
        self.map_state = ctk.CTkLabel(sw, text="", font=font(11), text_color=C["muted"], anchor="w")
        self.map_state.grid(row=1, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 8))
        self.refresh_map_switch()
        self.release_btn = ctk.CTkButton(row, text="Liberar partida (quedó marcada como mía)", height=34,
                                         corner_radius=10, fg_color="transparent", border_width=1,
                                         border_color=C["danger"], text_color=C["danger"],
                                         hover_color="#2A1716", font=font(12), command=self.release)

        # Historial
        hist = card(self)
        hist.pack(fill="x", pady=(14, 0))
        hhead = ctk.CTkFrame(hist, fg_color="transparent")
        hhead.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(hhead, text="Últimas sesiones", font=font(13, "bold"),
                     text_color=C["text"]).pack(side="left")
        ctk.CTkButton(hhead, text="Ver todo", width=70, height=26, corner_radius=8, font=font(12),
                      fg_color="transparent", hover_color=C["card2"], text_color=C["accent"],
                      command=self.show_history).pack(side="right")
        self.hist_box = ctk.CTkFrame(hist, fg_color="transparent")
        self.hist_box.pack(fill="x", padx=16, pady=(0, 12))
        self.sessions = []

        self.sync()

    # — estado visual —
    def set_busy(self, busy, text=""):
        self.app.busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.play_btn, self.sync_btn, self.upload_btn, self.import_btn):
            b.configure(state=state)
        self.step_lbl.configure(text=text)
        if busy:
            self.progress.pack(fill="x", pady=(10, 0), before=self.step_lbl)
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def step(self, text):
        self.app.after(0, lambda: self.step_lbl.configure(text=text))

    def render(self, info):
        owner, last = info
        me = self.app.repo.player
        if owner and owner.lower() == me.lower():
            self.dot.configure(text_color=C["warn"])
            self.state_lbl.configure(text="La partida está marcada como tuya")
            self.release_btn.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        elif owner:
            self.dot.configure(text_color=C["danger"])
            self.state_lbl.configure(text=f"{owner} está jugando")
            self.release_btn.grid_forget()
        else:
            self.dot.configure(text_color=C["accent"])
            self.state_lbl.configure(text="Libre para jugar")
            self.release_btn.grid_forget()
        self.host_owner = owner if owner and owner.lower() != me.lower() else None
        if self.host_owner:
            self.join_btn.configure(text=f"🎮  Unirse a la partida de {self.host_owner}")
            self.join_btn.pack(fill="x", pady=(10, 0))
        else:
            self.join_btn.pack_forget()
        if last:
            self.sub_lbl.configure(text=f"Última vez: {last[0]} · {last[1]}")
        else:
            self.sub_lbl.configure(text="Aún no hay partidas guardadas. Usa Importar o juega la primera.")
        self.render_mods()
        self.render_history()

    def render_history(self):
        for w in self.hist_box.winfo_children():
            w.destroy()
        try:
            self.sessions = self.app.repo.history()
        except Exception:  # noqa: BLE001
            self.sessions = []
        if not self.sessions:
            ctk.CTkLabel(self.hist_box, text="Todavía no hay sesiones. ¡Juega la primera!",
                         font=font(12), text_color=C["muted"]).pack(anchor="w")
            return
        for ses in self.sessions[:3]:
            session_row(self.hist_box, ses)

    def show_livemap(self):
        LiveMapDialog(self)

    def toggle_localmap(self):
        lm = self.app.localmap
        if self.map_switch.get():
            try:
                lm.start()
            except OSError as e:
                self.map_switch.deselect()
                return messagebox.showerror(APP_NAME, f"No pude abrir el mapa en el puerto {LOCAL_MAP_PORT}: {e}",
                                            parent=self)
            if not livemap_installed(self.app.repo.folder):
                messagebox.showinfo(APP_NAME, "Falta el mod del mapa en la partida. Ve a «🗺 Mapa en vivo» → "
                                    "«Instalar el mod en la partida».", parent=self)
            webbrowser.open(lm.url)
        else:
            lm.stop()
        self.app.cfg["localmap"] = bool(self.map_switch.get())
        save_config(self.app.cfg)
        self.refresh_map_switch()

    def refresh_map_switch(self):
        if not self.winfo_exists():
            return
        lm, relay = self.app.localmap, self.app.relay
        if lm.running:
            self.map_open.grid(row=0, column=1, padx=(0, 12), pady=(10, 0))
            self.map_state.configure(text=relay.status)
        else:
            self.map_open.grid_forget()
            self.map_state.configure(text="apagado · 0 recursos")
        self.after(3000, self.refresh_map_switch)

    def show_history(self):
        self.render_history()
        HistoryDialog(self, self.sessions)

    def join(self):
        owner = self.host_owner
        if not owner:
            return
        if not game_running():
            try:
                launch_game(self.app.repo.folder)
            except Exception as e:  # noqa: BLE001
                return self.app.error(e)
        open_steam_friends()
        messagebox.showinfo(
            APP_NAME,
            f"Abrí el juego y tu lista de amigos de Steam.\n\n"
            f"Cuando el juego llegue al menú: clic derecho en {owner} → «Unirse a la partida».\n\n"
            "No hagas Host: la partida la tiene él.", parent=self)

    def render_mods(self):
        try:
            self.report = mod_report(self.app.repo.folder)
        except OSError:
            self.report = None
        r = self.report
        if not r or not r["active"]:
            self.mods_lbl.configure(text="Mods: la partida no tiene mods activos.", text_color=C["muted"])
        elif r["problems"]:
            self.mods_lbl.configure(text=f"⚠ Mods: {r['problems']} cosas por revisar. "
                                         "Pulsa «Mods de la partida».", text_color=C["warn"])
        else:
            self.mods_lbl.configure(text=f"✓ Mods: {r['active']} activos y todos instalados en tu PC.",
                                    text_color=C["accent"])

    def show_mods(self):
        self.render_mods()
        if not self.report:
            return messagebox.showinfo(APP_NAME, "No encontré la configuración de la partida todavía. "
                                       "Sincroniza o importa una partida primero.", parent=self)
        ModsDialog(self, self.report)

    # — acciones —
    def sync(self):
        if self.app.busy:
            return
        self.set_busy(True, "Descargando la última partida…")
        repo = self.app.repo

        def work():
            repo.pull()
            repo.ensure_gitignore()
            return repo.lock_owner(), repo.last_session()

        def done(info):
            self.set_busy(False, "Todo al día ✓")
            self.render(info)

        def fail(err):
            self.set_busy(False, "")
            self.app.error(err)

        self.app.bg(work, done, fail)

    def play(self):
        if self.app.busy:
            return
        if game_running():
            return messagebox.showinfo(APP_NAME, "Project Zomboid ya está abierto. Ciérralo y vuelve a intentar.",
                                       parent=self)
        repo = self.app.repo
        self.set_busy(True, "Descargando la última partida…")

        def check():
            repo.pull()
            return repo.lock_owner()

        def decide(owner):
            if owner and owner.lower() != repo.player.lower():
                ok = messagebox.askyesno(
                    APP_NAME,
                    f"{owner} tiene la partida abierta ahora.\n\nSi juegan a la vez, uno de los dos "
                    f"pierde el progreso. Mejor únete a su partida por Steam.\n\n¿Jugar de todas formas?",
                    icon="warning", parent=self)
                if not ok:
                    self.set_busy(False, "")
                    return self.render((owner, repo.last_session()))
            self.render_mods()
            r = self.report
            lacking = r["lacking"] if r else 0
            if lacking and not messagebox.askyesno(
                    APP_NAME,
                    f"Te faltan {lacking} mods de la partida. Si entras así, el juego dará "
                    "error o los desactivará.\n\nRevisa «Mods de la partida». ¿Jugar de todas formas?",
                    icon="warning", parent=self):
                self.set_busy(False, "")
                return self.render((owner, repo.last_session()))
            self.app.bg(session, finished, failed)

        def session():
            self.step("Avisando a tus amigos que vas a jugar…")
            repo.take_lock()
            self.step("Abriendo Project Zomboid…")
            launch_game(repo.folder)
            for _ in range(120):  # hasta ~6 min esperando que abra
                time.sleep(3)
                if game_running():
                    break
            else:
                repo.release_lock(f"{repo.player} no llegó a jugar")
                raise RuntimeError("El juego no abrió. Liberé la partida, intenta de nuevo.")
            self.app.playing = True
            self.app.after(0, self.render_playing)
            self.step("Jugando… al cerrar el juego se sube sola.")
            while game_running():
                time.sleep(5)
            self.app.playing = False
            self.step("Subiendo la partida…")
            repo.release_lock()
            return repo.lock_owner(), repo.last_session()

        def finished(info):
            self.set_busy(False, "Partida guardada para todos ✓")
            self.render(info)
            self.app.deiconify()
            self.app.lift()

        def failed(err):
            self.app.playing = False
            self.set_busy(False, "")
            self.app.error(err)
            self.sync()

        def check_fail(err):
            self.set_busy(False, "")
            self.app.error(err)

        self.app.bg(check, decide, check_fail)

    def render_playing(self):
        self.dot.configure(text_color=C["accent"])
        self.state_lbl.configure(text="Estás jugando")
        self.sub_lbl.configure(text="No cierres PZ Share. Puedes minimizarla.")
        self.release_btn.grid_forget()

    def upload(self):
        """Sube a mano lo que haya cambiado (por si se jugó sin abrir desde PZ Share)."""
        if self.app.busy:
            return
        if game_running():
            return messagebox.showinfo(APP_NAME, "Cierra el juego primero para no subir una partida a medias.",
                                       parent=self)
        repo = self.app.repo
        self.set_busy(True, "Subiendo la partida…")

        def work():
            stamp = datetime.datetime.now().strftime("%d/%m %H:%M")
            changed = repo.commit_all(f"Partida de {repo.player} · {stamp}")
            repo.push()
            return changed, (repo.lock_owner(), repo.last_session())

        def done(res):
            changed, info = res
            self.set_busy(False, "Partida subida ✓" if changed else "No había cambios para subir")
            self.render(info)

        def fail(err):
            self.set_busy(False, "")
            self.app.error(err)

        self.app.bg(work, done, fail)

    def release(self):
        if game_running():
            return messagebox.showinfo(APP_NAME, "Cierra el juego primero.", parent=self)
        repo = self.app.repo
        self.set_busy(True, "Liberando y subiendo la partida…")

        def done(info):
            self.set_busy(False, "Partida liberada ✓")
            self.render(info)

        def fail(err):
            self.set_busy(False, "")
            self.app.error(err)

        self.app.bg(lambda: (repo.release_lock(), (repo.lock_owner(), repo.last_session()))[1], done, fail)

    def open_folder(self):
        folder = self.app.cfg["folder"]
        if IS_WIN:
            os.startfile(folder)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", folder])

    def import_dialog(self):
        servers = find_local_servers()
        if not servers:
            return messagebox.showinfo(
                APP_NAME, f"No encontré partidas hosteadas en:\n{ZOMBOID_HOME / 'Server'}", parent=self)
        ImportDialog(self, servers)

    def do_import(self, name):
        repo = self.app.repo
        self.set_busy(True, f"Importando «{name}»…")

        def work():
            n = import_server(name, repo.folder)
            if not n:
                raise RuntimeError("No encontré archivos de esa partida.")
            self.step("Subiendo a GitHub (la primera vez puede tardar)…")
            repo.commit_all(f"Partida importada: {name}")
            repo.push()
            return repo.lock_owner(), repo.last_session()

        def done(info):
            self.set_busy(False, f"«{name}» importada ✓ — en el juego hagan Host con ese nombre")
            self.render(info)

        def fail(err):
            self.set_busy(False, "")
            self.app.error(err)

        self.app.bg(work, done, fail)


class ImportDialog(ctk.CTkToplevel):
    def __init__(self, main: MainView, servers):
        super().__init__(main.app)
        self.main = main
        self.title("Importar partida")
        self.geometry("380x250")
        self.resizable(False, False)
        self.configure(fg_color=C["bg"])
        self.transient(main.app)
        self.after(50, self.grab_set)

        ctk.CTkLabel(self, text="Importar partida existente", font=font(17, "bold"),
                     text_color=C["text"]).pack(anchor="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(self, text="Copia una partida que ya hosteaste en este PC\npara compartirla con el grupo.",
                     font=font(12), text_color=C["muted"], justify="left").pack(anchor="w", padx=20)
        self.choice = ctk.CTkOptionMenu(self, values=servers, height=38, corner_radius=8,
                                        fg_color=C["card2"], button_color=C["border"],
                                        button_hover_color=C["card"], font=font(13))
        self.choice.pack(fill="x", padx=20, pady=16)
        ctk.CTkButton(self, text="Importar", height=42, corner_radius=10, font=font(14, "bold"),
                      fg_color=C["accent"], hover_color=C["accent_h"], text_color="#0B0F0C",
                      command=self.ok).pack(fill="x", padx=20)

    def ok(self):
        name = self.choice.get()
        self.destroy()
        self.main.do_import(name)


class ModsDialog(ctk.CTkToplevel):
    FILTERS = ("Todos", "Me faltan", "Instalados")

    def __init__(self, main: MainView, rep):
        super().__init__(main.app)
        self.rep = rep
        self.main = main
        self.title("Mods de la partida")
        self.geometry("580x780")
        self.minsize(500, 560)
        self.configure(fg_color=C["bg"])
        self.transient(main.app)
        self.details = load_ws_cache()
        self.thumbs = {}       # wid -> CTkImage (hay que guardar la referencia)
        self.thumb_labels = {}  # wid -> label donde va la imagen
        self.expanded = set()
        self.missing = set(rep["missing_ws"])

        ctk.CTkLabel(self, text="Mods de la partida", font=font(18, "bold"),
                     text_color=C["text"]).pack(anchor="w", padx=20, pady=(18, 2))
        n_ok = len(rep["wanted_ids"]) - len(self.missing)
        ctk.CTkLabel(self, text=f"{rep['active']} mods activos · {len(rep['wanted_ids'])} items de Workshop · "
                                f"{n_ok} instalados en tu PC",
                     font=font(12), text_color=C["muted"]).pack(anchor="w", padx=20)

        # Acciones rápidas
        acts = ctk.CTkFrame(self, fg_color="transparent")
        acts.pack(fill="x", padx=20, pady=(12, 0))
        acts.grid_columnconfigure((0, 1), weight=1)
        if self.missing:
            ctk.CTkButton(acts, text=f"Suscribirme a los {len(self.missing)} que me faltan", height=40,
                          corner_radius=10, font=font(13, "bold"), fg_color=C["accent"],
                          hover_color=C["accent_h"], text_color="#0B0F0C",
                          command=self.open_steam).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        else:
            ctk.CTkLabel(acts, text="✓ Tienes todos los mods", font=font(13, "bold"),
                         text_color=C["accent"]).grid(row=0, column=0, sticky="w")
        ghost_button(acts, f"Abrir todos ({len(rep['wanted_ids'])})", self.open_all
                     ).grid(row=0, column=1, sticky="ew", padx=(5, 0))

        self.filter = ctk.CTkSegmentedButton(self, values=list(self.FILTERS), command=lambda _: self.render_list(),
                                             font=font(12), selected_color=C["accent"],
                                             selected_hover_color=C["accent_h"], unselected_color=C["card2"],
                                             unselected_hover_color=C["border"], text_color=C["text"])
        self.filter.set("Me faltan" if self.missing else "Todos")
        self.filter.pack(fill="x", padx=20, pady=(12, 0))

        self.listbox = ctk.CTkScrollableFrame(self, fg_color=C["card"], corner_radius=12)
        self.listbox.pack(fill="both", expand=True, padx=20, pady=12)

        # Colección de Steam (opcional)
        self.coll = collection_id(main.app.repo.group().get("collection", ""))
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=20, pady=(0, 14))
        if self.coll:
            ghost_button(foot, "📦  Abrir la colección del grupo en Steam",
                         lambda: open_workshop(self.coll)).pack(fill="x")
        self.coll_toggle = ctk.CTkButton(foot, text="Colección de Steam (opcional) ▸", height=26,
                                         fg_color="transparent", hover_color=C["card2"],
                                         text_color=C["muted"], font=font(12), anchor="w",
                                         command=self.toggle_collection)
        self.coll_toggle.pack(fill="x", pady=(6, 0))
        self.coll_box = ctk.CTkFrame(foot, fg_color="transparent")
        self.coll_entry = ctk.CTkEntry(self.coll_box, height=32, corner_radius=8, font=font(12),
                                       placeholder_text="Link de la colección (opcional)",
                                       border_color=C["border"])
        self.coll_entry.pack(side="left", fill="x", expand=True)
        if self.coll:
            self.coll_entry.insert(0, f"https://steamcommunity.com/sharedfiles/filedetails/?id={self.coll}")
        ghost_button(self.coll_box, "Guardar", self.save_collection, width=80).pack(side="left", padx=(8, 0))

        self.render_list()
        self.main.app.bg(lambda: fetch_workshop_details(rep["wanted_ids"]), self.on_details, lambda e: None)

    # — lista interactiva —
    def toggle_collection(self):
        if self.coll_box.winfo_ismapped():
            self.coll_box.pack_forget()
            self.coll_toggle.configure(text="Colección de Steam (opcional) ▸")
        else:
            self.coll_box.pack(fill="x", pady=(4, 0))
            self.coll_toggle.configure(text="Colección de Steam (opcional) ▾")

    def on_details(self, fresh):
        if not self.winfo_exists():
            return
        self.details.update(fresh or {})
        self.render_list()

    def render_list(self):
        for w in self.listbox.winfo_children():
            w.destroy()
        self.thumb_labels = {}
        mode = self.filter.get()
        ids = self.rep["wanted_ids"]
        if mode == "Me faltan":
            ids = [w for w in ids if w in self.missing]
        elif mode == "Instalados":
            ids = [w for w in ids if w not in self.missing]
        if not self.rep["wanted_ids"]:
            ctk.CTkLabel(self.listbox, text="La partida no tiene items de Workshop (WorkshopItems vacío).",
                         font=font(12), text_color=C["muted"], wraplength=460).pack(anchor="w", padx=8, pady=8)
        elif not ids:
            ctk.CTkLabel(self.listbox, text="✓ Nada por aquí." if mode == "Me faltan" else "Nada que mostrar.",
                         font=font(13), text_color=C["accent"]).pack(anchor="w", padx=8, pady=8)
        for wid in ids:
            self.mod_card(wid)
        self.review_sections()
        # miniaturas en segundo plano
        todo = [(w, self.details.get(w, {}).get("preview", "")) for w in ids if w not in self.thumbs]
        if todo:
            self.main.app.bg(lambda: [(w, fetch_thumb(w, u)) for w, u in todo], self.on_thumbs, lambda e: None)
        for w in ids:
            if w in self.thumbs and w in self.thumb_labels:
                self.thumb_labels[w].configure(image=self.thumbs[w], text="")

    def on_thumbs(self, pairs):
        if not self.winfo_exists():
            return
        for wid, img in pairs:
            if img is None:
                continue
            self.thumbs[wid] = ctk.CTkImage(light_image=img, dark_image=img, size=(64, 64))
            lbl = self.thumb_labels.get(wid)
            if lbl is not None and lbl.winfo_exists():
                lbl.configure(image=self.thumbs[wid], text="")

    def mod_card(self, wid):
        d = self.details.get(wid, {})
        missing = wid in self.missing
        box = ctk.CTkFrame(self.listbox, fg_color=C["card2"], corner_radius=10, border_width=1,
                           border_color=C["danger"] if missing else C["border"])
        box.pack(fill="x", padx=4, pady=4)
        thumb = ctk.CTkLabel(box, text="🧩", width=64, height=64, fg_color=C["card"], corner_radius=8,
                             font=font(22))
        thumb.grid(row=0, column=0, rowspan=3, padx=10, pady=10, sticky="n")
        self.thumb_labels[wid] = thumb
        box.grid_columnconfigure(1, weight=1)

        title = ctk.CTkLabel(box, text=d.get("title", f"Workshop {wid}"), font=font(13, "bold"),
                             text_color=C["text"], anchor="w", justify="left", wraplength=250)
        title.grid(row=0, column=1, sticky="ew", pady=(10, 0))
        chips = "✗ Te falta" if missing else "✓ Instalado"
        tags = d.get("tags", [])
        b42 = "" if not d else ("  ·  B42" if "Build 42" in tags else "  ·  ⚠ sin tag B42")
        meta = f"{chips}{b42}"
        if d.get("subs"):
            meta += f"  ·  {fmt_subs(d['subs'])} suscriptores"
        if d.get("updated"):
            meta += f"  ·  act. {datetime.datetime.fromtimestamp(d['updated']).strftime('%d/%m/%Y')}"
        ctk.CTkLabel(box, text=meta, font=font(11), anchor="w", justify="left", wraplength=250,
                     text_color=C["danger"] if missing else C["muted"]).grid(row=1, column=1, sticky="ew")
        desc = d.get("desc", "") or ("Cargando descripción…" if not d else "Sin descripción.")
        full = wid in self.expanded
        flat = " ".join(desc.split())
        short = desc if full else (flat[:150].rsplit(" ", 1)[0] + "…" if len(flat) > 150 else flat)
        dl = ctk.CTkLabel(box, text=short[:900], font=font(11), text_color=C["text"], anchor="w",
                          justify="left", wraplength=250)
        dl.grid(row=2, column=1, sticky="ew", pady=(4, 10))

        btns = ctk.CTkFrame(box, fg_color="transparent")
        btns.grid(row=0, column=2, rowspan=3, padx=10, pady=10, sticky="n")
        ctk.CTkButton(btns, text="Suscribirme" if missing else "Abrir en Steam", width=110, height=30,
                      corner_radius=8, font=font(12, "bold" if missing else "normal"),
                      fg_color=C["accent"] if missing else C["card"],
                      hover_color=C["accent_h"] if missing else C["border"],
                      text_color="#0B0F0C" if missing else C["text"],
                      command=lambda: open_workshop(wid)).pack(pady=(0, 6))
        if len(desc) > 150:
            ctk.CTkButton(btns, text="Ver menos" if full else "Ver más", width=110, height=26,
                          corner_radius=8, font=font(11), fg_color="transparent", hover_color=C["card"],
                          text_color=C["accent"], command=lambda: self.toggle(wid)).pack()
        for w in (box, title, dl, thumb):
            w.bind("<Button-1>", lambda _e: self.toggle(wid))

    def toggle(self, wid):
        self.expanded.symmetric_difference_update({wid})
        self.render_list()

    def review_sections(self):
        """Revisión de Cristian: mods sin ID, items sin listar y descargados sin activar."""
        rep, n, ws = self.rep, self.rep["names"], self.rep["ws_names"]
        if not (rep["missing_mods"] or rep["unlisted"] or rep["unused_ws"]):
            return
        ctk.CTkLabel(self.listbox, text="Revisión", font=font(13, "bold"), text_color=C["text"]
                     ).pack(anchor="w", padx=8, pady=(14, 0))
        extra = " (casi seguro vienen de los items que te faltan)" if rep["missing_ws"] else ""
        self.link_rows(self.listbox, "Mods activos que no están en tu PC" + extra,
                       C["muted"] if rep["missing_ws"] else C["danger"],
                       [(m, "Buscar", lambda m=m: search_workshop(m)) for m in rep["missing_mods"]])
        self.section(self.listbox, "Activos, pero su item no está en WorkshopItems (tus amigos no lo bajarán solo)",
                     C["warn"], [n.get(m, m) for m in rep["unlisted"]])
        if rep["unlisted_ws"]:
            ctk.CTkButton(self.listbox, text=f"Añadir {len(rep['unlisted_ws'])} items a WorkshopItems", height=34,
                          corner_radius=10, font=font(12), fg_color=C["card2"], hover_color=C["border"],
                          text_color=C["text"], command=self.fix_unlisted).pack(fill="x", padx=8, pady=(6, 0))
        self.section(self.listbox, "Descargados pero ningún mod suyo está activado", C["warn"],
                     [ws.get(w, w) for w in rep["unused_ws"]])

    def save_collection(self):
        text = self.coll_entry.get().strip()
        cid = collection_id(text)
        if text and not cid:
            return messagebox.showwarning(APP_NAME, "Ese link no parece de Steam. Copia el link de la "
                                          "colección (…filedetails/?id=123456).", parent=self)
        self.main.app.repo.set_group(collection=cid or "")
        messagebox.showinfo(APP_NAME, "Guardado. Tus amigos lo verán cuando subas la partida "
                            "(⤒ Subir partida o al terminar de jugar).", parent=self)
        self.destroy()

    def open_all(self):
        ids = self.rep["wanted_ids"]
        if not messagebox.askyesno(APP_NAME, f"Se abrirán {len(ids)} páginas en Steam, una por mod. "
                                   "Dale «Suscribirse» en cada una.\n\n¿Continuar?", parent=self):
            return
        for wid in ids:
            open_workshop(wid)
            time.sleep(0.5)

    @staticmethod
    def link_rows(parent, title, color, rows):
        if not rows:
            return
        ctk.CTkLabel(parent, text=title, font=font(12, "bold"), text_color=color,
                     wraplength=380, justify="left", anchor="w").pack(fill="x", padx=8, pady=(10, 2))
        for label, btn, cmd in rows:
            line = ctk.CTkFrame(parent, fg_color="transparent")
            line.pack(fill="x", padx=8, pady=2)
            ctk.CTkButton(line, text=btn, width=64, height=28, corner_radius=8, font=font(12),
                          fg_color=C["card2"], hover_color=C["border"], text_color=C["text"],
                          command=cmd).pack(side="right")
            ctk.CTkLabel(line, text=f"• {label}", font=font(12), text_color=C["text"],
                         anchor="w").pack(side="left", fill="x", expand=True)

    @staticmethod
    def section(parent, title, color, items):
        if not items:
            return
        ctk.CTkLabel(parent, text=title, font=font(12, "bold"), text_color=color,
                     wraplength=380, justify="left", anchor="w").pack(fill="x", padx=8, pady=(10, 2))
        for it in items:
            ctk.CTkLabel(parent, text=f"• {it}", font=font(12), text_color=C["text"],
                         wraplength=380, justify="left", anchor="w").pack(fill="x", padx=8)

    def fix_unlisted(self):
        if game_running():
            return messagebox.showinfo(APP_NAME, "Cierra el juego primero.", parent=self)
        ids = self.rep["unlisted_ws"]
        if not messagebox.askyesno(
                APP_NAME, f"Se agregarán {len(ids)} items a WorkshopItems del archivo de la partida, "
                          "para que tus amigos los descarguen solos. El cambio se sube al jugar.\n\n¿Continuar?",
                parent=self):
            return
        add_workshop_items(self.main.app.repo.folder, ids)
        self.main.render_mods()
        self.destroy()
        messagebox.showinfo(APP_NAME, "Listo. Se subirá la próxima vez que juegues.", parent=self.main)

    def open_steam(self):
        ids = self.rep["missing_ws"]
        if not messagebox.askyesno(APP_NAME, f"Se abrirán {len(ids)} páginas de mods en Steam. "
                                   "Dale «Suscribirse» a cada una.\n\n¿Continuar?", parent=self):
            return
        for wid in ids:
            open_workshop(wid)
            time.sleep(0.5)


class LiveMapDialog(ctk.CTkToplevel):
    def __init__(self, main: MainView):
        super().__init__(main.app)
        self.main = main
        self.repo = main.app.repo
        cfg = self.repo.group().get("livemap") or {}
        self.title("Mapa en vivo")
        self.geometry("460x660")
        self.resizable(False, False)
        self.configure(fg_color=C["bg"])
        self.transient(main.app)

        ctk.CTkLabel(self, text="🗺  Mapa en vivo", font=font(18, "bold"),
                     text_color=C["text"]).pack(anchor="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(self, text="Muestra en una web dónde está cada jugador mientras juegan.",
                     font=font(12), text_color=C["muted"]).pack(anchor="w", padx=20)

        st = card(self)
        st.pack(fill="x", padx=20, pady=(14, 0))
        self.status = ctk.CTkLabel(st, text="", font=font(13, "bold"), text_color=C["text"], anchor="w")
        self.status.pack(fill="x", padx=14, pady=(12, 2))
        self.mod_lbl = ctk.CTkLabel(st, text="", font=font(12), text_color=C["muted"], anchor="w")
        self.mod_lbl.pack(fill="x", padx=14, pady=(0, 12))

        form = card(self)
        form.pack(fill="x", padx=20, pady=(12, 0))
        self.url = self.field(form, "Dirección del mapa", "https://tu-pi.ts.net/pzmap/", cfg.get("url", ""))
        self.token = self.field(form, "Token para enviar", "el PZMAP_TOKEN de la Pi", cfg.get("token", ""))
        self.view = self.field(form, "Llave para ver (opcional)", "el PZMAP_VIEW_KEY, si pusiste uno",
                               cfg.get("view", ""))
        ctk.CTkLabel(form, text="Mapa del juego renderizado (para «en este PC», opcional)", font=font(12, "bold"),
                     text_color=C["muted"]).pack(anchor="w", padx=14, pady=(10, 4))
        trow = ctk.CTkFrame(form, fg_color="transparent")
        trow.pack(fill="x", padx=14)
        self.tiles = ctk.CTkEntry(trow, height=34, corner_radius=8, font=font(12),
                                  placeholder_text="carpeta de salida de pzmap2dzi", border_color=C["border"])
        self.tiles.pack(side="left", fill="x", expand=True)
        if main.app.cfg.get("map_tiles"):
            self.tiles.insert(0, main.app.cfg["map_tiles"])
        ghost_button(trow, "Elegir…", self.pick_tiles, width=80).pack(side="left", padx=(8, 0))
        ctk.CTkFrame(form, fg_color="transparent", height=8).pack()

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(14, 0))
        row.grid_columnconfigure((0, 1), weight=1)
        ghost_button(row, "Guardar", self.save).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ctk.CTkButton(row, text="Abrir el mapa", height=38, corner_radius=10, font=font(13, "bold"),
                      fg_color=C["accent"], hover_color=C["accent_h"], text_color="#0B0F0C",
                      command=self.open_map).grid(row=0, column=1, sticky="ew", padx=(5, 0))
        self.install_btn = ghost_button(self, "Instalar el mod en la partida", self.install)
        self.install_btn.pack(fill="x", padx=20, pady=(10, 0))
        ctk.CTkLabel(self, text="Los ajustes y el mod viajan con la partida: tus amigos no configuran nada.",
                     font=font(11), text_color=C["muted"], wraplength=400).pack(padx=20, pady=(10, 0))
        self.refresh()

    def field(self, parent, label, placeholder, value):
        ctk.CTkLabel(parent, text=label, font=font(12, "bold"),
                     text_color=C["muted"]).pack(anchor="w", padx=14, pady=(10, 4))
        e = ctk.CTkEntry(parent, height=34, corner_radius=8, font=font(12),
                         placeholder_text=placeholder, border_color=C["border"])
        e.pack(fill="x", padx=14)
        if value:
            e.insert(0, value)
        return e

    def refresh(self):
        if not self.winfo_exists():
            return
        relay = self.main.app.relay
        ok = relay.last_ok and time.time() - relay.last_ok < 20
        self.status.configure(text=("● " if ok else "○ ") + relay.status.capitalize(),
                              text_color=C["accent"] if ok else C["text"])
        if livemap_installed(self.repo.folder):
            self.mod_lbl.configure(text="✓ Mod PZLiveMap instalado en la partida", text_color=C["accent"])
            self.install_btn.configure(text="Reinstalar / actualizar el mod")
        else:
            self.mod_lbl.configure(text="⚠ Falta instalar el mod en la partida", text_color=C["warn"])
        self.after(2000, self.refresh)

    def pick_tiles(self):
        d = filedialog.askdirectory(parent=self, title="Carpeta de salida de pzmap2dzi")
        if d:
            self.tiles.delete(0, "end")
            self.tiles.insert(0, str(Path(d)))

    def save(self, quiet=False):
        tiles = self.tiles.get().strip()
        found = LocalMap.find_dzi(tiles)
        if tiles and not found:
            return messagebox.showwarning(APP_NAME, "En esa carpeta no encontré base_top/layer0.dzi. Elige la carpeta "
                                          "de salida de pzmap2dzi (la que tiene html/map_data).", parent=self)
        self.main.app.cfg["map_tiles"] = tiles
        save_config(self.main.app.cfg)
        self.main.app.localmap.tiles_dir = found
        url = self.url.get().strip()
        if url and not url.startswith(("http://", "https://")):
            return messagebox.showwarning(APP_NAME, "La dirección debe empezar por https://", parent=self)
        if url and not url.endswith("/"):
            url += "/"
        self.repo.set_group(livemap={"url": url, "token": self.token.get().strip(),
                                     "view": self.view.get().strip()})
        if not quiet:
            messagebox.showinfo(APP_NAME, "Guardado. Se comparte con tus amigos al subir la partida.", parent=self)

    def open_map(self):
        url = self.url.get().strip()
        if not url:
            return messagebox.showinfo(APP_NAME, "Primero pon la dirección del mapa.", parent=self)
        view = self.view.get().strip()
        webbrowser.open(url + (("&" if "?" in url else "?") + "k=" + urllib.parse.quote(view) if view else ""))

    def install(self):
        if game_running():
            return messagebox.showinfo(APP_NAME, "Cierra el juego primero.", parent=self)
        try:
            self.save(quiet=True)
            n = install_livemap(self.repo.folder)
        except Exception as e:  # noqa: BLE001
            return self.main.app.error(e)
        messagebox.showinfo(APP_NAME, "Listo: el mod quedó en la partida" + (" y activado en el servidor"
                            if n else "") + ".\n\nDale «⤒ Subir partida» para que les llegue a tus amigos.",
                            parent=self)
        self.main.render_mods()


def session_row(parent, ses, wrap=360):
    line = ctk.CTkFrame(parent, fg_color="transparent")
    line.pack(fill="x", pady=2)
    initial = (ses["who"][:1] or "?").upper()
    ctk.CTkLabel(line, text=initial, width=26, height=26, corner_radius=13, fg_color=C["card2"],
                 font=font(12, "bold"), text_color=C["accent"]).pack(side="left")
    ctk.CTkLabel(line, text=f"  {ses['who']}", font=font(12, "bold"), text_color=C["text"]).pack(side="left")
    ctk.CTkLabel(line, text=fmt_minutes(ses["minutes"]), font=font(12), text_color=C["text"]).pack(side="right")
    ctk.CTkLabel(line, text=f"  ·  {fmt_when(ses['start'])}", font=font(12),
                 text_color=C["muted"]).pack(side="left")


class HistoryDialog(ctk.CTkToplevel):
    def __init__(self, main: MainView, sessions):
        super().__init__(main.app)
        self.title("Historial de la partida")
        self.geometry("440x560")
        self.minsize(380, 420)
        self.configure(fg_color=C["bg"])
        self.transient(main.app)

        ctk.CTkLabel(self, text="Historial de la partida", font=font(17, "bold"),
                     text_color=C["text"]).pack(anchor="w", padx=20, pady=(18, 2))
        total = sum(s["minutes"] or 0 for s in sessions)
        ctk.CTkLabel(self, text=f"{len(sessions)} sesiones · {fmt_minutes(total)} jugados en total",
                     font=font(12), text_color=C["muted"]).pack(anchor="w", padx=20)

        # Ranking por jugador
        stats = {}
        for s_ in sessions:
            st = stats.setdefault(s_["who"], {"n": 0, "min": 0, "last": 0})
            st["n"] += 1
            st["min"] += s_["minutes"] or 0
            st["last"] = max(st["last"], s_["end"])
        rank = card(self)
        rank.pack(fill="x", padx=20, pady=(14, 0))
        ctk.CTkLabel(rank, text="Quién ha jugado más", font=font(13, "bold"),
                     text_color=C["text"]).pack(anchor="w", padx=14, pady=(12, 6))
        top = max([st["min"] for st in stats.values()] or [1]) or 1
        medals = ["🥇", "🥈", "🥉"]
        for i, (who, st) in enumerate(sorted(stats.items(), key=lambda kv: -kv[1]["min"])):
            row = ctk.CTkFrame(rank, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=3)
            ctk.CTkLabel(row, text=f"{medals[i] if i < 3 else '  '} {who}", font=font(12, "bold"),
                         text_color=C["text"], width=130, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=f"{fmt_minutes(st['min'])} · {st['n']}×", font=font(11),
                         text_color=C["muted"]).pack(side="right")
            bar = ctk.CTkProgressBar(row, height=8, progress_color=C["accent"], fg_color=C["card2"])
            bar.set(st["min"] / top)
            bar.pack(side="left", fill="x", expand=True, padx=8)
        ctk.CTkFrame(rank, fg_color="transparent", height=8).pack()

        box = ctk.CTkScrollableFrame(self, fg_color=C["card"], corner_radius=12)
        box.pack(fill="both", expand=True, padx=20, pady=14)
        if not sessions:
            ctk.CTkLabel(box, text="Todavía no hay sesiones.", font=font(12),
                         text_color=C["muted"]).pack(anchor="w", padx=8, pady=8)
        for s_ in sessions:
            session_row(box, s_)


def already_running():
    """True si ya hay otra PZ Share abierta (y la trae al frente). Solo Windows."""
    if not IS_WIN:
        return False
    import ctypes
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.CreateMutexW.restype = ctypes.c_void_p
    handle = k32.CreateMutexW(None, False, r"Local\PZShareSingleInstance")
    if k32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        already_running.handle = handle  # mantenerlo vivo mientras corra la app
        return False
    hwnd = u32.FindWindowW(None, APP_NAME)
    if hwnd:
        u32.ShowWindow(hwnd, 9)  # SW_RESTORE
        u32.SetForegroundWindow(hwnd)
    return True


if __name__ == "__main__":
    if not already_running():
        App().mainloop()
