import os, time, io, json, asyncio, subprocess, shutil
from datetime import datetime, timezone
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont

app = FastAPI(title="Cinder Brain Ops")
START_TS = time.time()
AGENTS: dict[str, WebSocket] = {}
LATEST_FRAME: dict[str, bytes] = {}
CMDS: dict[str, asyncio.Future] = {}
FRAMES: dict[str, bytes] = {}
LOGS: list[str] = []

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _log(msg: str):
    LOGS.append(f"{datetime.now(timezone.utc).isoformat()}  {msg}")
    if len(LOGS) > 300:
        LOGS[:] = LOGS[-300:]


def _health():
    return {
        "ok": True, "health": "/healthz", "ui": "/panel/",
        "time": datetime.now(timezone.utc).isoformat(),
        "uptime_s": int(time.time() - START_TS),
        "screenshot": True, "agents": len(AGENTS), "devices": len(AGENTS),
    }


@app.get("/healthz")
def healthz():
    return _health()


# 兼容前端多路径探测，根治 405
@app.get("/api/healthz")
@app.get("/api/health")
@app.get("/api/status")
def api_health():
    return _health()


@app.get("/api/devices")
def devices():
    return [{"id": k, "name": k, "platform": "android", "status": "online"} for k in AGENTS]


def _stub_frame(text: str) -> bytes:
    img = Image.new("RGB", (720, 1280), (18, 20, 28))
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34)
    except Exception:
        f = ImageFont.load_default()
    d.text((40, 560), "Cinder // Remote", fill=(120, 130, 160), font=f)
    d.text((40, 610), text, fill=(80, 88, 110), font=f)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return buf.getvalue()


@app.get("/api/screenshot")
@app.get("/screenshot")
def screenshot():
    if FRAMES:
        return StreamingResponse(io.BytesIO(list(FRAMES.values())[-1]), media_type="image/jpeg")
    return StreamingResponse(io.BytesIO(_stub_frame("no agent connected")), media_type="image/jpeg")


# ---- 录像：Agent 逐帧上报 / 合成 MP4 / 列表与下载 ----
REC: dict[str, list[bytes]] = {}
REC_DONE: dict[str, str] = {}
REC_DIR = os.path.join(os.path.dirname(__file__), "static", "clips")
os.makedirs(REC_DIR, exist_ok=True)

@app.post("/api/record")
async def record_start(action: str = Query("start"), device: str = Query("")):
    """action=start 开始；action=stop 停止并合成 MP4"""
    if action == "start":
        rid = "clip-%d" % int(time.time())
        REC[rid] = []
        return {"ok": True, "recording": True, "id": rid}
    if REC:
        rid = list(REC)[-1]
        frames = REC.pop(rid, [])
        path = os.path.join(REC_DIR, rid + ".mp4")
        ok = False
        if frames:
            try:  # 先把帧落成临时 jpg，再用 ffmpeg 合成
                tmp = os.path.join(REC_DIR, rid)
                os.makedirs(tmp, exist_ok=True)
                for i, b in enumerate(frames):
                    with open(os.path.join(tmp, "f%04d.jpg" % i), "wb") as f:
                        f.write(b)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "5",
                                "-i", os.path.join(tmp, "f%04d.jpg"),
                                "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
                               check=True, timeout=120)
                shutil.rmtree(tmp, ignore_errors=True)
                ok = True
            except Exception as e:
                return {"ok": False, "error": str(e), "frames": len(frames)}
        return {"ok": ok, "recording": False, "id": rid, "frames": len(frames),
                "url": "/static/clips/" + rid + ".mp4" if ok else ""}
    return {"ok": False, "error": "no recording"}

@app.get("/api/clips")
def clips():
    out = []
    for n in sorted(os.listdir(REC_DIR), reverse=True):
        if n.endswith(".mp4"):
            out.append({"name": n, "url": "/static/clips/" + n,
                        "size": os.path.getsize(os.path.join(REC_DIR, n))})
    return out

@app.post("/api/frame")
async def push_frame(data: dict):
    """Agent 上报一帧（hex），录制中则入片"""
    raw = bytes.fromhex(data.get("data", "")) if data.get("data") else b""
    if raw:
        LATEST_FRAME[data.get("id", "anon")] = raw
        for k in REC:
            REC[k].append(raw)
    return {"ok": True}

@app.get("/api/files")
def files(path: str = Query("/")):
    base = os.path.expanduser("~")
    t = base if path in ("/", "", "~") else os.path.join(base, path.lstrip("/"))
    try:
        if not os.path.isdir(t):
            t = base
        items = []
        for n in sorted(os.listdir(t))[:200]:
            p = os.path.join(t, n)
            items.append({"name": n, "type": "dir" if os.path.isdir(p) else "file",
                          "size": 0 if os.path.isdir(p) else os.path.getsize(p)})
        return {"path": t, "items": items}
    except Exception as e:
        return JSONResponse(status_code=200, content={"path": t, "items": [], "error": str(e)})


@app.get("/api/file")
def file(path: str = Query("")):
    return JSONResponse(status_code=404, content={"error": "not implemented in sandbox"})


KEY_MAP = {"home": "HOME", "back": "BACK", "recent": "APP_SWITCH", "menu": "MENU",
           "power": "POWER", "up": "UP", "down": "DOWN", "left": "LEFT",
           "right": "RIGHT", "ok": "ENTER", "vol+": "VOLUME_UP", "vol-": "VOLUME_DOWN"}


async def _dispatch(action: str, **kw):
    """把指令投给在线 Agent；无人在线则返回 executed=False"""
    if not AGENTS:
        _log(f"cmd {action} 排队（无 Agent）")
        return {"ok": True, "executed": False, "action": action, "note": "no agent online"}
    target = list(AGENTS)[0]
    cid = str(time.time())
    loop = asyncio.get_event_loop()
    CMDS[cid] = loop.create_future()
    try:
        await AGENTS[target].send_json({"type": "cmd", "id": cid, "action": action, **kw})
        await asyncio.wait_for(CMDS[cid], timeout=8)
        _log(f"cmd {action} 已由 {target} 执行")
        return {"ok": True, "executed": True, "action": action, "cmd_id": cid}
    except Exception as e:
        return {"ok": False, "executed": False, "action": action, "error": str(e)}
    finally:
        CMDS.pop(cid, None)


@app.post("/api/control")
@app.get("/api/control")
async def control(command: str = Query("home"), value: str = Query("")):
    c = (command or "").strip()
    if c.lower() in KEY_MAP:
        return await _dispatch("key", code=KEY_MAP[c.lower()])
    if c == "brightness":
        return await _dispatch("brightness", value=int(value or 80))
    if c == "clip":
        return await _dispatch("clip", text=value)
    if c == "open":
        return await _dispatch("open", url=value)
    if c == "toast":
        return await _dispatch("toast", text=value)
    if c == "tts":
        return await _dispatch("tts", text=value)
    if c == "shake":
        return await _dispatch("shake")
    if c == "flash":
        return await _dispatch("flash")
    if c == "shot":
        return await _dispatch("shot")
    if c == "draw":
        return await _dispatch("draw", stroke=value or "")
    if c == "shell":
        return await _dispatch("shell", cmd=value)
    return await _dispatch("shell", cmd=c)


@app.post("/api/shell")
async def shell(req: Request):
    cmd = ""
    try:
        j = await req.json()
        cmd = str(j.get("cmd", ""))
    except Exception:
        pass
    r = await _dispatch("shell", cmd=cmd)
    if not AGENTS:
        r["note"] = "指令已记录，等待 Agent 接管后下发"
    return r


@app.get("/api/logs")
def api_logs():
    return LOGS[-100:]


@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket):
    await ws.accept()
    aid = ""
    try:
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")
            if t == "register":
                aid = str(msg.get("id") or f"dev-{id(ws)}")
                AGENTS[aid] = ws
                await ws.send_json({"type": "registered", "id": aid})
                _log(f"Agent 上线：{aid}")
            elif t == "frame":
                if aid:
                    FRAMES[aid] = bytes.fromhex(msg.get("data", ""))[:400000]
            elif t == "result":
                fut = CMDS.get(msg.get("id"))
                if fut and not fut.done():
                    fut.set_result(msg)
            elif t == "log":
                _log(f"[{aid or 'agent'}] {msg.get('text', '')}")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if aid:
            AGENTS.pop(aid, None)
            FRAMES.pop(aid, None)
            _log(f"Agent 离线：{aid}")


@app.websocket("/ws/panel")
async def ws_panel(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json({"type": "agents", "list": list(AGENTS), "health": _health()})
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass


@app.get("/ui")
def ui_alias():
    return RedirectResponse("/panel/")


@app.get("/")
def root():
    if os.path.exists(os.path.join(STATIC_DIR, "index.html")):
        return RedirectResponse("/panel/")
    return _health()


@app.get("/panel/")
@app.get("/panel")
def panel():
    p = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(p):
        return FileResponse(p, media_type="text/html")
    return JSONResponse(status_code=404, content={"error": "panel not found"})
