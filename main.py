# main.py | Python 3.11 + FastAPI + uvicorn
# Render: Build "pip install -r requirements.txt"
#         Start "uvicorn main:app --host 0.0.0.0 --port $PORT"
import os, io, glob, subprocess
from fastapi import FastAPI, Response, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

app = FastAPI(title="Cinder Brain Ops")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# ---------- 你原有的业务路由写在这里 ----------
# （/healthz、/api/screenshot、/api/files、/api/control 等保持原样不动）

@app.get("/healthz")
def healthz():
    return {"ok": True, "health": "/healthz", "ui": "/ui"}

@app.get("/api/devices")
def devices():
    return [{"id": "local", "name": "本机设备", "status": "online"}]

# ---------- 静态控制台挂载（必须放在所有路由之后）----------
app.mount("/panel", StaticFiles(directory="static", html=True), name="panel")

@app.get("/")
def root():
    return RedirectResponse("/panel/")
