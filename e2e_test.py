"""端到端自测：起服务 -> 注册模拟 Agent -> 验健康/画面/指令 -> 打印结论"""
import subprocess, time, sys, urllib.request, json, os

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 8011
BASE = f"http://127.0.0.1:{PORT}"

srv = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
agent = None
try:
    time.sleep(4)
    if srv.poll() is not None:
        print("服务启动失败:\n", srv.stdout.read()[-1500:]); raise SystemExit(1)

    def get(p):
        with urllib.request.urlopen(BASE + p, timeout=8) as r:
            return r.status, r.read()

    ok = True
    # 1 面板
    s, b = get("/panel/")
    html = b.decode(); print(f"[1] GET /panel/ -> {s}, {len(b)} bytes")
    ok &= (s == 200 and "Cinder" in html)
    # 2 前端是否还写死旧域名
    bad = "cinder-brain-ops.onrender.com" in html or "onrender.com" in html
    print(f"[2] 前端域名自适应 -> {'❌ 仍写死域名' if bad else '✅ location.origin'}")
    ok &= (not bad)
    # 3 四个健康路径全 GET
    for p in ["/healthz", "/api/health", "/api/healthz", "/api/status"]:
        s, b = get(p); d = json.loads(b)
        print(f"[3] GET {p} -> {s} ok={d.get('ok')}")
        ok &= (s == 200 and d.get("ok") is True)
    # 4 占位图
    s, b = get("/api/screenshot")
    print(f"[4] GET /api/screenshot -> {s} image/jpeg {len(b)}B")
    ok &= (s == 200 and b[:2] == b"\xff\xd8")
    s, b = get("/screenshot"); print(f"    GET /screenshot -> {s} {len(b)}B")
    # 5 根路径跳转
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    op = urllib.request.build_opener(NoRedirect)
    try: op.open(BASE + "/", timeout=5)
    except urllib.error.HTTPError as e: print(f"[5] GET / -> {e.code} (跳转)")
    # 6 Agent 注册 + 指令
    agent = subprocess.Popen([sys.executable, "fake_agent.py", f"ws://127.0.0.1:{PORT}"],
                             cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(3)
    s, b = get("/api/devices"); print(f"[6] GET /api/devices -> {json.loads(b)}")
    req = urllib.request.Request(BASE + "/api/control?command=back", method="POST")
    with urllib.request.urlopen(req, timeout=12) as r:
        d = json.loads(r.read())
    print(f"[7] POST /api/control?command=back -> {d}")
    ok &= (d.get("ok") is True)
    print("    注：无 Agent 在线时 executed=False 属预期行为")
    s, b = get("/api/screenshot"); print(f"[8] Agent 帧 -> {s} {len(b)}B")
    print("\n==== 结果:", "✅ 全部通过" if ok else "❌ 有失败项", "====\n")
    if agent.stdout: print("[agent 输出]", (agent.stdout.read() or "").strip()[:400])
finally:
    for p in (agent, srv):
        if p:
            p.terminate()
            try: p.wait(timeout=5)
            except Exception: p.kill()
