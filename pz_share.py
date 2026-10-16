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
import webbrowser
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
GIT_DOWNLOAD = "https://git-scm.com/download/win"

GITIGNORE = """# PZ Share: solo se comparte la partida. Graficos, teclas y logs quedan locales.
/*
!/.gitignore
!/host.lock
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
    """Traduce errores de git a algo entendible."""
    s = str(err).lower()
    if "could not resolve host" in s or "unable to access" in s:
        return "Sin conexión con GitHub. Revisa tu internet."
    if "authentication" in s or "terminal prompts disabled" in s or "403" in s:
        return "GitHub no te dejó entrar. Pide que te agreguen al repo e inicia sesión cuando se abra el navegador."
    if "not found" in s and "repository" in s:
        return "No se encontró el repo. Revisa el link y que tengas acceso."
    if "conflict" in s:
        return "Hubo un conflicto: alguien jugó al mismo tiempo. Avísale al grupo antes de seguir."
    return str(err)


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
        return False

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
        "names": names,
        "missing_ws": missing_ws,
        # activos que no estan en este PC y que no se explican por un item de Workshop faltante
        "missing_mods": [m for m, w in active if m not in installed_ids and w not in missing_ws],
        # item de Workshop descargado (y pedido) pero ninguno de sus mods esta activado
        "unused_ws": [w for w in wanted_ws if w in ws_mods and ws_mods[w] and not (ws_mods[w] & active_ids)],
        # mod activo que viene de un item de Workshop que no esta en WorkshopItems: los demas no lo bajaran
        "unlisted": [m for m, _ in active if m in ws_of and ws_of[m] not in wanted_ws and m not in local],
        "ws_names": {w: next(iter(sorted(names[m] for m in ms if m in names)), w)
                     for w, ms in ws_mods.items() if ms},
    }
    # Sin el mapa mod->item no se sabe de cual item viene cada mod faltante: si ya faltan items de
    # Workshop, esos mods se explican por ellos y no se cuentan aparte.
    rep["lacking"] = len(missing_ws) + (0 if missing_ws else len(rep["missing_mods"]))
    rep["problems"] = rep["lacking"] + len(rep["unlisted"]) + len(rep["unused_ws"])
    return rep


def open_workshop(wid):
    """Abre la pagina del mod en el cliente de Steam; sin Steam, en el navegador."""
    if steam_exe():
        webbrowser.open(f"steam://url/CommunityFilePage/{wid}")
    else:
        webbrowser.open(f"https://steamcommunity.com/sharedfiles/filedetails/?id={wid}")


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
        self.geometry("460x700")
        self.minsize(420, 660)
        self.configure(fg_color=C["bg"])
        self.cfg = load_config()
        self.busy = False
        self.playing = False
        self.view = None
        self.protocol("WM_DELETE_WINDOW", self.on_close)
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
        self.import_btn = ghost_button(row, "⤓  Importar partida existente", self.import_dialog)
        self.import_btn.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.mods_btn = ghost_button(row, "Mods de la partida", self.show_mods)
        self.mods_btn.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.release_btn = ctk.CTkButton(row, text="Liberar partida (quedó marcada como mía)", height=34,
                                         corner_radius=10, fg_color="transparent", border_width=1,
                                         border_color=C["danger"], text_color=C["danger"],
                                         hover_color="#2A1716", font=font(12), command=self.release)

        # Tip
        ctk.CTkLabel(self, text="Tip: en el juego elige Host. Tus amigos entran por invitación de Steam.",
                     font=font(11), text_color=C["muted"], wraplength=400).pack(side="bottom", pady=(10, 0))

        self.sync()

    # — estado visual —
    def set_busy(self, busy, text=""):
        self.app.busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.play_btn, self.sync_btn, self.import_btn):
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
        if last:
            self.sub_lbl.configure(text=f"Última vez: {last[0]} · {last[1]}")
        else:
            self.sub_lbl.configure(text="Aún no hay partidas guardadas. Usa Importar o juega la primera.")
        self.render_mods()

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
    def __init__(self, main: MainView, rep):
        super().__init__(main.app)
        self.rep = rep
        self.title("Mods de la partida")
        self.geometry("460x560")
        self.minsize(400, 400)
        self.configure(fg_color=C["bg"])
        self.transient(main.app)

        ctk.CTkLabel(self, text="Mods de la partida", font=font(17, "bold"),
                     text_color=C["text"]).pack(anchor="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(self, text=f"{rep['active']} mods activos · {rep['wanted_ws']} items de Workshop",
                     font=font(12), text_color=C["muted"]).pack(anchor="w", padx=20)

        box = ctk.CTkScrollableFrame(self, fg_color=C["card"], corner_radius=12)
        box.pack(fill="both", expand=True, padx=20, pady=14)
        n = rep["names"]
        ws = rep["ws_names"]
        if rep["missing_ws"]:
            ctk.CTkLabel(box, text="Te faltan (suscríbete en Steam)", font=font(12, "bold"),
                         text_color=C["danger"], anchor="w").pack(fill="x", padx=8, pady=(10, 2))
            for w in rep["missing_ws"]:
                line = ctk.CTkFrame(box, fg_color="transparent")
                line.pack(fill="x", padx=8, pady=2)
                ctk.CTkButton(line, text="Abrir", width=64, height=28, corner_radius=8, font=font(12),
                              fg_color=C["card2"], hover_color=C["border"], text_color=C["text"],
                              command=lambda w=w: open_workshop(w)).pack(side="right")
                ctk.CTkLabel(line, text=f"• Workshop {w}", font=font(12), text_color=C["text"],
                             anchor="w").pack(side="left", fill="x", expand=True)
        extra = " (casi seguro vienen de los items que te faltan)" if rep["missing_ws"] else ""
        self.section(box, "Mods activos que no están en tu PC" + extra,
                     C["muted"] if rep["missing_ws"] else C["danger"], rep["missing_mods"])
        self.section(box, "Activos, pero su item no está en WorkshopItems (tus amigos no lo bajarán solo)",
                     C["warn"], [n.get(m, m) for m in rep["unlisted"]])
        self.section(box, "Descargados pero ningún mod suyo está activado", C["warn"],
                     [ws.get(w, w) for w in rep["unused_ws"]])
        if not rep["problems"]:
            ctk.CTkLabel(box, text="✓ Todo en orden: los mods activos están instalados.",
                         font=font(13), text_color=C["accent"]).pack(anchor="w", padx=8, pady=8)

        if rep["missing_ws"]:
            ctk.CTkButton(self, text=f"Abrir {len(rep['missing_ws'])} mods en Steam", height=42,
                          corner_radius=10, font=font(14, "bold"), fg_color=C["accent"],
                          hover_color=C["accent_h"], text_color="#0B0F0C",
                          command=self.open_steam).pack(fill="x", padx=20, pady=(0, 18))
        else:
            self.geometry("460x500")

    @staticmethod
    def section(parent, title, color, items):
        if not items:
            return
        ctk.CTkLabel(parent, text=title, font=font(12, "bold"), text_color=color,
                     wraplength=380, justify="left", anchor="w").pack(fill="x", padx=8, pady=(10, 2))
        for it in items:
            ctk.CTkLabel(parent, text=f"• {it}", font=font(12), text_color=C["text"],
                         wraplength=380, justify="left", anchor="w").pack(fill="x", padx=8)

    def open_steam(self):
        ids = self.rep["missing_ws"]
        if not messagebox.askyesno(APP_NAME, f"Se abrirán {len(ids)} páginas de mods en Steam. "
                                   "Dale «Suscribirse» a cada una.\n\n¿Continuar?", parent=self):
            return
        for wid in ids:
            open_workshop(wid)
            time.sleep(0.5)


if __name__ == "__main__":
    App().mainloop()
