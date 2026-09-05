#!/usr/bin/env python3
"""TGFlow Control Room API + Telegram worker launcher."""

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

from main import RestrictedMessageBot

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
app = FastAPI(title="TGFlow Control Room", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("TGFlow_ALLOWED_ORIGIN", "*")], allow_credentials=True, allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

worker: Optional[RestrictedMessageBot] = None
worker_task: Optional[asyncio.Task] = None
started_at = time.time()
activity: list[dict[str, Any]] = []
rules: list[dict[str, Any]] = []


def add_activity(kind: str, message: str, **extra: Any) -> None:
    activity.insert(0, {"time": time.time(), "kind": kind, "message": message, **extra})
    del activity[100:]


class Rule(BaseModel):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    enabled: bool = True


@app.on_event("startup")
async def startup() -> None:
    global worker, worker_task
    worker = RestrictedMessageBot()
    worker_task = asyncio.create_task(worker.run())
    add_activity("system", "TGFlow Telegram worker started")


@app.on_event("shutdown")
async def shutdown() -> None:
    if worker:
        if worker.bot_client:
            await worker.bot_client.disconnect()
        if worker.user_client:
            await worker.user_client.disconnect()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    user_connected = bool(worker and worker.user_client and worker.user_client.is_connected())
    bot_connected = bool(worker and worker.bot_client and worker.bot_client.is_connected())
    return {"ok": True, "worker": "online" if user_connected or bot_connected else "offline", "user_connected": user_connected, "bot_connected": bot_connected, "uptime_seconds": int(time.time() - started_at), "cache_size": len(worker.message_cache) if worker else 0, "rules": len(rules)}


@app.get("/api/activity")
async def get_activity(limit: int = Query(50, ge=1, le=100)) -> list[dict[str, Any]]:
    return activity[:limit]


@app.get("/api/rules")
async def get_rules() -> list[dict[str, Any]]:
    return rules


@app.post("/api/rules")
async def add_rule(rule: Rule) -> dict[str, Any]:
    item = {"id": f"rule-{int(time.time() * 1000)}", **rule.model_dump()}
    rules.append(item)
    add_activity("rule", f"Added route {rule.source} → {rule.target}")
    return item


@app.delete("/api/rules/{rule_id}")
async def delete_rule(rule_id: str) -> dict[str, bool]:
    before = len(rules)
    rules[:] = [r for r in rules if r["id"] != rule_id]
    if len(rules) == before:
        raise HTTPException(404, "Rule not found")
    add_activity("rule", f"Removed route {rule_id}")
    return {"ok": True}


@app.post("/api/worker/restart")
async def restart_worker() -> dict[str, Any]:
    if not worker:
        raise HTTPException(503, "Worker is not initialized")
    if worker.bot_client:
        await worker.bot_client.disconnect()
    if worker.user_client:
        await worker.user_client.disconnect()
    await worker.initialize_clients()
    add_activity("system", "Telegram clients restarted")
    return await status()


@app.get("/api/source/history")
async def source_history(chat: str = Query(..., min_length=1), limit: int = Query(25, ge=1, le=100), before: Optional[int] = Query(None)) -> dict[str, Any]:
    if not worker or not worker.user_client or not worker.user_client.is_connected():
        raise HTTPException(503, "Telegram user session is offline")
    try:
        entity = await worker.user_client.get_entity(chat)
        messages = await worker.user_client.get_messages(entity, limit=limit, max_id=before or 0)
        items = []
        for message in messages:
            if not message:
                continue
            media_type = "video" if message.video else "audio" if message.audio else "photo" if message.photo else "document" if message.document else None
            items.append({"id": message.id, "date": message.date.isoformat() if message.date else None, "text": message.text or "", "media_type": media_type, "has_media": bool(message.media), "protected": bool(getattr(message, "noforwards", False))})
        add_activity("history", f"Loaded {len(items)} messages from {chat}")
        return {"chat": chat, "messages": items, "next_before": items[-1]["id"] if items else None}
    except Exception as exc:
        add_activity("error", f"History lookup failed: {exc}")
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/source/message/{chat}/{message_id}/media")
async def source_media(chat: str, message_id: int) -> FileResponse:
    if not worker or not worker.user_client or not worker.user_client.is_connected():
        raise HTTPException(503, "Telegram user session is offline")
    try:
        entity = await worker.user_client.get_entity(chat)
        message = await worker.user_client.get_messages(entity, ids=message_id)
        if not message or not message.media:
            raise HTTPException(404, "Message has no media")
        media_dir = BASE_DIR / "media-cache"
        media_dir.mkdir(exist_ok=True)
        path = await worker.user_client.download_media(message, file=str(media_dir / f"{chat.replace('/', '_')}_{message_id}"))
        if not path:
            raise HTTPException(404, "Telegram did not provide downloadable media for this message")
        add_activity("media", f"Retrieved media for {chat}/{message_id}")
        return FileResponse(path, filename=Path(path).name)
    except HTTPException:
        raise
    except Exception as exc:
        add_activity("error", f"Media retrieval failed: {exc}")
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")), reload=False)
