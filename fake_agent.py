"""协议样例：模拟手机 Agent 连后端（注册 + 推帧 + 执行指令）"""
import asyncio, json, base64, io, websockets
from PIL import Image

URL = "http://127.0.0.1:8000/ws/agent"

async def main():
    async with websockets.connect(URL.replace("http","ws")) as ws:
        await ws.send(json.dumps({"type":"register","id":"phone-001"}))
        print("[agent] registered")
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "cmd":
                print("[agent] exec:", msg.get("action"), msg.get("code"))
                img = Image.new("RGB",(360,640),(30,40,60))
                from PIL import ImageDraw
                ImageDraw.Draw(img).text((20,300),"LIVE FRAME",fill=(255,255,255))
                buf = io.BytesIO(); img.save(buf,format="JPEG"); 
                await ws.send(json.dumps({"type":"frame","data":buf.getvalue().hex()}))
                await ws.send(json.dumps({"type":"result","id":msg["id"],
                    "executed":{"action":msg["action"],"code":msg["code"]}}))
asyncio.run(main())
