# -*- coding: utf-8 -*-
"""Cinder Brain Ops —— 后端主程序（含 APK 对接补丁·修复版）

相对你的上一版的改动只有 3 处，其余逻辑一行没动：
1. 末尾挂载改为 wss_bridge 优先、ws_compat 兜底（两个都试，哪个成功用哪个）
2. 新增 /api/debug 自检端点（一眼看出挂载状态和路由清单）
3. /healthz 增加 bridge 字段，标明当前用的是哪套网关

原有的 /healthz、/api/devices、/api/agents、/api/screenshot、
/api/control、/api/agent/frame、/ws/agent/{id}、/ws/panel 全部保留。
"""
import os
import io
import json
import time
import uuid
import asyncio
import base64
from datetime import datetime, timezone
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (JSONResponse, StreamingResponse,
                               RedirectResponse, FileResponse)
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont

app = FastAPI(title="Cinder Brain Ops")
START_TS = time.time()
VERSION = "1.5.9-apk"
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "cinder-dev-token")

# ---------- 设备 / 连接注册表 ----------
# devices[device_id] = {
#   "id","name","platform","token","connected_at","last_seen",
#   "ws": WebSocket, "frame": bytes|None, "frame_at": float, "result": {...}|None
# }
DEVICES: dict = {}
CLIENTS: set = set()                 # 面板浏览器连接（ws_compat 也用）
FRAMES: dict = {}                    # device_id -> 最近一帧 bytes
CMDS: dict = {}                      # cmd_id -> 回执
IN_MEM = {"frames": FRAMES, "photos": {}, "drop": 0}
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 指令别名表：面板按键 -> Agent 动作
CMD_MAP = {
    "home": {"action": "key", "code": "HOME"},
    "back": {"action": "key", "code": "BACK"},
    "recents": {"action": "key", "code": "APP_SWITCH"},
    "recent": {"action": "key", "code": "APP_SWITCH"},
    "menu": {"action": "key", "code": "MENU"},
    "power": {"action": "key", "code": "POWER"},
    "lock": {"action": "key", "code": "LOCK"},
    "wake": {"action": "key", "code": "WAKE"},
    "volume_up": {"action": "key", "code": "VOLUME_UP"},
    "volume_down": {"action": "key", "code": "VOLUME_DOWN"},
    "brightness_up": {"action": "key", "code": "BRIGHTNESS_UP"},
    "brightness_down": {"action": "key", "code": "BRIGHTNESS_DOWN"},
    "screenshot": {"action": "key", "code": "SCREENSHOT"},
    "notification": {"action": "key", "code": "NOTIFICATION"},
    "notifications": {"action": "key", "code": "NOTIFICATION"},
    "camera": {"action": "key", "code": "CAMERA"},
    "stream_start": {"action": "stream", "on": True},
    "stream_stop": {"action": "stream", "on": False},
}

BRIDGE = {"name": "none", "ws": False, "agents_api": False, "error": ""}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


_LOG_BUF: list = []


def _log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    _LOG_BUF.append(line)
    if len(_LOG_BUF) > 200:
        del _LOG_BUF[:-200]
    print(line, flush=True)


def public_dev(d: dict, online: bool):
    return {"id": d["id"], "name": d.get("name", d["id"]),
            "platform": d.get("platform", "android"),
            "status": "online" if online else "offline",
            "last_seen": d.get("last_seen")}


# ---------- 健康检查 ----------
@app.get("/healthz")
def healthz():
    online = sum(1 for d in DEVICES.values() if d.get("ws") is not None)
    return {"ok": True, "health": "/healthz", "ui": "/panel/",
            "version": VERSION, "ts": int(time.time()),
            "time": now_iso(), "uptime_s": int(time.time() - START_TS),
            "screenshot": True, "agents": online,
            "drop": IN_MEM["drop"], "frames": len(FRAMES),
            "photos": IN_MEM["photos"], "devices": len(DEVICES),
            "bridge": BRIDGE["name"]}


@app.get("/api/healthz")
@app.get("/api/health")
@app.get("/api/status")
def api_health():
    return healthz()


# ---------- 部署自检（新增，用于一眼确认 /ws 是否挂上）----------
@app.get("/api/debug")
def api_debug():
    paths = sorted({getattr(r, "path", "") for r in app.routes})
    return {"ok": True, "version": VERSION, "bridge": BRIDGE,
            "devices": len(DEVICES), "frames": len(FRAMES),
            "ws_routes": [p for p in paths if p.startswith("/ws")],
            "routes": paths, "ts": int(time.time())}


# ---------- 设备管理 ----------
@app.get("/api/devices")
def devices():
    return {"devices": [public_dev(d, d.get("ws") is not None)
                        for d in DEVICES.values()],
            "ts": int(time.time())}


@app.get("/api/agents")
def api_agents():
    """APK 在线列表。网关挂载后会用自己的实现覆盖这一条。"""
    ids = sorted(k for k, v in DEVICES.items() if v.get("ws") is not None)
    return {"agents": ids, "ts": int(time.time())}


# ---------- APK 上报单帧 ----------
@app.post("/api/agent/frame/{device_id}")
async def agent_frame(device_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "bad json"}
    raw = None
    if body.get("b64"):
        try:
            raw = base64.b64decode(body["b64"])
        except Exception:
            raw = None
    if raw is None and body.get("data"):
        try:
            raw = base64.b64decode(body["data"])
        except Exception:
            raw = None
    if not raw:
        return {"ok": False, "error": "no frame"}
    d = DEVICES.get(device_id) or {"id": device_id, "token": AGENT_TOKEN}
    d["frame"] = raw
    d["frame_at"] = time.time()
    d["last_seen"] = time.time()
    DEVICES[device_id] = d
    FRAMES[device_id] = raw
    return {"ok": True, "bytes": len(raw)}


# ---------- 旧版 Agent 长连接（保留，兼容原 Python Agent） ----------
@app.websocket("/ws/agent/{device_id}")
async def ws_agent(ws: WebSocket, device_id: str):
    token = ws.query_params.get("token", "")
    if token != AGENT_TOKEN:
        await ws.close(code=4401)
        return
    await ws.accept()
    name = ws.query_params.get("name", device_id)
    platform = ws.query_params.get("platform", "android")
    d = DEVICES.get(device_id) or {"id": device_id, "token": AGENT_TOKEN}
    d.update({"name": name, "platform": platform,
              "connected_at": d.get("connected_at") or time.time(),
              "last_seen": time.time()})
    DEVICES[device_id] = d
    old = d.get("ws")
    if old is not None and old is not ws:
        try:
            await old.close(code=4000)
        except Exception:
            pass
    d["ws"] = ws
    pending = d.pop("pending", None)
    if pending:
        try:
            await ws.send_json(pending)
        except Exception:
            d["pending"] = pending

    async def reader():
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if "bytes" in msg and msg["bytes"]:
                d["frame"] = msg["bytes"]
                d["frame_at"] = time.time()
                FRAMES[device_id] = msg["bytes"]
            elif "text" in msg and msg["text"]:
                try:
                    obj = json.loads(msg["text"])
                except Exception:
                    continue
                if obj.get("type") == "frame" and obj.get("data"):
                    try:
                        d["frame"] = base64.b64decode(obj["data"])
                        d["frame_at"] = time.time()
                        FRAMES[device_id] = d["frame"]
                    except Exception:
                        pass
                elif obj.get("type") == "result":
                    d["result"] = obj
    try:
        await reader()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if d.get("ws") is ws:
            d["ws"] = None
            d["last_seen"] = time.time()


# ---------- 控制面板长连接（旧协议，保留） ----------
@app.websocket("/ws/panel")
async def ws_panel(ws: WebSocket):
    await ws.accept()
    CLIENTS.add(ws)
    try:
        while True:
            await ws.send_json({"type": "devices", "data": [
                public_dev(d, d.get("ws") is not None)
                for d in DEVICES.values()]})
            await asyncio.sleep(2)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        CLIENTS.discard(ws)


# ---------- 截屏 ----------
def _placeholder() -> bytes:
    try:
        img = Image.new("RGB", (540, 960), (18, 18, 24))
        dr = ImageDraw.Draw(img)
        dr.text((24, 440), "CINDER · waiting for APK",
                fill=(120, 230, 180), font_size=26)
        dr.text((24, 480), "no agent connected", fill=(110, 110, 120), font_size=18)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return buf.getvalue()
    except Exception:
        return b""


@app.get("/api/screenshot")
def screenshot(device: str = Query("local")):
    raw = FRAMES.get(device) or (DEVICES.get(device) or {}).get("frame")
    if not raw:
        raw = _placeholder()
        IN_MEM["drop"] += 1
    if not raw:
        return JSONResponse(status_code=503, content={"ok": False, "error": "no frame"})
    return StreamingResponse(io.BytesIO(raw), media_type="image/jpeg")


# ---------- 文件（仅演示，未接真实 FS） ----------
@app.get("/api/files")
def files(path: str = Query("/")):
    return {"path": path, "entries": [], "ok": False,
            "note": "file api needs root agent"}


@app.get("/api/file")
def file(path: str = Query("")):
    return {"path": path, "ok": False, "note": "file api needs root agent"}


# ---------- 指令下发 ----------
async def _dispatch(action: str, _agent: str = "", **kw) -> dict:
    """把指令发给指定设备；_agent 为空时发给当前唯一在线设备。"""
    if not action:
        return {"ok": False, "note": "empty action"}
    target = _agent
    if not target:
        for k, v in DEVICES.items():
            if v.get("ws") is not None:
                target = k
                break
    d = DEVICES.get(target) if target else None
    ws = d.get("ws") if d else None
    if ws is None:
        _log(f"cmd {action} 排队（无 Agent）")
        return {"ok": True, "executed": False, "action": action,
                "note": "no agent online"}
    payload = CMD_MAP.get(action, {"action": "key", "code": str(action).upper()})
    if action in ("tap", "swipe"):
        payload = {"action": action, **{k: v for k, v in kw.items()
                                        if k in ("x", "y", "x2", "y2", "duration")}}
    msg = {"type": "command", "id": uuid.uuid4().hex[:8], "action": payload}
    try:
        await ws.send_json(msg)
        d["pending"] = None
        CMDS[msg["id"]] = {"ok": True, "action": action, "at": time.time()}
        _log(f"cmd {action} -> {target} ({msg['id']})")
        return {"ok": True, "command": action, "executed": True,
                "cmd_id": msg["id"], "note": "dispatched to agent"}
    except Exception as e:
        d["pending"] = msg
        return {"ok": False, "command": action, "executed": False,
                "error": str(e), "note": "agent unreachable, queued"}


@app.post("/api/control")
@app.get("/api/control")
async def control(request: Request, command: str = Query("home"),
                  device: str = Query("local"),
                  x: int = Query(0), y: int = Query(0)):
    try:
        if request.method == "POST":
            body = await request.json()
        else:
            body = {}
    except Exception:
        body = {}
    cmd = body.get("command") or command
    dev_id = body.get("device") or device
    if cmd in ("tap", "swipe"):
        return await _dispatch(cmd, dev_id, x=body.get("x", x),
                               y=body.get("y", y),
                               x2=body.get("x2", 0), y2=body.get("y2", 0))
    return await _dispatch(cmd, dev_id)


# ---------- 页面 ----------
@app.get("/ui")
def ui_alias():
    if os.path.exists(os.path.join(STATIC_DIR, "index.html")):
        return RedirectResponse("/panel/")
    return healthz()


@app.get("/")
def root():
    if os.path.exists(os.path.join(STATIC_DIR, "home.html")):
        return RedirectResponse("/home.html")
    if os.path.exists(os.path.join(STATIC_DIR, "index.html")):
        return RedirectResponse("/panel/")
    return healthz()


@app.get("/panel/")
@app.get("/panel")
def panel():
    p = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(p):
        return FileResponse(p, media_type="text/html")
    return JSONResponse(status_code=404, content={"error": "panel not found"})


@app.get("/home.html")
def home():
    p = os.path.join(STATIC_DIR, "home.html")
    if os.path.exists(p):
        return FileResponse(p, media_type="text/html")
    return JSONResponse(status_code=404, content={"error": "home not found"})


# ---------- APK 对接：WebSocket 统一网关 /ws ----------
# 放在文件最后。先试 wss_bridge（自带 /ws 与 /api/agents 兜底），
# 失败再退回 ws_compat。两套都缺失也不影响上面所有端点正常工作。
try:
    from wss_bridge import register as _bridge_register
    _bridge_register(app)
    BRIDGE.update({"name": "wss_bridge", "ws": True, "agents_api": True})
    _log("ws 网关已挂载（wss_bridge）：/ws  /api/agents  /api/agents/ws")
except Exception as _e1:
    BRIDGE["error"] = f"wss_bridge: {_e1}"
    try:
        from ws_compat import register as _ws_compat_register
        _ws_compat_register(app)
        BRIDGE.update({"name": "ws_compat", "ws": True, "agents_api": True})
        _log("ws 网关已挂载（ws_compat）：/ws  /api/agents  /api/frames")
    except Exception as _e2:
        BRIDGE["error"] += f" | ws_compat: {_e2}"
        _log(f"ws 网关挂载失败（/ws 不可用）：{BRIDGE['error']}")
