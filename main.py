import os, time, io
from datetime import datetime, timezone
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont

app = FastAPI(title="Cinder Brain Ops")
START_TS = time.time()

# ---- 静态前端：static/index.html ----
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "health": "/healthz",
        "ui": "/panel/",
        "time": datetime.now(timezone.utc).isoformat(),
        "uptime_s": int(time.time() - START_TS),
        "screenshot": True,
    }

# 兼容前端多路径探测：/api/healthz、/api/health、/api/status 都指向同一份
@app.get("/api/healthz")
@app.get("/api/health")
@app.get("/api/status")
def api_health():
    return healthz()

@app.get("/api/devices")
def devices():
    return [{"id": "local", "name": "Local Node", "status": "online", "platform": "render-sandbox"}]

#  实时画面：GET /api/screenshot  返回 image/jpeg
@app.get("/api/screenshot")
def screenshot():
    img = Image.new("RGB", (720, 1280), (18, 20, 28))
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34)
    except Exception:
        f = ImageFont.load_default()
    d.text((40, 560), "Cinder // Remote", fill=(120, 130, 160), font=f)
    d.text((40, 610), "no display attached", fill=(80, 88, 110), font=f)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/jpeg")

#  文件浏览：GET /api/files?path=/sdcard
@app.get("/api/files")
def files(path: str = Query("/")):
    base = os.path.expanduser("~")
    target = base if path in ("/", "", "~") else os.path.join(base, path.lstrip("/"))
    try:
        if not os.path.isdir(target):
            target = base
        items = []
        for name in sorted(os.listdir(target))[:200]:
            p = os.path.join(target, name)
            items.append({"name": name, "type": "dir" if os.path.isdir(p) else "file",
                          "size": 0 if os.path.isdir(p) else os.path.getsize(p)})
        return {"path": target, "items": items}
    except Exception as e:
        return JSONResponse(status_code=200, content={"path": target, "items": [], "error": str(e)})

@app.get("/api/file")
def file(path: str = Query("")):
    return JSONResponse(status_code=404, content={"error": "not implemented in sandbox"})

#  控制指令：POST /api/control  {"command":"home"|"lock"|"reboot"}
@app.post("/api/control")
@app.get("/api/control")
def control(command: str = Query("home")):
    return {"ok": True, "command": command, "executed": False, "note": "sandbox stub"}

@app.get("/ui")
def ui_alias():
    return RedirectResponse("/panel/")

@app.get("/")
def root():
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
