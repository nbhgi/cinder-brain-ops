import json, time, asyncio
try:
    from fastapi import WebSocket, WebSocketDisconnect
except Exception:
    WebSocket = None
    WebSocketDisconnect = None

AGENTS, PANELS, FRAMES, CMDS = {}, set(), {}, []

def _now(): return int(time.time())
def _log(m): print("[wss_bridge] %s" % m, flush=True)

async def _run(action, d, aid=""):
    CMDS.append({"action": action, "ts": _now()})
    if len(CMDS) > 500: del CMDS[:200]
    if not action: return {"ok": False}
    try:
        import main as _m
        dp = getattr(_m, "_dispatch", None) or getattr(_m, "dispatch", None)
    except Exception:
        dp = None
    args = d.get("args", d.get("value", d.get("text", d.get("cmd", d.get("url", "")))))
    if dp:
        try:
            r = dp(action, _agent=aid, value=args, text=args, cmd=args, url=args)
            if asyncio.iscoroutine(r): r = await r
            return r if isinstance(r, dict) else {"ok": True}
        except Exception as e:
            _log("dispatch: %s" % e)
    return {"ok": True, "executed": False}

async def _handle(ws, role, aid):
    if role == "agent" and aid:
        AGENTS[aid] = ws; _log("agent online: %s" % aid)
    elif role == "panel":
        PANELS.add(ws)
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=25)
            except asyncio.TimeoutError:
                try: await ws.send_text(json.dumps({"t":"ping","ts":_now()}))
                except Exception: break
                continue
            if not raw: continue
            try: msg = json.loads(raw)
            except Exception: continue
            if not isinstance(msg, dict): continue
            if msg.get("t") == "ping" or msg.get("type") == "ping":
                try: await ws.send_text(json.dumps({"t":"pong","ts":_now()}))
                except Exception: break
                continue
            t = msg.get("t") or msg.get("type") or ""
            d = msg.get("d") if isinstance(msg.get("d"), dict) else {}
            if t in ("frame","shot","screenshot") and (d.get("b64") or d.get("image") or msg.get("b64")):
                FRAMES[aid or "phone01"] = {"b64": d.get("b64") or d.get("image") or msg.get("b64"), "ts": _now()}
                continue
            if t in ("cmd","command","action","input") or msg.get("action"):
                a = d.get("action") or msg.get("action") or ""
                r = await _run(str(a), d, aid)
                try: await ws.send_text(json.dumps({"t":"result","d":{"action":a,**r}}, ensure_ascii=False))
                except Exception: pass
                continue
            if t == "stream":
                tg = AGENTS.get(aid) or (list(AGENTS.values())[0] if AGENTS else None)
                if tg:
                    try: await tg.send_text(json.dumps({"t":"stream","d":{"on":bool(d.get("on",True)),"fps":8,"w":540}}))
                    except Exception: pass
    except Exception as e:
        if WebSocketDisconnect is None or not isinstance(e, WebSocketDisconnect):
            _log("loop: %s" % e)
    finally:
        if role == "agent" and aid and AGENTS.get(aid) is ws: AGENTS.pop(aid, None)
        elif role == "panel": PANELS.discard(ws)

def register(app):
    if WebSocket is None:
        _log("no fastapi websocket"); return

    @app.get("/api/agents")
    async def _agents():
        if AGENTS: return {"agents": list(AGENTS.keys()), "ts": _now()}
        try:
            import main as _m
            dv = getattr(_m, "DEVICES", None) or getattr(_m, "AGENTS", None)
        except Exception:
            dv = None
        if isinstance(dv, dict) and dv: return {"agents": list(dv.keys()), "ts": _now()}
        if isinstance(dv, list) and dv: return {"agents": [str(x) for x in dv], "ts": _now()}
        return {"agents": [], "ts": _now()}

    @app.websocket("/ws")
    async def _ws(ws):
        await ws.accept()
        await _handle(ws, ws.query_params.get("role","panel"),
                      ws.query_params.get("id","") or ws.query_params.get("agent",""))

    @app.websocket("/api/agents/ws")
    async def _ws2(ws):
        await ws.accept()
        await _handle(ws, ws.query_params.get("role","panel"),
                      ws.query_params.get("id","") or ws.query_params.get("agent",""))

    _log("registered: /api/agents /ws /api/agents/ws")
