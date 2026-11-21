# -*- coding: utf-8 -*-
"""
Cinder WebSocket 兼容层 —— 给 APK 用的统一端点 /ws

这一个文件同时解决两件事：
1. 提供 /ws?role=agent|client&id=xxx （APK 的 WsClient 连这个）
   协议用 {t, d} 简写，内部自动翻译成后端既有协议。
2. 兜底提供 /api/agents。线上 /healthz 返回 {"agents":0,"drop":0,"frames":0,"photos":{}}
   说明跑的是旧版 main.py（没有 /api/agents），只要末尾 import 本模块，
   路由就会自动补上，不用改 main.py 任何一行。

挂载方式（main.py 末尾加两行即可）：
    from ws_compat import register as _ws_compat_register
    _ws_compat_register(app)
"""
import os
import io
import time
import json
import uuid
import base64
import asyncio

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

try:  # PIL 可选，缺了只影响占位图
    from PIL import Image as _Image
except Exception:  # pragma: no cover
    _Image = None

# ---------------- 全局容器（与 main.py 的 DEVICES 打通） ----------------
CLIENTS: set = set()                 # 面板浏览器连接
ALT: dict = {}                       # alt_id -> ws（APK 用 phone01 这类 id）
FRAMES: dict = {}                    # device_id -> 最近一帧 bytes
FRAME_AT: dict = {}                  # device_id -> 时间戳
CMDS: dict = {}                      # cmd_id -> 回执
_LOG_BUF: list = []

# 旧版 main.py 没有 IN_MEM / STATIC_DIR，这里自行兜底
IN_MEM = {"frames": FRAMES, "photos": {}, "drop": 0}
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 与 main.py 的 CMD_MAP 对齐；import 失败则用内置表，保证独立可跑
try:
    from main import CMD_MAP as _CMD_MAP  # type: ignore
except Exception:
    _CMD_MAP = None

FALLBACK_CMD_MAP = {
    "home": {"action": "key", "code": "HOME"},
    "back": {"action": "key", "code": "BACK"},
    "recents": {"action": "key", "code": "APP_SWITCH"},
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
    "camera": {"action": "key", "code": "CAMERA"},
}

PING_EVERY = 20          # 秒；Render 免费版 15 分钟无数据会掐线
MAX_FRAME_B64 = 3_000_000


def _log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    _LOG_BUF.append(line)
    if len(_LOG_BUF) > 200:
        del _LOG_BUF[:-200]
    try:
        from main import _log as _main_log  # type: ignore
        _main_log(msg)
        return
    except Exception:
        pass
    print(line, flush=True)


def _cmd_map() -> dict:
    if _CMD_MAP:
        return _CMD_MAP
    try:
        import main as _m  # type: ignore
        if getattr(_m, "CMD_MAP", None):
            return _m.CMD_MAP
    except Exception:
        pass
    return FALLBACK_CMD_MAP


def _devices():
    """取后端 DEVICES；取不到返回空表"""
    try:
        import main as _m  # type: ignore
        return getattr(_m, "DEVICES", {}) or {}
    except Exception:
        return {}


def _primary_agent_ws():
    """取当前在线的 Agent 连接：优先 DEVICES，其次 /ws 自己登记的 ALT"""
    for d in _devices().values():
        ws = d.get("ws")
        if ws is not None:
            return ws
    for ws in ALT.values():
        if ws is not None:
            return ws
    return None


def _note_agent(ws):
    """把 /ws 连进来的 Agent 记进 DEVICES，让 /api/agents 与 /healthz 口径一致"""
    try:
        import main as _m  # type: ignore
        devices = getattr(_m, "DEVICES", None)
        if devices is None:
            return
        key = os.environ.get("CINDER_DEFAULT_DEVICE", "phone01")
        d = devices.get(key) or {"id": key, "token": getattr(_m, "AGENT_TOKEN", "cinder-dev-token")}
        d.update({"name": d.get("name", key), "platform": "android",
                  "connected_at": d.get("connected_at") or time.time(),
                  "last_seen": time.time(), "ws_via": "ws_compat"})
        if not d.get("ws_attached"):
            d["ws"] = ws
            d["ws_attached"] = True
        devices[key] = d
    except Exception:
        pass


def _drop_agent(ws):
    try:
        import main as _m  # type: ignore
        for d in (getattr(_m, "DEVICES", {}) or {}).values():
            if d.get("ws") is ws:
                d["ws"] = None
                d["last_seen"] = time.time()
    except Exception:
        pass


# ---------------- 占位图（没有真实帧时给面板一张） ----------------
def _placeholder() -> bytes:
    if _Image is None:
        return b""
    try:
        img = _Image.new("RGB", (540, 960), (18, 18, 24))
        from PIL import ImageDraw, ImageFont
        dr = ImageDraw.Draw(img)
        dr.text((24, 440), "CINDER · waiting for APK",
                fill=(120, 230, 180), font_size=26)
        dr.text((24, 480), "no agent connected", fill=(110, 110, 120), font_size=18)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return buf.getvalue()
    except Exception:
        return b""


# ---------------- 指令下发：复用后端既有 _dispatch，没有就自己发 ----------------
async def dispatch(action: str, _agent: str = "", **kw) -> dict:
    if not action:
        return {"ok": False, "note": "empty action"}
    target = _agent
    try:
        import main as _m  # type: ignore
        fn = getattr(_m, "_dispatch", None)
        if callable(fn):
            return await fn(action, _agent, **kw)
        devices = getattr(_m, "DEVICES", {}) or {}
        if not target:
            for k, v in devices.items():
                if v.get("ws") is not None:
                    target = k
                    break
        d = devices.get(target)
        ws = d.get("ws") if d else None
        if ws is None:
            ws = _primary_agent_ws()
        if ws is None:
            return {"ok": True, "executed": False,
                    "note": "no agent online", "action": action}
        amap = getattr(_m, "CMD_MAP", None) or FALLBACK_CMD_MAP
        payload = amap.get(action, {"action": "key", "code": str(action).upper()})
        if action in ("tap", "swipe"):
            payload = {"action": action, **kw}
        msg = {"type": "command", "id": uuid.uuid4().hex[:8], "action": payload}
        await ws.send_json(msg)
        if d is not None:
            d["pending"] = None
        CMDS[msg["id"]] = {"ok": True, "action": action, "at": time.time()}
        return {"ok": True, "executed": True, "cmd_id": msg["id"],
                "action": action, "note": "dispatched"}
    except Exception as e:
        return {"ok": False, "executed": False, "error": str(e), "action": action}


# ---------------- 核心：/ws 网关 ----------------
async def _send(ws, obj) -> bool:
    try:
        await ws.send_json(obj)
        return True
    except Exception:
        return False


async def _broadcast(obj):
    dead = []
    for c in list(CLIENTS):
        if not await _send(c, obj):
            dead.append(c)
    for d in dead:
        CLIENTS.discard(d)


async def _pinger(ws, role):
    while True:
        await asyncio.sleep(PING_EVERY)
        if not await _send(ws, {"t": "ping", "ts": int(time.time())}):
            break


def _norm_args(d: dict) -> str:
    """关键修复：参数可能塞在 args/value/text/cmd/url/number 任一字段里"""
    for k in ("args", "value", "text", "cmd", "url", "number", "data"):
        if d.get(k) not in (None, ""):
            v = d.get(k)
            return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return ""


async def _handle_agent(ws, aid: str):
    ALT[aid] = ws
    _note_agent(ws)
    await _send(ws, {"t": "hello", "ok": True, "id": aid})
    await _broadcast({"t": "agent", "d": {"online": True, "id": aid}})
    _log(f"agent online: {aid}")
    try:
        while True:
            msg = await ws.receive_json()
            t = msg.get("t") or msg.get("type")
            d = msg.get("d") if isinstance(msg.get("d"), dict) else {}
            if t == "pong":
                continue
            if t == "frame":
                b64 = d.get("b64") or msg.get("b64") or d.get("data")
                if b64:
                    try:
                        raw = base64.b64decode(b64)
                        if len(raw) < MAX_FRAME_B64 and raw:
                            FRAMES[aid] = raw
                            FRAME_AT[aid] = time.time()
                            IN_MEM["frames"][aid] = raw
                    except Exception:
                        pass
                await _broadcast({"t": "frame", "d": {
                    "id": aid, "b64": b64, "ts": int(time.time())}})
            elif t == "result":
                CMDS[msg.get("id", "")] = {"ok": True, "d": d}
                await _broadcast({"t": "result", "d": d})
            elif t == "log":
                _log(f"[{aid}] {d.get('msg', '')}")
                await _broadcast({"t": "log", "d": d})
            elif t == "clipboard":
                await _broadcast({"t": "clipboard", "d": d})
            elif t == "stats":
                await _broadcast({"t": "stats", "d": d})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ALT.pop(aid, None)
        _drop_agent(ws)
        FRAME_AT.pop(aid, None)
        await _broadcast({"t": "agent", "d": {"online": False, "id": aid}})
        _log(f"agent offline: {aid}")


async def _handle_client(ws):
    CLIENTS.add(ws)
    # 上线先补一帧，面板不至于黑屏
    try:
        snap = None
        for k in (list(FRAMES.keys()) or [None]):
            if k and FRAMES.get(k):
                snap = FRAMES[k]
                break
        if snap:
            await _send(ws, {"t": "frame", "d": {
                "b64": base64.b64encode(snap).decode(), "ts": int(time.time())}})
    except Exception:
        pass
    try:
        while True:
            msg = await ws.receive_json()
            t = msg.get("t") or msg.get("type")
            d = msg.get("d") if isinstance(msg.get("d"), dict) else {}
            aid = msg.get("agent") or d.get("id") or ""
            if t == "ping":
                await _send(ws, {"t": "pong"})
            elif t == "cmd":
                action = d.get("action") or msg.get("action") or ""
                args = _norm_args(d)
                r = await dispatch(action, aid,
                                   value=args, text=args, cmd=args,
                                   url=args, number=args)
                await _send(ws, {"t": "result", "d": {"cmd": action, **r}})
            elif t == "shot":
                r = await dispatch("screenshot", aid)
                if not r.get("executed"):
                    raw = _placeholder()
                    if raw:
                        await _send(ws, {"t": "frame", "d": {
                            "b64": base64.b64encode(raw).decode(),
                            "stale": True,
                            "ts": int(time.time())}})
                await _send(ws, {"t": "result", "d": {"cmd": "shot", **r}})
            elif t == "stream":
                on = bool(d.get("on", True))
                if on:
                    r = await dispatch("stream_start", aid)
                else:
                    r = await dispatch("stream_stop", aid)
                await _send(ws, {"t": "result", "d": {"cmd": "stream", **r}})
            elif t == "input":
                action = d.get("action") or "input"
                args = _norm_args(d)
                r = await dispatch(action, aid, text=args, value=args)
                await _send(ws, {"t": "result", "d": {"cmd": action, **r}})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        CLIENTS.discard(ws)


def _make_ws_handler():
    async def ws_gateway(ws: WebSocket):
        await ws.accept()
        role = ws.query_params.get("role", "client")
        aid = ws.query_params.get("id", "phone01") or "phone01"
        if role not in ("agent", "client"):
            await ws.close(code=4400)
            return
        ping = asyncio.create_task(_pinger(ws, role))
        try:
            if role == "agent":
                await _handle_agent(ws, aid)
            else:
                await _handle_client(ws)
        finally:
            ping.cancel()
            if role == "agent":
                ALT.pop(aid, None)
                _drop_agent(ws)
            CLIENTS.discard(ws)
            try:
                await ws.close()
            except Exception:
                pass
    return ws_gateway


def _make_agents_handler():
    def api_agents():
        ids = set()
        for d in _devices().values():
            if d.get("ws") is not None:
                ids.add(d.get("id", "?"))
        ids.update(ALT.keys())
        return {"agents": sorted(ids), "ts": int(time.time())}
    return api_agents


def _make_frames_handler():
    def api_frames():
        return {"frames": {k: len(v or b"") for k, v in FRAMES.items()},
                "at": {k: int(v) for k, v in FRAME_AT.items()}}
    return api_frames


def register(app, *_, **__):
    """挂在 app 上。多余参数一律吞掉，兼容旧版 8 参数调用方式。"""
    app.websocket("/ws")(_make_ws_handler())
    app.get("/api/agents")(_make_agents_handler())
    app.get("/api/frames")(_make_frames_handler())
    _log("ws_compat registered: /ws  /api/agents  /api/frames")
    return app
