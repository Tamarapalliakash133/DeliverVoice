# DeliverCall — pre-delivery voice agent

FastAPI + plain HTML/CSS/JS (no build step). Gnani Prisma (STT) / Timbre (TTS), **Vobiz** for calls, LangChain + OpenRouter for the conversation brain.

## Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
# edit backend/.env (see below), then from the delivercall/ folder:
uvicorn backend.main:app --port 8000      # UI + API at http://localhost:8000
```

## backend/.env
| Key | What |
|---|---|
| `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN` | from your Vobiz console |
| `VOBIZ_FROM_NUMBER` | a voice number on your Vobiz account, E.164 (`+91...`) |
| `PUBLIC_BASE_URL` | your current ngrok https URL (`ngrok http 8000`) |
| `DEMO_PHONE` | **full** number, `+91` + 10 digits. Every call rings this instead of the customer |
| `GNANI_API_KEY`, `OPENROUTER_API_KEY` | speech + LLM |

## How a call works
1. **Start calling customers** -> backend `POST https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/` with `answer_url=<PUBLIC_BASE_URL>/vobiz/answer?order_id=N`.
2. Phone rings. When answered, Vobiz fetches `/vobiz/answer`; we reply with XML containing a bidirectional `<Stream>` (mu-law 8 kHz) pointing to `wss://.../ws/vobiz/N`, followed by `<Hangup/>`.
3. Over that WebSocket: `media` events (customer audio) -> Prisma STT -> LLM -> Timbre TTS -> `playAudio` events.
4. Call ends -> outcome saved -> dashboard updates. No answer -> `/vobiz/hangup` marks it, retry after `RETRY_MINUTES`, then SMS fallback (logged only; Vobiz SMS not wired).

No Vobiz Application needs to be configured for outbound calls, because the answer URL is passed per call.

## Debugging
The server console prints every Vobiz request/response (`[VOBIZ] ...`), the webhooks Vobiz sends, and WebSocket events (`[WS] ...`). Dial errors also show in the yellow box on the page.

## Verify against docs (couldn't test without your keys)
- Timbre `audio_config` / `GNANI_VOICE` valid for your language.
- If `hangup`/`answer` webhooks don't arrive, check the ngrok URL is current.
- SMS sender must be DLT-registered in India; WhatsApp is not wired.
