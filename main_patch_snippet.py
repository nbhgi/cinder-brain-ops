# ===== 下面是需要在 main.py 中追加/修改的部分（对照行号）=====
#
# [1] 第 10 行 AGENTS 定义之后，新增 CLIENTS 集合：
#       CLIENTS: set[WebSocket] = set()
#
# [2] 第 14 行 LOGS 之后，追加：
#       _EXTRA = {}
#
# [3] 第 159 行 _dispatch 签名改为（增加 _agent 参数，指定下发目标）：
#       async def _dispatch(action: str, _agent: str = "", **kw):
#            target = _agent or (list(AGENTS)[0] if AGENTS else "")
#            if not target:
#                _log(f"cmd {action} 排队（无 Agent）")
#                return {"ok": True, "executed": False, "action": action, "note": "no agent online"}
#            ... 其余保持原样，把 CMDS[cid] 的等待逻辑不变
#
# [4] 文件末尾（最后一个路由之后）追加两行注册：
#       from ws_compat import register as _ws_compat_register
#       _ws_compat_register(app, lambda: AGENTS, lambda: FRAMES, lambda: CMDS,
#                           lambda: REC, lambda: _log, lambda: _dispatch, lambda: _health)
