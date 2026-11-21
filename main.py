# main.py | Python 3.11+ / FastAPI / uvicorn
# Render 配置：
#   Build Command : pip install -r requirements.txt
#   Start Command : uvicorn main:app --host 0.0.0.0 --port $PORT
import os
import io
import glob
import platform
from datetime import datetime

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    RedirectResponse, JSONResponse, FileResponse, Response,
)
from fastapi.staticfiles import StaticFiles

APP_TITLE = "Cinder Brain Ops"
START_TIME = datetime.utcnow()

app = FastAPI(title=APP_TITLE)

# ---------- 跨域：前端与后端同域时这行无害，跨域时必需 ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- 可选依赖：截图用，缺了不影响启动 ----------
try:
    from PIL import ImageGrab
    HAS_GRAB = True
except Exception:          # pragma: no cover
    HAS_GRAB = False


# ============================================================
#  健康检查：前端状态灯打的就是这个
# ============================================================
@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "health": "/healthz",
        "ui": "/panel/",
        "time": datetime.utcnow().isoformat() + "Z",
        "uptime_s": int((datetime.utcnow() - START_TIME).total_seconds()),
        "screenshot": HAS_GRAB,
    }


# 兼容前端多路径探测：/api/healthz、/api/health、/api/status 都指向同一份
@app.get("/api/healthz")
@app.get("/api/health")
@app.get("/api/status")
def api_health():
    return healthz()


@app.get("/api/devices")
def devices():
    return [{
        "id": "local",
        "name": platform.node() or "本机设备",
        "status": "online",
        "platform": platform.platform(),
    }]


# ============================================================
#  实时画面：GET /api/screenshot  返回 image/jpeg
#  有 GUI 桌面时真截图；无桌面时返回一张占位图，保证不 500
# ============================================================
def _placeholder_jpeg(text: str = "NO DISPLAY") -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (720, 1280), (18, 20, 26))
    d = ImageDraw.Draw(img)
    d.text((24, 24), text, fill=(230, 230, 230))
    d.text((24, 60), "Cinder Brain Ops / screenshot placeholder",
           fill=(120, 126, 140))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


@app.get("/api/screenshot")
def screenshot():
    if not HAS_GRAB:
        return Response(_placeholder_jpeg("PIL UNAVAILABLE"),
                        media_type="image/jpeg")
    try:
        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return Response(buf.getvalue(), media_type="image/jpeg")
    except Exception as exc:                     # 无桌面环境会走到这里
        return Response(_placeholder_jpeg(f"NO DISPLAY: {exc}"),
                        media_type="image/jpeg")


# ============================================================
#  文件浏览：GET /api/files?path=/sdcard
# ============================================================
@app.get("/api/files")
def files(path: str = Query("/sdcard")):
    root = os.path.realpath(path if os.path.isdir(path) else "/")
    items = []
    try:
        for name in sorted(os.listdir(root))[:500]:
            full = os.path.join(root, name)
            items.append({
                "name": name,
                "path": full,
                "type": "dir" if os.path.isdir(full) else "file",
                "size": 0 if os.path.isdir(full) else _safe_size(full),
            })
    except Exception as exc:
        return JSONResponse({"path": root, "items": [], "error": str(exc)},
                            status_code=200)
    return {"path": root, "parent": os.path.dirname(root.rstrip("/")) or "/",
            "items": items}


def _safe_size(p: str) -> int:
    try:
        return os.path.getsize(p)
    except Exception:
        return 0


@app.get("/api/file")
def file_content(path: str = Query(...)):
    if not os.path.isfile(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)


# ============================================================
#  控制指令：POST /api/control  {"command":"home"|"lock"|"reboot"}
#  同时兼容 GET，方便手机上直接点链接调试
# ============================================================
@app.post("/api/control")
@app.get("/api/control")
async def control(payload: dict | None = None, command: str | None = Query(None)):
    cmd = command or (payload or {}).get("command") or (payload or {}).get("action")
    if not cmd:
        return JSONResponse({"ok": False, "error": "command required"},
                            status_code=400)
    return JSONResponse({"ok": True, "command": cmd,
                         "note": "received; bind to your device bridge"})


@app.get("/ui")
def ui_alias():
    return RedirectResponse("/panel/")


@app.get("/")
def root():
    return RedirectResponse("/panel/")


# ============================================================
#  静态控制台：必须放在所有路由注册之后
#  目录 static/ 与 main.py 同级，里面是 index.html
# ============================================================
app.mount("/panel", StaticFiles(directory="static", html=True), name="panel")
