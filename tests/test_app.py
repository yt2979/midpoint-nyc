from fastapi.testclient import TestClient
import app as server

def test_same_session_remembers_and_new_sessions_are_isolated(monkeypatch):
    def fake_agent(messages,state):
        seen=[m["content"] for m in messages if m["role"]=="user"]
        messages.append({"role":"assistant","content":" | ".join(seen)})
        return " | ".join(seen),[{"name":"check","args":{},"result":{"seen":seen}}]
    monkeypatch.setattr(server,"run_agent",fake_agent)
    server.sessions.clear()
    client=TestClient(server.app)
    first=client.post("/chat",json={"message":"Alice at Columbia"}).json()
    assert set(first)=={"response","session_id","tool_calls"}
    second=client.post("/chat",json={"message":"Max 35 minutes","session_id":first["session_id"]}).json()
    assert "Alice at Columbia" in second["response"]
    other=client.post("/chat",json={"message":"Where should Bob meet?"}).json()
    assert "Alice" not in other["response"]
    assert first["session_id"]!=other["session_id"]
    assert client.post("/clear",params={"session_id":first["session_id"]}).status_code==200
    restarted=client.post("/chat",json={"message":"Hello","session_id":first["session_id"]}).json()
    assert restarted["session_id"]!=first["session_id"]
    assert restarted["response"]=="Hello"

def test_empty_chat_rejected_and_health_never_contains_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY","private-secret")
    client=TestClient(server.app)
    assert client.post("/chat",json={"message":" "}).status_code==422
    assert client.post("/chat",json={"message":"x"*4001}).status_code==422
    health=client.get("/health").json()
    assert health["maps_configured"]
    assert "private-secret" not in str(health)

def test_same_chat_concurrent_request_returns_actionable_conflict():
    sid,session=server._session(None)
    session.lock.acquire()
    try:
        reply=TestClient(server.app).post("/chat",json={"message":"Hi","session_id":sid})
        assert reply.status_code==409
        assert "processing" in reply.json()["detail"]
    finally:
        session.lock.release()

def test_expired_session_does_not_recover_old_context():
    sid,session=server._session(None)
    session.touched-=server.SESSION_TTL+1
    new_id,new_session=server._session(sid)
    assert new_id!=sid
    assert new_session.state=={}
