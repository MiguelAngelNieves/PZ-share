"""
PZ Live Map — mapa en vivo de una partida de Project Zomboid (servidor para la Raspberry Pi).

El mod PZLiveMap escribe el estado de los jugadores en un .json dentro del juego; PZ Share (en el PC
del que hostea) lo lee y lo manda aquí. La lógica vive en pzcore.py; este archivo es solo la API web
y el stream en vivo a los navegadores (Server-Sent Events).
"""
import asyncio
import hmac
import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pzcore import Core, num

WRITE_TOKEN = os.environ.get("PZMAP_TOKEN", "")
VIEW_KEY = os.environ.get("PZMAP_VIEW_KEY", "")  # vacío = cualquiera con el link puede ver
DATA = Path(os.environ.get("PZMAP_DATA", "/data"))
STATIC = Path(__file__).parent / "static"
SAVE_EVERY = 180        # segundos entre guardados a disco (la microSD agradece pocas escrituras)
PREFIX = os.environ.get("PZMAP_PREFIX", "/pzmap").rstrip("/")
TITLE = os.environ.get("PZMAP_TITLE", "PZ Live Map")


def load_towns():
    for f in (DATA / "towns.json", STATIC / "towns.json"):
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
    return []


core = Core(DATA, load_towns(), os.environ.get("PZMAP_DISCORD_WEBHOOK", ""), TITLE)
listeners: set[asyncio.Queue] = set()
app = FastAPI(title="PZ Live Map", docs_url=None, redoc_url=None)


@app.middleware("http")
async def strip_prefix(request: Request, call_next):
    """Funciona igual detrás de un proxy que conserve el subpath (/pzmap/...) o que lo quite."""
    path = request.scope["path"]
    if PREFIX and (path == PREFIX or path.startswith(PREFIX + "/")):
        request.scope["path"] = path[len(PREFIX):] or "/"
    return await call_next(request)


# ───────────────────────── seguridad ─────────────────────────
def check_view(k):
    if VIEW_KEY and not hmac.compare_digest(k or "", VIEW_KEY):
        raise HTTPException(403, "Falta la llave de vista (?k=...)")


def check_token(authorization):
    if not WRITE_TOKEN or not hmac.compare_digest(authorization.removeprefix("Bearer ").strip(), WRITE_TOKEN):
        raise HTTPException(401, "Token inválido")


async def body_json(request: Request, limit=20_000):
    raw = await request.body()
    if len(raw) > limit:
        raise HTTPException(413, "Demasiado grande")
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "JSON inválido") from None
    if not isinstance(data, dict):
        raise HTTPException(400, "JSON inválido")
    return data


def guard(fn, *a):
    try:
        return fn(*a)
    except KeyError:
        raise HTTPException(404, "No existe") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# ───────────────────────── en vivo ─────────────────────────
async def broadcast():
    data = json.dumps(core.public_state(take_cells=True), ensure_ascii=False)
    for q in list(listeners):
        if q.qsize() < 5:
            q.put_nowait(data)


def on_change(_kind):
    try:
        asyncio.get_running_loop().create_task(broadcast())
    except RuntimeError:
        pass


core.on_change = on_change


@app.post("/api/update")
async def update(request: Request, authorization: str = Header("")):
    check_token(authorization)
    core.update(await body_json(request, 200_000))
    return {"ok": True}


@app.get("/api/state")
async def get_state(k: str = ""):
    check_view(k)
    return JSONResponse(core.public_state())


@app.get("/api/stream")
async def stream(request: Request, k: str = ""):
    check_view(k)
    q: asyncio.Queue = asyncio.Queue()
    listeners.add(q)

    async def gen():
        try:
            yield f"data: {json.dumps(core.public_state(), ensure_ascii=False)}\n\n"
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


@app.get("/api/explored")
async def explored(k: str = ""):
    check_view(k)
    return core.explored_list()


@app.get("/api/history")
async def history(k: str = ""):
    check_view(k)
    return core.history()


@app.get("/api/achievements")
async def achievements(k: str = ""):
    check_view(k)
    return core.achievements()


# ───────────────────────── marcas, pings y tareas (los del grupo, desde la web) ─────────────────────────
@app.post("/api/marks")
async def add_mark(request: Request, k: str = ""):
    check_view(k)
    return guard(core.add_mark, await body_json(request))


@app.patch("/api/marks/{mid}")
async def edit_mark(mid: str, request: Request, k: str = ""):
    check_view(k)
    return guard(core.edit_mark, mid, await body_json(request))


@app.delete("/api/marks/{mid}")
async def del_mark(mid: str, k: str = ""):
    check_view(k)
    core.del_mark(mid)
    return {"ok": True}


@app.post("/api/ping")
async def ping(request: Request, k: str = ""):
    check_view(k)
    return guard(core.add_ping, await body_json(request))


@app.get("/api/inbox")
async def inbox(authorization: str = Header("")):
    """Para PZ Share: los pings recientes, en el formato que lee el mod."""
    check_token(authorization)
    return PlainTextResponse(core.inbox_lines())


@app.post("/api/tasks")
async def add_task(request: Request, k: str = ""):
    check_view(k)
    return guard(core.add_task, await body_json(request))


@app.patch("/api/tasks/{tid}")
async def set_task(tid: str, request: Request, k: str = ""):
    check_view(k)
    return guard(core.set_task, tid, await body_json(request))


@app.delete("/api/tasks/{tid}")
async def del_task(tid: str, k: str = ""):
    check_view(k)
    core.del_task(tid)
    return {"ok": True}


# ───────────────────────── fotos ─────────────────────────
@app.post("/api/photo")
async def add_photo(request: Request, authorization: str = Header(""), by: str = "", x: float = 0, y: float = 0,
                    day: int = 0, ext: str = "jpg"):
    """PZ Share sube aquí las capturas de Steam (F12), con la posición del jugador en ese momento."""
    check_token(authorization)
    data = await request.body()
    return guard(core.add_photo, {"by": by, "x": x, "y": y, "day": day}, data, ext)


@app.get("/api/photos")
async def photos(k: str = ""):
    check_view(k)
    return core.state["photos"]


@app.get("/photos/{file}")
async def photo_file(file: str, k: str = ""):
    check_view(k)
    f = core.photo_path(file)
    if not f:
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/reset-trails")
async def reset_trails(authorization: str = Header("")):
    check_token(authorization)
    core.reset_trails()
    return {"ok": True}


# ───────────────────────── mapa ─────────────────────────
@app.get("/towns.json")
async def towns():
    custom = DATA / "towns.json"
    return FileResponse(custom if custom.exists() else STATIC / "towns.json")


def find_dzi():
    """Mapa renderizado con pzmap2dzi: data/tiles/<algo>/layer0.dzi + map_info.json."""
    custom = os.environ.get("PZMAP_DZI", "").strip("/")
    if custom:
        return custom + "/"
    tiles = DATA / "tiles"
    # primero la isométrica (más detalle), luego la vista superior
    for cand in ("base", "map_data/base", "html/map_data/base", "base_top", "map_data/base_top", "html/map_data/base_top"):
        if (tiles / cand / "layer0.dzi").exists() and (tiles / cand / "map_info.json").exists():
            return f"tiles/{cand}/"
    return ""


@app.get("/config.json")
async def config():
    img = DATA / "map.png"
    bounds = os.environ.get("PZMAP_IMAGE_BOUNDS", "")
    return {"dzi": find_dzi(), "tiles": os.environ.get("PZMAP_TILES", ""), "image": img.exists(),
            "image_bounds": [num(v) for v in bounds.split(",")] if bounds.count(",") == 3 else None,
            "title": TITLE, "need_key": bool(VIEW_KEY), "features": True}


@app.get("/map.png")
async def map_png():
    img = DATA / "map.png"
    if not img.exists():
        raise HTTPException(404)
    return FileResponse(img)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


(DATA / "tiles").mkdir(parents=True, exist_ok=True)
app.mount("/tiles", StaticFiles(directory=DATA / "tiles"), name="tiles")


# ───────────────────────── tareas de fondo ─────────────────────────
async def ticker():
    """Detecta desconexiones, refresca a los navegadores y guarda cada tanto."""
    elapsed = 0
    while True:
        await asyncio.sleep(5)
        elapsed += 5
        core.tick()
        if elapsed >= SAVE_EVERY:
            await asyncio.to_thread(core.save)
            elapsed = 0


@app.on_event("startup")
async def startup():
    asyncio.create_task(ticker())


@app.on_event("shutdown")
async def shutdown():
    core.save(force=True)
