import os, io, re, wave, httpx, numpy as np


GK = os.getenv("GNANI_API_KEY"); VOICE = os.getenv("GNANI_VOICE", "Nalini")
def _env(k, d=""): return (os.getenv(k) or d).strip().strip("\"'")
VB = dict(id=_env("VOBIZ_AUTH_ID"), token=_env("VOBIZ_AUTH_TOKEN"), caller=_env("VOBIZ_FROM_NUMBER"),
          host=_env("VOBIZ_API_HOST", "api.vobiz.ai"))
VOBIZ_OK = all(VB[k] for k in ("id", "token", "caller")) and bool(GK)
SR = 16000      
CALL_SR = 8000   


def _resample(x, a, b):
    if a == b: return x
    n = int(len(x) * b / a); return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16)

def _pcm(b): 
    if b[:4] == b"RIFF":
        w = wave.open(io.BytesIO(b)); a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1: a = a[::w.getnchannels()]
        return _resample(a, w.getframerate(), SR).tobytes()
    return b

def _ulaw_table():
    t = []
    for u in range(256):
        u = ~u & 0xFF; exp = (u >> 4) & 7; s = ((((u & 0xF) << 3) + 0x84) << exp) - 0x84
        t.append(-s if u & 0x80 else s)
    return np.array(t, dtype=np.int16)
_ULAW = _ulaw_table()

def ulaw_to_pcm(b: bytes) -> np.ndarray:           
    return _ULAW[np.frombuffer(b, dtype=np.uint8)]

def pcm_to_ulaw(pcm: bytes) -> bytes:             
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.int32)
    neg = x < 0
    x = np.minimum(np.abs(x), 32635) + 0x84
    exp = np.clip(np.floor(np.log2(x)).astype(np.int32) - 7, 0, 7)
    mant = (x >> (exp + 3)) & 0x0F
    return ((~((neg.astype(np.int32) << 7) | (exp << 4) | mant)) & 0xFF).astype(np.uint8).tobytes()


async def tts(text, lang):
    """Gnani Timbre REST -> raw PCM16 mono at CALL_SR."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.vachana.ai/api/v1/tts/inference", headers={"X-API-Key-ID": GK},
            json={"text": text, "voice": VOICE, "model": "timbre-v2.5", "language": "en-IN", "speed": 1.0,
                  "audio_config": {"sample_rate": SR, "num_channels": 1, "sample_width": 2}})
    if r.status_code >= 400: raise RuntimeError(f"Gnani TTS {r.status_code}: {r.text[:200]}")
    return _resample(np.frombuffer(_pcm(r.content), dtype=np.int16), SR, CALL_SR).tobytes()

async def stt(pcm, lang):
    """Gnani Prisma REST. pcm = PCM16 @ CALL_SR. Comma-separated codes enable Hinglish-style code-switching."""
    pcm = _resample(np.frombuffer(pcm, dtype=np.int16), CALL_SR, SR).tobytes()
    buf = io.BytesIO(); w = wave.open(buf, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm); w.close()
    codes = "en-IN"   # English only
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.vachana.ai/stt/v3", headers={"X-API-Key-ID": GK},
                         files={"audio_file": ("a.wav", buf.getvalue(), "audio/wav")}, data={"language_code": codes})
    if r.status_code >= 400: raise RuntimeError(f"Gnani STT {r.status_code}: {r.text[:200]}")
    return (r.json().get("transcript") or "").strip()


def norm_phone(p):
    """-> E.164 (+91XXXXXXXXXX), which is what Vobiz expects."""
    d = "".join(ch for ch in (p or "") if ch.isdigit())
    if len(d) == 12 and d.startswith("91"): d = d[2:]
    elif len(d) == 11 and d.startswith("0"): d = d[1:]
    if len(d) != 10: raise RuntimeError(f"Phone number '{p}' is not a valid 10-digit Indian mobile number. Fix it in the Demo phone box.")
    return "+91" + d

async def vobiz_call(to, answer_url, hangup_url):
    """Place an outbound call (POST /api/v1/Account/{auth_id}/Call/). Vobiz then fetches answer_url when the callee picks up."""
    url = f"https://{VB['host']}/api/v1/Account/{VB['id']}/Call/"
    caller = VB["caller"] if VB["caller"].startswith("+") else "+" + VB["caller"].lstrip("0")
    payload = {"from": caller, "to": norm_phone(to), "answer_url": answer_url, "answer_method": "POST",
               "hangup_url": hangup_url, "hangup_method": "POST"}
    print("[VOBIZ] POST", url, payload)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, headers={"Content-Type": "application/json", "X-Auth-ID": VB["id"], "X-Auth-Token": VB["token"]}, json=payload)
    print("[VOBIZ]", r.status_code, r.text[:500])
    if r.status_code >= 400: raise RuntimeError(f"Vobiz API error {r.status_code}: {r.text[:300]}")
    try: d = r.json(); return d.get("request_uuid") or d.get("call_uuid") or ""
    except Exception: return ""

async def send_sms(phone, body):
    
    print("[SMS fallback]", phone, body)
