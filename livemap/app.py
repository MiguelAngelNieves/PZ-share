"""
PZ Live Map — mapa en vivo de una partida de Project Zomboid.

El mod PZLiveMap escribe las posiciones de los jugadores en un .json dentro del juego;
PZ Share (en el PC del que hostea) lo lee y lo manda aquí. Este servicio guarda el estado
en memoria y lo transmite a los navegadores en vivo (Server-Sent Events).
"""
import asyncio
import hmac
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

WRITE_TOKEN = os.environ.get("PZMAP_TOKEN", "")
VIEW_KEY = os.environ.get("PZMAP_VIEW_KEY", "")  # vacío = cualquiera con el link puede ver
DATA = Path(os.environ.get("PZMAP_DATA", "/data"))
STATIC = Path(__file__).parent / "static"
ONLINE_SECS = 25        # sin noticias por más de esto = desconectado
TRAIL_MAX = 800         # puntos de rastro por jugador
TRAIL_STEP = 4.0        # tiles mínimos entre puntos del rastro
SAVE_EVERY = 180        # segundos entre guardados a disco (la microSD agradece pocas escrituras)
EVENTS_MAX = 60

PREFIX = os.environ.get("PZMAP_PREFIX", "/pzmap").rstrip("/")

app = FastAPI(title="PZ Live Map", docs_url=None, redoc_url=None)


@app.middleware("http")
async def strip_prefix(request: Request, call_next):
    """Funciona igual detrás de un proxy que conserve el subpath (/pzmap/...) o que lo quite."""
    path = request.scope["path"]
    if PREFIX and (path == PREFIX or path.startswith(PREFIX + "/")):
        request.scope["path"] = path[len(PREFIX):] or "/"
    return await call_next(request)
state = {"world": {}, "players": {}, "events": [], "updated": 0}
listeners: set[asyncio.Queue] = set()
dirty = False


# ───────────────────────── estado ─────────────────────────
def load():
    global state
    try:
        state = json.loads((DATA / "state.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass


def save():
    global dirty
    if not dirty:
        return
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DATA / "state.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DATA / "state.json")
    dirty = False


def event(text, kind="info"):
    state["events"].insert(0, {"t": time.time(), "text": text, "kind": kind})
    del state["events"][EVENTS_MAX:]


def public_state():
    now = time.time()
    players = []
    for name, p in state["players"].items():
        players.append({**{k: v for k, v in p.items() if k != "trail"},
                        "name": name, "online": now - p.get("seen", 0) < ONLINE_SECS,
                        "trail": p.get("trail", [])[-TRAIL_MAX:]})
    players.sort(key=lambda p: (not p["online"], p["name"].lower()))
    return {"world": state["world"], "players": players, "events": state["events"][:30],
            "updated": state["updated"], "now": now}


async def broadcast():
    data = json.dumps(public_state(), ensure_ascii=False)
    for q in list(listeners):
        if q.qsize() < 5:
            q.put_nowait(data)


def check_view(k):
    if VIEW_KEY and not hmac.compare_digest(k or "", VIEW_KEY):
        raise HTTPException(403, "Falta la llave de vista (?k=...)")


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ───────────────────────── API ─────────────────────────
@app.post("/api/update")
async def update(request: Request, authorization: str = Header("")):
    global dirty
    if not WRITE_TOKEN or not hmac.compare_digest(authorization.removeprefix("Bearer ").strip(), WRITE_TOKEN):
        raise HTTPException(401, "Token inválido")
    body = await request.json()
    now = time.time()
    w = body.get("world") or {}
    state["world"] = {"day": int(num(w.get("day"))), "hour": int(num(w.get("hour"))),
                      "minute": int(num(w.get("minute"))), "host": str(body.get("host", ""))[:40],
                      "server": str(w.get("server", ""))[:60]}
    for raw in (body.get("players") or [])[:32]:
        name = str(raw.get("name", ""))[:40].strip()
        if not name:
            continue
        p = state["players"].get(name)
        was_online = p is not None and now - p.get("seen", 0) < ONLINE_SECS
        if p is None:
            p = state["players"][name] = {"trail": [], "first": now}
        if not was_online:
            event(f"{name} se conectó", "join")
        dead = bool(raw.get("dead"))
        if dead and not p.get("dead"):
            event(f"💀 {name} murió", "death")
        x, y = num(raw.get("x")), num(raw.get("y"))
        trail = p.setdefault("trail", [])
        if not trail or abs(trail[-1][0] - x) + abs(trail[-1][1] - y) >= TRAIL_STEP:
            trail.append([round(x, 1), round(y, 1), int(now)])
            del trail[:-TRAIL_MAX]
        veh = raw.get("vehicle")
        if veh and not p.get("vehicle"):
            event(f"🚗 {name} se subió a un vehículo", "info")
        p.update(x=x, y=y, z=int(num(raw.get("z"))), hp=max(0, min(100, num(raw.get("hp"), 100))),
                 dead=dead, vehicle=str(veh)[:40] if veh else None, seen=now)
    state["updated"] = now
    dirty = True
    await broadcast()
    return {"ok": True}


@app.get("/api/state")
async def get_state(k: str = ""):
    check_view(k)
    return JSONResponse(public_state())


@app.get("/api/stream")
async def stream(request: Request, k: str = ""):
    check_view(k)
    q: asyncio.Queue = asyncio.Queue()
    listeners.add(q)

    async def gen():
        try:
            yield f"data: {json.dumps(public_state(), ensure_ascii=False)}\n\n"
            while not await request.is_disconnected():
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            listeners.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/reset-trails")
async def reset_trails(authorization: str = Header("")):
    global dirty
    if not WRITE_TOKEN or not hmac.compare_digest(authorization.removeprefix("Bearer ").strip(), WRITE_TOKEN):
        raise HTTPException(401, "Token inválido")
    for p in state["players"].values():
        p["trail"] = []
    dirty = True
    await broadcast()
    return {"ok": True}


@app.get("/towns.json")
async def towns():
    custom = DATA / "towns.json"
    return FileResponse(custom if custom.exists() else STATIC / "towns.json")


@app.get("/config.json")
async def config():
    img = DATA / "map.png"
    bounds = os.environ.get("PZMAP_IMAGE_BOUNDS", "")
    return {"tiles": os.environ.get("PZMAP_TILES", ""), "image": img.exists(),
            "image_bounds": [num(v) for v in bounds.split(",")] if bounds.count(",") == 3 else None,
            "title": os.environ.get("PZMAP_TITLE", "PZ Live Map"), "need_key": bool(VIEW_KEY)}


@app.get("/map.png")
async def map_png():
    img = DATA / "map.png"
    if not img.exists():
        raise HTTPException(404)
    return FileResponse(img)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


# ───────────────────────── tareas ─────────────────────────
async def ticker():
    """Detecta desconexiones, refresca a los navegadores y guarda cada tanto."""
    last_save = time.time()
    online = set()
    while True:
        await asyncio.sleep(5)
        now = time.time()
        current = {n for n, p in state["players"].items() if now - p.get("seen", 0) < ONLINE_SECS}
        for gone in online - current:
            event(f"{gone} se desconectó", "leave")
            await broadcast()
        online = current
        if now - last_save > SAVE_EVERY:
            save()
            last_save = now


@app.on_event("startup")
async def startup():
    load()
    asyncio.create_task(ticker())


@app.on_event("shutdown")
async def shutdown():
    save()
