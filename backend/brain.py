import os, re, json
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

SLOTS = ["9 AM - 12 PM", "12 PM - 3 PM", "3 PM - 6 PM", "6 PM - 9 PM"]
KEY = os.getenv("OPENROUTER_API_KEY")
llm = ChatOpenAI(model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"), api_key=KEY,
                 base_url="https://openrouter.ai/api/v1", temperature=0.2, timeout=20) if KEY else None

PROMPT = """You are DeliverCall, a polite voice agent phoning a customer on delivery day on behalf of the seller.
ORDER: customer=<<C>>, product=<<P>>, COD amount=Rs <<A>>, address="<<ADDR>>", landmark="<<LM>>", current language=<<L>>.
FLOW: (1) greet by name, say the parcel is out for delivery today, ask if they are available and in which slot: <<SLOTS>>.
(2) read back the address+landmark, ask if correct (gate, floor, landmark changes). (3) close politely.
RULES: Speak ONLY in simple, clear English, whatever language the customer uses. Never switch languages. If the customer speaks another language, understand it but still reply in English.
If the customer asks to cancel at any point, ask the reason once (unless already given), then end with outcome cancelled.
Max 2 short sentences per turn. Write numbers/prices as spoken words. One question per turn.
Outcome only in your FINAL turn (together with a goodbye and end_call=true):
 confirmed (available today; slot must be exactly one of the slots; address verified), rescheduled (wants another day/time; put it in slot, free text e.g. "tomorrow 6 PM - 9 PM"),
 cancelled (ask the reason once first), return_requested (ask the reason once first), handoff (customer confused/upset twice or asks for a human; say a colleague will call).
Return ONLY JSON: {"reply":str,"language":"en-IN","outcome":null|"confirmed"|"rescheduled"|"cancelled"|"return_requested"|"handoff","slot":null|str,"address_note":null|str,"reason":null|str,"end_call":bool}"""

def _sys(o):
    return (PROMPT.replace("<<C>>", o["customer"]).replace("<<P>>", o["product"]).replace("<<A>>", str(o["amount"]))
            .replace("<<ADDR>>", o["address"]).replace("<<LM>>", o["landmark"] or "").replace("<<L>>", o["lang"])
            .replace("<<SLOTS>>", ", ".join(SLOTS)))

async def turn(order, hist):
    if llm:
        try:
            msgs = [SystemMessage(content=_sys(order))]
            for h in hist:
                msgs.append(AIMessage(content=h["text"]) if h["role"] == "agent" else HumanMessage(content=h["text"]))
            if not hist: msgs.append(HumanMessage(content="[CALL CONNECTED - give the opening greeting]"))
            r = await llm.ainvoke(msgs)
            d = json.loads(re.search(r"\{.*\}", r.content, re.S).group(0))
            d["language"] = "en-IN"; d["reply"] = d.get("reply") or "..."
            return d
        except Exception as e:
            print("LLM error, using fallback:", e)
    return fallback(order, hist)

def fallback(o, hist):  # tiny rule-based brain so the demo works with no LLM key
    hi = False; n = sum(h["role"] == "customer" for h in hist)
    last = hist[-1]["text"].lower() if hist else ""
    base = {"language": "en-IN", "outcome": None, "slot": None, "address_note": None, "reason": None, "end_call": False}
    if n == 0:
        return {**base, "reply": f"नमस्ते {o['customer']} जी, आपका {o['product']} आज डिलीवरी के लिए निकला है। क्या आप आज घर पर हैं?" if hi
                else f"Hello {o['customer']}, your {o['product']} is out for delivery today. Are you available, and at what time?"}
    if any(w in last for w in ["cancel", "nahi", "नहीं", "వద్దు", "no "]) and n == 1:
        return {**base, "outcome": "cancelled", "end_call": True, "reason": last, "reply": "ठीक है, ऑर्डर कैंसल कर दिया। धन्यवाद!" if hi else "Okay, I have cancelled the order. Thank you!"}
    if n == 1:
        slot = SLOTS[3] if any(w in last for w in ["shaam", "evening", "शाम", "6", "7"]) else SLOTS[1] if any(w in last for w in ["noon", "dopahar", "12", "1", "2"]) else SLOTS[0] if any(w in last for w in ["morning", "subah", "सुबह", "9", "10"]) else SLOTS[2]
        return {**base, "slot": slot, "reply": f"ठीक है, {slot}। आपका पता {o['address']}, {o['landmark']} सही है?" if hi else f"Got it, {slot}. Is the address {o['address']}, {o['landmark']} correct?"}
    return {**base, "outcome": "confirmed", "end_call": True, "address_note": last if "no" in last or "nahi" in last else None,
            "reply": "धन्यवाद! डिलीवरी कन्फर्म है।" if hi else "Thank you! Your delivery is confirmed."}
