import os, sys, re, json, time, base64, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # works from backend/ or project root
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
import backend.db as db
import backend.brain as brain
import backend.voice as voice
import backend.seed as seed

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
RETRY = float(os.getenv("RETRY_MINUTES", "1")); MAXTRY = 2
_base = (os.getenv("PUBLIC_WS_URL") or os.getenv("PUBLIC_BASE_URL") or "").strip().strip("\"'").rstrip("/")
WS_URL = _base.replace("https://", "wss://").replace("http://", "ws://")
HTTP_URL = WS_URL.replace("wss://", "https://").replace("ws://", "http://")
LIVE = bool(WS_URL and voice.VOBIZ_OK)
OUT = {"confirmed", "rescheduled", "cancelled", "return_requested", "handoff"}
sessions, runners, connected = {}, {}, set()
DEMO = {"phone": os.getenv("DEMO_PHONE", "")}
STATE = {"last_error": ""}   # last dial error, shown in the UI
db.init(); seed.run(); db.q("update orders set lang='en-IN'")   # English only, also for existing rows

def shape(o):
    o["transcript"] = json.loads(o["transcript"] or "[]"); return o

def add_msg(oid, role, text):
    o = db.get(oid); tr = json.loads(o["transcript"]); tr.append({"role": role, "text": text, "ts": time.time()})
    db.update(oid, transcript=json.dumps(tr))

def end(oid):
    s = sessions.get(oid)
    if s: s["ev"].set()

def apply(oid, t):
    kw = {}
    for k, col in (("language", "lang"), ("slot", "slot"), ("address_note", "addr_note"), ("reason", "reason")):
        if t.get(k): kw[col] = t[k]
    oc = t.get("outcome"); done = bool(t.get("end_call") or oc in OUT)
    if oc in OUT: kw["status"] = oc
    elif done: kw["status"] = "handoff"
    if kw: db.update(oid, **kw)
    if done: end(oid)
    return done

CANCEL_RE = re.compile(r"\b(cancel(l?ed|l?ing)?|don'?t want|do not want|not interested|no longer (want|need)|don'?t need|do not need|not required|no need)\b", re.I)
NOT_CANCEL_RE = re.compile(r"\b(don'?t|do not|not|never|no)\s+(want to\s+|wish to\s+)?cancel", re.I)
HAS_REASON_RE = re.compile(r"\b(because|since|as i|reason|too (late|expensive|costly)|found|cheaper|changed my mind|wrong|mistake|ordered by)\b", re.I)
cancel_asked = set()   # orders where the agent already asked "why?" after a cancel request

def cancel_turn(oid, text):
    """Deterministic cancel handling, so the dashboard updates the moment the customer asks to cancel."""
    done = {"language": "en-IN", "outcome": "cancelled", "end_call": True, "slot": None, "address_note": None}
    if oid in cancel_asked:                                   # this message is the reason
        cancel_asked.discard(oid)
        return {**done, "reason": text, "reply": "Thank you, I have noted that and cancelled your order. Have a nice day!"}
    if CANCEL_RE.search(text) and not NOT_CANCEL_RE.search(text):
        if HAS_REASON_RE.search(text):                        # reason already given in the same sentence
            return {**done, "reason": text, "reply": "Understood, I have cancelled your order. Have a nice day!"}
        cancel_asked.add(oid)
        db.update(oid, status="cancelled", reason="Customer asked to cancel")   # dashboard updates right now
        return {"language": "en-IN", "outcome": None, "end_call": False, "slot": None, "address_note": None, "reason": "Customer asked to cancel",
                "reply": "Sure, I will cancel it. May I know the reason, please?"}
    return None

async def handle_text(oid, text):
    add_msg(oid, "customer", text)
    t = cancel_turn(oid, text)
    if t:
        add_msg(oid, "agent", t["reply"]); return t, apply(oid, t)
    t = await brain.turn(db.get(oid), json.loads(db.get(oid)["transcript"]))
    add_msg(oid, "agent", t["reply"])
    return t, apply(oid, t)

# ---------- calling queue ----------
async def place_call(o):
    oid = o["id"]; ev = asyncio.Event(); sessions[oid] = {"ev": ev}
    db.update(oid, status="calling", attempts=o["attempts"] + 1, next_retry=None)
    if LIVE:
        try:
            STATE["last_error"] = ""
            await voice.vobiz_call(DEMO["phone"] or o["phone"], f"{HTTP_URL}/vobiz/answer?order_id={oid}",
                                   f"{HTTP_URL}/vobiz/hangup?order_id={oid}")
        except Exception as e:
            print("Vobiz error:", e); STATE["last_error"] = str(e); runners.clear()
            db.update(oid, status="pending", attempts=max(0, o["attempts"])); ev.set(); return
    try: await asyncio.wait_for(ev.wait(), 180)
    except asyncio.TimeoutError: pass
    if db.get(oid)["status"] == "calling": db.update(oid, status="no_answer")
    o2 = db.get(oid)
    if o2["status"] == "no_answer":
        if o2["attempts"] < MAXTRY: db.update(oid, next_retry=time.time() + RETRY * 60)   # retry later
        elif not o2["sms_sent"]:                                                          # SMS/WhatsApp fallback
            await voice.send_sms(o2["phone"], f"Hi {o2['customer']}, your {o2['product']} is out for delivery today. Reply with a convenient time slot.")
            db.update(oid, sms_sent=1)

def next_order(aid):
    r = db.q("select * from orders where agent_id=? and (status='pending' or (status='no_answer' and attempts<? and next_retry<=?)) order by seq", (aid, MAXTRY, time.time()))
    return r[0] if r else None

async def run_queue(aid):
    try:
        while runners.get(aid):
            o = next_order(aid)
            if o: await place_call(o); continue
            if db.q("select count(*) c from orders where agent_id=? and status='no_answer' and attempts<?", (aid, MAXTRY))[0]["c"]:
                await asyncio.sleep(3); continue
            break
    finally:
        runners[aid] = False

# ---------- REST ----------
@app.get("/api/config")
def config():
    need = {"VOBIZ_AUTH_ID": voice.VB["id"], "VOBIZ_AUTH_TOKEN": voice.VB["token"], "VOBIZ_FROM_NUMBER": voice.VB["caller"],
            "GNANI_API_KEY": voice.GK, "PUBLIC_BASE_URL": WS_URL}
    return {"live_calls": LIVE, "llm": bool(brain.llm), "slots": brain.SLOTS, "missing": [k for k, v in need.items() if not v]}

@app.get("/api/demo-phone")
def get_demo(): return DEMO

@app.post("/api/demo-phone")
def set_demo(body: dict): DEMO["phone"] = (body.get("phone") or "").strip().replace(" ", ""); return DEMO

@app.get("/api/agents")
def agents(): return db.q("select * from agents")

@app.post("/api/agents/{aid}/start")
async def start(aid: int):
    if not LIVE: return {"error": "Live calling is not configured. Set VOBIZ_AUTH_ID, VOBIZ_AUTH_TOKEN, VOBIZ_FROM_NUMBER, GNANI_API_KEY and PUBLIC_BASE_URL in backend/.env and restart."}
    if not runners.get(aid): runners[aid] = True; asyncio.create_task(run_queue(aid))
    return {"running": True}

@app.post("/api/agents/{aid}/stop")
def stop(aid: int): runners[aid] = False; return {"running": False}

@app.get("/api/agents/{aid}/status")
def status(aid: int): return {"running": bool(runners.get(aid)), "error": STATE["last_error"]}

def slot_idx(s):
    return brain.SLOTS.index(s) if s in brain.SLOTS else 9

@app.get("/api/agents/{aid}/route")
def route(aid: int):
    rows = [shape(r) for r in db.q("select * from orders where agent_id=?", (aid,))]
    def key(r):
        s = r["status"]
        if s == "confirmed": return (0, slot_idx(r["slot"]), r["seq"])   # re-sorted by confirmed window
        if s in ("pending", "calling", "no_answer"): return (1, 0, r["seq"])
        if s == "handoff": return (2, 0, r["seq"])
        return (3, 0, r["seq"])
    rows.sort(key=key); pos = 0
    for r in rows:
        r["skip"] = r["status"] in ("rescheduled", "cancelled", "return_requested")
        if not r["skip"]: pos += 1; r["route_pos"] = pos
        else: r["route_pos"] = None
    return rows

@app.get("/api/orders")
def orders(): return [shape(r) for r in db.q("select * from orders order by id")]

@app.get("/api/stats")
def stats():
    c = {r["status"]: r["c"] for r in db.q("select status, count(*) c from orders group by status")}
    return {k: c.get(k, 0) for k in ("pending", "calling", "confirmed", "rescheduled", "cancelled", "return_requested", "no_answer", "handoff")}

@app.post("/api/orders/{oid}/call")
async def call_one(oid: int):
    if not LIVE: return {"error": "Live calling is not configured. Set VOBIZ_AUTH_ID, VOBIZ_AUTH_TOKEN, VOBIZ_FROM_NUMBER, GNANI_API_KEY and PUBLIC_BASE_URL in backend/.env and restart."}
    o = db.get(oid)
    if o["status"] not in ("calling",): asyncio.create_task(place_call(o))
    return {"ok": True}

@app.post("/api/reset")
def reset(): seed.run(force=True); return {"ok": True}

# ---------- Vobiz webhooks ----------
def _calling_order():
    c = db.q("select id from orders where status='calling' order by updated desc")
    return c[0]["id"] if c else None

@app.api_route("/vobiz/answer", methods=["GET", "POST"])
async def vobiz_answer(request: Request, order_id: int | None = None):
    """Vobiz fetches this when the callee picks up; we reply with XML that opens the bidirectional audio stream."""
    oid = order_id or _calling_order()
    print("[VOBIZ] /answer order_id =", oid, "| CallUUID =", (await request.form()).get("CallUUID") if request.method == "POST" else "-")
    ws = f"{WS_URL}/ws/vobiz/{oid}" if oid else f"{WS_URL}/ws/vobiz"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000"
            statusCallbackUrl="{HTTP_URL}/vobiz/stream-status" statusCallbackMethod="POST">{ws}</Stream>
    <Hangup/>
</Response>"""
    return Response(content=xml, media_type="application/xml")

@app.post("/vobiz/stream-status")
async def vobiz_stream_status(request: Request):
    print("[VOBIZ] stream-status", dict(await request.form())); return Response(status_code=204)

@app.api_route("/vobiz/hangup", methods=["GET", "POST"])
async def vobiz_hangup(request: Request, order_id: int):
    f = dict(await request.form()) if request.method == "POST" else {}
    print("[VOBIZ] hangup", order_id, f)
    o = db.get(order_id)
    # callee never picked up (the media socket never opened) -> no answer
    if o and o["status"] == "calling" and order_id not in connected:
        db.update(order_id, status="no_answer"); end(order_id)
    return Response(status_code=204)

# ---------- Vobiz bidirectional voice stream (mu-law 8 kHz, JSON over WebSocket) ----------
SR = voice.CALL_SR; CHUNK = 160   # 20 ms of mu-law @ 8 kHz

@app.websocket("/ws/vobiz/{order_id}")
async def ws_vobiz_id(ws: WebSocket, order_id: int): await vobiz_stream(ws, order_id)

@app.websocket("/ws/vobiz")
async def ws_vobiz(ws: WebSocket, order_id: int | None = None): await vobiz_stream(ws, order_id)

async def vobiz_stream(ws: WebSocket, order_id):
    await ws.accept(); print("[WS] Vobiz connected, order_id =", order_id)
    sid = None; busy = True; speech = bytearray(); sil_ms = 0.0; voice_ms = 0.0
    if order_id: connected.add(order_id)

    async def say(text, lang):
        pcm = await voice.tts(text, lang)
        u = voice.pcm_to_ulaw(pcm)
        for i in range(0, len(u), CHUNK):
            await ws.send_text(json.dumps({"event": "playAudio", "streamId": sid,
                "media": {"contentType": "audio/x-mulaw", "sampleRate": SR, "payload": base64.b64encode(u[i:i + CHUNK]).decode()}}))
        return len(u) / SR   # seconds of audio queued

    async def respond(pcm):
        nonlocal busy; busy = True; done = False
        try:
            o = db.get(order_id)
            text = await voice.stt(bytes(pcm), o["lang"])
            if text:
                t, done = await handle_text(order_id, text)
                await asyncio.sleep(await say(t["reply"], t["language"]))
        except Exception as e: print("turn error:", e)
        if done:
            await asyncio.sleep(0.5)
            try: await ws.close()
            except Exception: pass
        busy = False

    async def greet():
        nonlocal busy
        try:
            o = db.get(order_id); t = await brain.turn(o, []); add_msg(order_id, "agent", t["reply"])
            await asyncio.sleep(await say(t["reply"], t.get("language") or o["lang"]))
        except Exception as e: print("greeting error:", e)
        busy = False

    try:
        while True:
            m = json.loads(await ws.receive_text()); ev = m.get("event")
            if ev == "start":
                st = m.get("start") or {}; sid = m.get("streamId") or st.get("streamId")
                print("[WS] start:", m)
                if order_id is None:
                    order_id = _calling_order()
                    if order_id is None: print("[WS] no order for this call"); await ws.close(); return
                    connected.add(order_id)
                asyncio.create_task(greet())
            elif ev == "media" and not busy:
                a = voice.ulaw_to_pcm(base64.b64decode(m["media"]["payload"]))
                ms = len(a) / (SR / 1000)
                rms = float(np.sqrt(np.mean(a.astype(np.float32) ** 2))) if len(a) else 0
                if rms > 400: voice_ms += ms; sil_ms = 0; speech += a.tobytes()
                elif voice_ms: sil_ms += ms; speech += a.tobytes()
                if voice_ms >= 150 and (sil_ms >= 700 or len(speech) > SR * 2 * 15):
                    pcm, speech, voice_ms, sil_ms = bytes(speech), bytearray(), 0.0, 0.0
                    asyncio.create_task(respond(pcm))
            elif ev == "stop": break
    except WebSocketDisconnect: pass
    finally:
        o = db.get(order_id) if order_id else None
        if o and o["status"] == "calling":
            has_reply = any(h["role"] == "customer" for h in json.loads(o["transcript"]))
            db.update(order_id, status="handoff" if has_reply else "no_answer")  # hung up mid-call -> human follows up
        if order_id: connected.discard(order_id); end(order_id)

# ---------- serve the plain HTML/CSS/JS frontend (no build step) ----------
from fastapi.staticfiles import StaticFiles
_dist = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
else:
    @app.get("/")
    def root():
        return {"app": "DeliverCall API", "status": "running", "live_calls": LIVE, "docs": "/docs", "ui": "frontend folder missing"}
