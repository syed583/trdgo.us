"""Standalone LOCAL server for the Smart Move dashboard: only the read-only Smart Move router (saved evidence) plus the two stubs the frontend shell asks for at start-up.

    python -m uvicorn smart_move_local_server:app --host 127.0.0.1 --port 8000      (from the backend folder)

It has no database, no provider client, no scheduler and no write endpoint. The `/auth/me` stub returns a signed-in, read-only, non-admin account so the shell renders; it grants
nothing (there is nothing else to grant on this server) and exists only for local use. The full TRDGO-US backend is not started by this file.
"""
from fastapi import FastAPI

from smart_move_routes import router

app = FastAPI(title="Smart Move (local, read-only, saved evidence)", docs_url="/docs")
app.include_router(router)


@app.get("/auth/me")
def me():
    return {"authenticated": True, "is_admin": False, "full_access": False, "local_stub": True, "note": "local Smart Move server: read-only stub, no real account"}


@app.get("/api/health")
def health():
    return {"status": "OK", "service": "smart-move-local", "read_only": True}
