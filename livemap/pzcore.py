"""
Núcleo del mapa en vivo: el estado de la partida y todo lo que se construye con él.
Solo biblioteca estándar: lo usan el servidor de la Pi (app.py) y el modo «en este PC» de PZ Share.

Qué guarda: jugadores (posición y estado que el jugador ve en su pantalla), rastros, niebla de guerra
(lo explorado), marcas, pings, tareas, fotos, historial para el timelapse, estadísticas y bitácora.
"""
import json
import math
import threading
import time
import urllib.request
from pathlib import Path

ONLINE_SECS = 25        # sin noticias por más de esto = desconectado
TRAIL_MAX = 600         # puntos de rastro por jugador (lo que se ve en vivo)
TRAIL_STEP = 4.0        # tiles mínimos entre puntos del rastro
HIST_STEP = 15.0        # tiles mínimos entre puntos del historial (timelapse)
CELL = 20               # niebla: celdas de 20×20 tiles
REVEAL = 2              # radio que se revela alrededor de cada jugador (en celdas, ~40 tiles)
EVENTS_MAX = 300
PING_TTL = 120          # segundos que un ping vive (en el mapa y en la bandeja del juego)
TOWN_RADIUS = 450       # tiles: «llegó a Muldraugh»
KEYW = 4096             # clave de celda = cy * KEYW + cx

MARK_KINDS = {"base", "loot", "peligro", "carro", "otro"}
PART_ES = {
    "Hand_L": "mano izq.", "Hand_R": "mano der.", "ForeArm_L": "antebrazo izq.", "ForeArm_R": "antebrazo der.",
    "UpperArm_L": "brazo izq.", "UpperArm_R": "brazo der.", "Torso_Upper": "pecho", "Torso_Lower": "abdomen",
    "Head": "cabeza", "Neck": "cuello", "Groin": "ingle", "UpperLeg_L": "muslo izq.", "UpperLeg_R": "muslo der.",
    "LowerLeg_L": "pierna izq.", "LowerLeg_R": "pierna der.", "Foot_L": "pie izq.", "Foot_R": "pie der.",
}


def num(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def clean(s, n=60):
    return " ".join(str(s or "").split())[:n]


class Core:
    def __init__(self, data_dir=None, towns=None, discord_webhook="", title="PZ Live Map"):
        self.data = Path(data_dir) if data_dir else None
        self.towns = towns or []
        self.webhook = discord_webhook or ""
        self.title = title
        self.lock = threading.RLock()
        self.on_change = None          # lo pone el servidor: avisa a los navegadores
        self.dirty = False
        self.hist_buf = []
        self.explored = set()
        self.new_cells = []            # celdas recién reveladas (van en el stream)
        self.pings = []
        self.seq = int(time.time() * 1000)  # ids únicos aunque se reinicie
        self.state = {"world": {}, "players": {}, "events": [], "updated": 0, "marks": [], "tasks": [],
                      "photos": [], "stats": {}, "group": {"towns": [], "vehicles": 0, "tasks_done": 0,
                                                            "last_death_day": None, "first_day": None}}
        self.online = set()
        self.load()

    # ───────────── persistencia (pocas escrituras: la microSD lo agradece) ─────────────
    def _f(self, name):
        return self.data / name

    def load(self):
        if not self.data:
            return
        try:
            st = json.loads(self._f("state.json").read_text(encoding="utf-8"))
            for k, v in st.items():
                self.state[k] = v
            self.state.setdefault("group", {}).setdefault("towns", [])
        except Exception:  # noqa: BLE001
            pass
        try:
            self.explored = set(json.loads(self._f("explored.json").read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass

    def save(self, force=False):
        if not self.data or not (self.dirty or force):
            return
        with self.lock:
            self.data.mkdir(parents=True, exist_ok=True)
            for name, obj in (("state.json", self.state), ("explored.json", sorted(self.explored))):
                tmp = self._f(name + ".tmp")
                tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                tmp.replace(self._f(name))
            if self.hist_buf:
                with self._f("history.jsonl").open("a", encoding="utf-8") as fh:
                    for row in self.hist_buf:
                        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                self.hist_buf = []
            self.dirty = False

    def changed(self, kind="state"):
        self.dirty = True
        if self.on_change:
            try:
                self.on_change(kind)
            except Exception:  # noqa: BLE001
                pass

    # ───────────── utilidades ─────────────
    def next_id(self):
        self.seq += 1
        return f"{self.seq:x}"

    def game_time(self):
        w = self.state.get("world") or {}
        return {"day": w.get("day"), "hour": w.get("hour"), "minute": w.get("minute")}

    def event(self, text, kind="info"):
        gt = self.game_time()
        self.state["events"].insert(0, {"t": time.time(), "text": text, "kind": kind, **gt})
        del self.state["events"][EVENTS_MAX:]

    def notify(self, text):
        """Aviso opcional a un canal de Discord (webhook). Nunca bloquea ni rompe nada."""
        if not self.webhook:
            return

        def go():
            try:
                req = urllib.request.Request(self.webhook, data=json.dumps({"content": text[:1900]}).encode(),
                                             headers={"Content-Type": "application/json", "User-Agent": "PZLiveMap"})
                urllib.request.urlopen(req, timeout=10).read()
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=go, daemon=True).start()

    def town_at(self, x, y, radius=TOWN_RADIUS):
        best, bd = None, radius
        for t in self.towns:
            d = math.hypot(num(t.get("x")) - x, num(t.get("y")) - y)
            if d < bd:
                best, bd = t.get("name"), d
        return best

    def reveal(self, x, y):
        cx, cy = int(x // CELL), int(y // CELL)
        for dy in range(-REVEAL, REVEAL + 1):
            for dx in range(-REVEAL, REVEAL + 1):
                if dx * dx + dy * dy <= REVEAL * REVEAL + 1:
                    k = (cy + dy) * KEYW + (cx + dx)
                    if k not in self.explored and cx + dx >= 0 and cy + dy >= 0:
                        self.explored.add(k)
                        self.new_cells.append(k)
        if len(self.new_cells) > 8000:
            del self.new_cells[:-4000]

    # ───────────── datos que llegan del juego ─────────────
    def update(self, body):
        with self.lock:
            self._update(body)
        self.changed()

    def _update(self, body):
        now = time.time()
        st = self.state
        w = body.get("world") or {}
        day = int(num(w.get("day")))
        st["world"] = {"day": day, "hour": int(num(w.get("hour"))), "minute": int(num(w.get("minute"))),
                       "host": clean(body.get("host"), 40), "server": clean(w.get("server"), 60)}
        grp = st["group"]
        if day and not grp.get("first_day"):
            grp["first_day"] = day
        was_anyone = any(now - p.get("seen", 0) < ONLINE_SECS for p in st["players"].values())
        for raw in (body.get("players") or [])[:32]:
            name = clean(raw.get("name"), 40)
            if not name:
                continue
            self._player(name, raw, now, day)
        if not was_anyone and any(now - p.get("seen", 0) < ONLINE_SECS for p in st["players"].values()):
            host = st["world"].get("host") or "Alguien"
            self.notify(f"🧟 **{host}** abrió la partida (día {day}). ¡Únanse!")
        st["updated"] = now

    def _player(self, name, raw, now, day):
        st = self.state
        p = st["players"].get(name)
        was_online = p is not None and now - p.get("seen", 0) < ONLINE_SECS
        if p is None:
            p = st["players"][name] = {"trail": [], "first": now}
        s = st["stats"].setdefault(name, {"km": 0.0, "kills": 0, "deaths": 0, "best_day_kills": 0, "day_kills": {},
                                          "car_km": 0.0, "best_car": 0.0, "cur_car": 0.0, "best_alive": 0,
                                          "life_start": day or 1, "last_kills": None, "photos": 0})
        if not was_online:
            self.event(f"{name} se conectó", "join")
        x, y = num(raw.get("x")), num(raw.get("y"))
        dead = bool(raw.get("dead"))
        veh = raw.get("vehicle")
        if isinstance(veh, str):
            veh = {"name": veh}
        veh = {"name": clean((veh or {}).get("name"), 40).removeprefix("Base."),
               "fuel": (veh or {}).get("fuel"), "driver": bool((veh or {}).get("driver"))} if veh else None

        # — estadísticas —
        if "x" in p and was_online:
            d = math.hypot(x - p["x"], y - p["y"])
            if d < 300:  # saltos enormes = teletransporte o reaparición, no cuentan
                s["km"] += d / 1000
                if veh and p.get("vehicle"):
                    s["car_km"] += d / 1000
                    s["cur_car"] += d / 1000
        if not veh and p.get("vehicle"):
            if s["cur_car"] > s["best_car"]:
                s["best_car"] = round(s["cur_car"], 2)
            s["cur_car"] = 0.0
        kills = int(num(raw.get("kills"), -1))
        if kills >= 0:
            last = s.get("last_kills")
            delta = kills - last if last is not None and kills >= last else (kills if last is not None else 0)
            s["last_kills"] = kills
            if delta > 0:
                s["kills"] += delta
                dk = s["day_kills"]
                dk[str(day)] = dk.get(str(day), 0) + delta
                s["best_day_kills"] = max(s["best_day_kills"], dk[str(day)])
                if len(dk) > 40:
                    for k in sorted(dk, key=lambda k: int(k))[:-40]:
                        dk.pop(k)
        if day:
            alive = day - int(s.get("life_start") or day) + 1
            s["best_alive"] = max(s.get("best_alive", 0), alive if not dead else 0)

        # — eventos —
        if dead and not p.get("dead"):
            s["deaths"] += 1
            s["life_start"] = day or 1
            s["last_kills"] = None
            self.state["group"]["last_death_day"] = day
            self.event(f"{name} murió", "death")
            self.notify(f"💀 **{name}** murió en el día {day}.")
        if not dead and p.get("dead"):
            s["life_start"] = day or 1
        if veh and not p.get("vehicle"):
            self.state["group"]["vehicles"] = self.state["group"].get("vehicles", 0) + 1
            self.event(f"{name} se subió a {veh['name'] or 'un vehículo'}", "car")
        town = self.town_at(x, y)
        if town and town != p.get("town"):
            visited = self.state["group"]["towns"]
            if town not in visited:
                visited.append(town)
                self.event(f"{name} llegó por primera vez a {town}", "town")
            else:
                self.event(f"{name} llegó a {town}", "town")
        p["town"] = town

        # — rastro, historial y niebla —
        tr = p.setdefault("trail", [])
        if not tr or abs(tr[-1][0] - x) + abs(tr[-1][1] - y) >= TRAIL_STEP:
            tr.append([round(x, 1), round(y, 1), int(now)])
            del tr[:-TRAIL_MAX]
        lh = p.get("_hist")
        if not lh or math.hypot(lh[0] - x, lh[1] - y) >= HIST_STEP or bool(veh) != lh[2]:
            w = self.state["world"]
            self.hist_buf.append([int(now), name, round(x), round(y), w.get("day"), w.get("hour"), w.get("minute"),
                                  1 if veh else 0])
            p["_hist"] = [x, y, bool(veh)]
        self.reveal(x, y)

        wounds = []
        for wd in (raw.get("wounds") or [])[:17]:
            part = clean(wd.get("part"), 20)
            wounds.append({"part": PART_ES.get(part, part), "w": [clean(k, 12) for k in (wd.get("w") or [])[:6]],
                           "band": bool(wd.get("band"))})
        moodles = {clean(k, 12): int(num(v)) for k, v in list((raw.get("moodles") or {}).items())[:16] if num(v) > 0}
        p.update(x=x, y=y, z=int(num(raw.get("z"))), hp=max(0, min(100, num(raw.get("hp"), 100))), dead=dead,
                 vehicle=veh, seen=now, kills=max(0, kills), weapon=clean(raw.get("weapon"), 40) or None,
                 bag=clean(raw.get("bag"), 40) or None, weight=num(raw.get("weight")),
                 maxWeight=num(raw.get("maxWeight")), moodles=moodles, wounds=wounds,
                 hours=num(raw.get("hours")))

    def tick(self):
        """Cada pocos segundos: detecta desconexiones y cierra sesiones."""
        now = time.time()
        with self.lock:
            current = {n for n, p in self.state["players"].items() if now - p.get("seen", 0) < ONLINE_SECS}
            gone = self.online - current
            for n in gone:
                self.event(f"{n} se desconectó", "leave")
            if self.online and not current:
                self.notify(self.session_summary())
            self.online = current
            self.pings = [p for p in self.pings if now - p["t"] < PING_TTL]
        if gone:
            self.changed()

    def session_summary(self):
        w = self.state.get("world") or {}
        lines = [f"📓 Fin de la sesión — día {w.get('day')}."]
        for n, s in self.state["stats"].items():
            lines.append(f"• {n}: {s['kills']} kills en total, {s['km']:.1f} km caminados")
        return "\n".join(lines)

    # ───────────── lo que hacen los del grupo desde la web ─────────────
    def add_mark(self, d):
        with self.lock:
            if len(self.state["marks"]) >= 300:
                raise ValueError("Demasiadas marcas")
            kind = d.get("kind") if d.get("kind") in MARK_KINDS else "otro"
            m = {"id": self.next_id(), "x": round(num(d.get("x"))), "y": round(num(d.get("y"))), "kind": kind,
                 "label": clean(d.get("label"), 40), "by": clean(d.get("by"), 40) or "Alguien", "t": time.time()}
            self.state["marks"].append(m)
            self.event(f"{m['by']} marcó {m['label'] or kind} en el mapa", "mark")
        self.changed()
        return m

    def edit_mark(self, mid, d):
        with self.lock:
            for m in self.state["marks"]:
                if m["id"] == mid:
                    if "label" in d:
                        m["label"] = clean(d.get("label"), 40)
                    if d.get("kind") in MARK_KINDS:
                        m["kind"] = d["kind"]
                    break
            else:
                raise KeyError(mid)
        self.changed()
        return m

    def del_mark(self, mid):
        with self.lock:
            self.state["marks"] = [m for m in self.state["marks"] if m["id"] != mid]
        self.changed()

    def add_ping(self, d):
        with self.lock:
            p = {"id": self.next_id(), "x": round(num(d.get("x"))), "y": round(num(d.get("y"))),
                 "from": clean(d.get("by"), 40).replace("|", " ") or "Alguien",
                 "text": clean(d.get("text"), 40).replace("|", " "), "t": time.time()}
            self.pings.append(p)
            del self.pings[:-30]
        self.changed()
        return p

    def inbox_lines(self):
        """La bandeja que PZ Share deja en Zomboid/Lua/pzlivemap_in.txt para el mod."""
        now = time.time()
        with self.lock:
            return "".join(f"{p['id']}|{p['x']}|{p['y']}|{p['from']}|{p['text']}\n"
                           for p in self.pings if now - p["t"] < PING_TTL)

    def add_task(self, d):
        with self.lock:
            if len(self.state["tasks"]) >= 200:
                raise ValueError("Demasiadas tareas")
            text = clean(d.get("text"), 120)
            if not text:
                raise ValueError("Escribe la tarea")
            t = {"id": self.next_id(), "text": text, "by": clean(d.get("by"), 40) or "Alguien",
                 "done": False, "doneBy": None, "t": time.time()}
            self.state["tasks"].append(t)
        self.changed()
        return t

    def set_task(self, tid, d):
        with self.lock:
            for t in self.state["tasks"]:
                if t["id"] == tid:
                    if "done" in d:
                        done = bool(d["done"])
                        if done and not t["done"]:
                            who = clean(d.get("by"), 40) or "Alguien"
                            t["doneBy"] = who
                            self.state["group"]["tasks_done"] = self.state["group"].get("tasks_done", 0) + 1
                            self.event(f"{who} completó: {t['text']}", "task")
                        t["done"] = done
                    if d.get("text"):
                        t["text"] = clean(d["text"], 120)
                    break
            else:
                raise KeyError(tid)
        self.changed()
        return t

    def del_task(self, tid):
        with self.lock:
            self.state["tasks"] = [t for t in self.state["tasks"] if t["id"] != tid]
        self.changed()

    # ───────────── fotos (capturas de Steam que sube PZ Share) ─────────────
    def add_photo(self, meta, data: bytes, ext="jpg"):
        if not self.data:
            raise ValueError("Sin carpeta de datos")
        if len(data) > 6_000_000:
            raise ValueError("Foto demasiado grande")
        ext = ext if ext in ("jpg", "png", "webp") else "jpg"
        with self.lock:
            if len(self.state["photos"]) >= 3000:
                raise ValueError("Álbum lleno")
            pid = self.next_id()
            folder = self.data / "photos"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{pid}.{ext}").write_bytes(data)
            name = clean(meta.get("by"), 40) or "Alguien"
            x, y = num(meta.get("x")), num(meta.get("y"))
            w = self.state.get("world") or {}
            ph = {"id": pid, "file": f"{pid}.{ext}", "by": name, "x": round(x), "y": round(y),
                  "day": int(num(meta.get("day"), w.get("day") or 0)), "hour": w.get("hour"), "minute": w.get("minute"),
                  "place": self.town_at(x, y, 900) or "Afueras", "t": time.time()}
            self.state["photos"].append(ph)
            st = self.state["stats"].get(name)
            if st is not None:
                st["photos"] = st.get("photos", 0) + 1
            self.event(f"{name} sacó una foto en {ph['place']}", "photo")
        self.changed()
        return ph

    def photo_path(self, file):
        if not self.data or "/" in file or "\\" in file or file.startswith("."):
            return None
        f = self.data / "photos" / file
        return f if f.is_file() else None

    # ───────────── lecturas ─────────────
    def public_state(self, take_cells=False):
        now = time.time()
        with self.lock:
            players = []
            for name, p in self.state["players"].items():
                q = {k: v for k, v in p.items() if not k.startswith("_") and k != "trail"}
                q.update(name=name, online=now - p.get("seen", 0) < ONLINE_SECS, trail=p.get("trail", [])[-300:])
                players.append(q)
            players.sort(key=lambda p: (not p["online"], p["name"].lower()))
            cells = self.new_cells
            if take_cells:
                self.new_cells = []
            return {"world": self.state["world"], "players": players, "events": self.state["events"][:40],
                    "updated": self.state["updated"], "now": now, "marks": self.state["marks"],
                    "tasks": self.state["tasks"], "pings": [p for p in self.pings if now - p["t"] < PING_TTL],
                    "explored_n": len(self.explored), "new_cells": cells[-4000:], "photos_n": len(self.state["photos"])}

    def explored_list(self):
        with self.lock:
            return {"cell": CELL, "keyw": KEYW, "cells": sorted(self.explored)}

    def history(self):
        rows = []
        if self.data and self._f("history.jsonl").exists():
            with self._f("history.jsonl").open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
        with self.lock:
            rows.extend(self.hist_buf)
            events = [e for e in self.state["events"] if e.get("kind") in ("death", "town", "car", "task", "photo", "mark")]
        return {"rows": rows[-200_000:], "events": events}

    def achievements(self):
        with self.lock:
            st = self.state
            stats = st["stats"]
            w = st.get("world") or {}
            day = w.get("day") or 0
            g = st["group"]
            kills = sum(s["kills"] for s in stats.values())
            km = sum(s["km"] for s in stats.values())
            km2 = len(self.explored) * CELL * CELL / 1e6
            best_car = max([s["best_car"] for s in stats.values()] + [s["cur_car"] for s in stats.values()] + [0])
            last_death = g.get("last_death_day")
            no_death = day - (last_death if last_death else (g.get("first_day") or 1)) + (0 if last_death else 1)

            def a(key, name, desc, value, goal):
                value = max(0, value)
                return {"key": key, "name": name, "desc": desc, "value": round(value, 1), "goal": goal,
                        "done": value >= goal}
            ach = [
                a("week", "Primera semana", "Sobrevivir 7 días", day, 7),
                a("month", "Un mes en Knox", "Sobrevivir 30 días", day, 30),
                a("wheels", "Sobre ruedas", "Subirse a un vehículo", g.get("vehicles", 0), 1),
                a("roadtrip", "Viaje por carretera", "Un viaje de 5 km en carro", best_car, 5),
                a("towns", "Turistas", f"Visitar los {len(self.towns)} pueblos", len(g.get("towns", [])), len(self.towns) or 8),
                a("k1000", "Mil caídos", "1.000 kills entre todos", kills, 1000),
                a("k5000", "Exterminio", "5.000 kills entre todos", kills, 5000),
                a("km100", "Maratón", "Caminar 100 km entre todos", km, 100),
                a("map10", "Cartógrafos", "Explorar 10 km²", km2, 10),
                a("photos", "Fotógrafos", "Sacar 10 fotos", len(st["photos"]), 10),
                a("tasks", "Organizados", "Completar 10 tareas", g.get("tasks_done", 0), 10),
                a("nodeath", "Nadie se queda atrás", "14 días seguidos sin muertes", no_death, 14),
            ]

            def best(key, fmt):
                if not stats:
                    return None
                n, s = max(stats.items(), key=lambda kv: kv[1].get(key, 0))
                v = s.get(key, 0)
                return {"name": n, "value": fmt(v)} if v else None
            records = [
                {"label": "Más kills en un día", **(best("best_day_kills", str) or {"name": "—", "value": ""})},
                {"label": "Viaje más largo en carro", **(best("best_car", lambda v: f"{v:.1f} km".replace(".", ",")) or {"name": "—", "value": ""})},
                {"label": "Más días vivo", **(best("best_alive", str) or {"name": "—", "value": ""})},
                {"label": "Más caminado", **(best("km", lambda v: f"{v:.0f} km") or {"name": "—", "value": ""})},
            ]
            per = [{"name": n, "kills": s["kills"], "km": round(s["km"], 1), "deaths": s["deaths"],
                    "best_alive": s.get("best_alive", 0), "photos": s.get("photos", 0)} for n, s in stats.items()]
            return {"totals": {"day": day, "kills": kills, "km": round(km, 1), "km2": round(km2, 2),
                               "towns": g.get("towns", []), "photos": len(st["photos"])},
                    "achievements": ach, "records": records, "players": per}

    def reset_trails(self):
        with self.lock:
            for p in self.state["players"].values():
                p["trail"] = []
        self.changed()
