"""FastAPI chat boundary. Based on the course's gemini-web-tool-calling starter."""
from pathlib import Path
from threading import Lock
import time
import uuid
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent import MODEL, SYSTEM_PROMPT, run_agent
from maps_client import MapsClient

ROOT=Path(__file__).parent
app=FastAPI(title="THE MIDPOINT NYC")
# Frontend assets are optional during initial test-first development.
app.mount("/static",StaticFiles(directory=ROOT/"static",check_dir=False),name="static")


@dataclass
class Session:
    messages: list = field(default_factory=lambda:[{"role":"system","content":SYSTEM_PROMPT}])
    state: dict = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)
    touched: float = field(default_factory=time.monotonic)


sessions: dict[str,Session]={}
store_lock=Lock()
SESSION_TTL=3600
MAX_SESSIONS=100


class ChatRequest(BaseModel):
    message: str=Field(min_length=1,max_length=4000)
    session_id: str|None=Field(default=None,max_length=80)


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict]


@app.get("/")
def index():
    return FileResponse(ROOT/"index.html")


@app.get("/health")
def health():
    return {"status":"ok","maps_configured":MapsClient().configured,"model":MODEL}


def _session(requested):
    with store_lock:
        now=time.monotonic()
        for sid,session in list(sessions.items()):
            if now-session.touched>SESSION_TTL and not session.lock.locked():
                del sessions[sid]
        if requested and requested in sessions:
            session=sessions[requested]
            session.touched=now
            return requested,session
        if len(sessions)>=MAX_SESSIONS:
            raise HTTPException(503,"Chat capacity reached. Retry later or clear an unused chat.")
        sid=str(uuid.uuid4())
        sessions[sid]=Session()
        return sid,sessions[sid]


@app.post("/chat",response_model=ChatResponse)
def chat(request:ChatRequest):
    if not request.message.strip():
        raise HTTPException(422,"Message must contain text.")
    sid,session=_session(request.session_id)
    # Same-session simultaneous messages cannot interleave tool results/history.
    if not session.lock.acquire(blocking=False):
        raise HTTPException(409,"This chat is still processing a message. Wait for its reply.")
    try:
        from datetime import datetime
        from fairness import NYC
        session.messages[0]={"role":"system","content":SYSTEM_PROMPT+"\nCurrent NYC date/time: "+datetime.now(NYC).isoformat()}
        if len(session.messages)>180:
            raise HTTPException(409,"This chat is full. Start a new chat to continue.")
        session.messages.append({"role":"user","content":request.message.strip()})
        response,tool_calls=run_agent(session.messages,session.state)
        session.touched=time.monotonic()
        return ChatResponse(response=response,session_id=sid,tool_calls=tool_calls)
    finally:
        session.lock.release()


@app.post("/clear")
def clear(session_id:str|None=None):
    with store_lock:
        if session_id in sessions:
            session=sessions[session_id]
            if session.lock.locked():
                raise HTTPException(409,"Wait for this chat's pending reply before clearing it.")
            del sessions[session_id]
    return {"status":"ok"}


@app.get("/privacy",response_class=HTMLResponse)
def privacy():
    return """<!doctype html><html lang="en"><meta name="viewport" content="width=device-width"><title>Privacy — THE MIDPOINT NYC</title><body style="font:16px/1.7 system-ui;max-width:680px;margin:40px auto;padding:20px"><h1>Privacy</h1><p>This student app sends your chat and tool results to Google Vertex AI. It sends starting points and meeting addresses to Google Maps. Use station names when possible. Do not share passwords, API keys, or private details.</p><p>The server keeps your chat and route data in memory. It clears them after one hour without use, or when the server restarts. This browser tab also keeps your chat and a chat ID. New chat clears both copies. Hosting providers may keep service logs.</p><p>We do not sell your data or use ads. API keys are not stored in chats. Google handles data under its <a href="https://policies.google.com/privacy">Privacy Policy</a>. New chat does not delete records held by Google or other providers.</p><p>This is a student project by yt2979 for IEOR 4570 at Columbia University. It is not an official Google service.</p><a href="/">Back to chat</a></body></html>"""


@app.get("/terms",response_class=HTMLResponse)
def terms():
    return """<!doctype html><html lang="en"><meta name="viewport" content="width=device-width"><title>Terms — THE MIDPOINT NYC</title><body style="font:16px/1.7 system-ui;max-width:680px;margin:40px auto;padding:20px"><h1>Terms of use</h1><p>THE MIDPOINT NYC is a student app that helps friends plan where to meet. Travel times are estimates. Place details may be old or missing. Some activities only run at certain times of year.</p><p>Check opening hours, food needs, access, bookings, and tickets before you go. Driving times do not include parking or waiting for a ride. This app does not make bookings.</p><p>We compare a short list of places. We first try to reduce the longest trip while keeping each person's time limit. We do not check every place in New York or promise that a plan will work. Requests may be limited.</p><p>By using Google Maps data in this app, you agree to the <a href="https://maps.google.com/help/terms_maps/">Google Maps/Google Earth Additional Terms</a> and acknowledge <a href="https://policies.google.com/privacy">Google's Privacy Policy</a>.</p><a href="/">Back to chat</a></body></html>"""


if __name__=="__main__":
    import os
    import sys
    from getpass import getpass
    import uvicorn
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT","ieor-4570-f26-yt2979")
    if not os.getenv("GOOGLE_MAPS_API_KEY") and sys.stdin.isatty():
        key=getpass("Google Maps API key (hidden; kept only in this process; Enter to skip): ").strip()
        if key: os.environ["GOOGLE_MAPS_API_KEY"]=key
    uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","8001")))
