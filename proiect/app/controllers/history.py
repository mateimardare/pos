"""Job Status Service (lab 4 + project): read-only task history and WebSocket notifications.

`task_history` documents are created and updated by the runner (one per execution and station):
_id (job id), id_user -> users, id_script -> scripts, id_computer, id_cluster (optional), id_execution,
date, state in {pending, active, finished} (missing == pending), status (message), notification (bool).

REST (read only):
  GET  /history, /history/{id}          admin: every task; maintainer: their own
  POST /history/notifications           maintainer: start a notification session -> one-time WebSocket URI
WebSocket:
  GET  /history/notifications/{token}   (Upgrade) pushes each task once, when it reaches `finished`
"""
import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from .. import views
from ..common import BASE, PageParams, not_found, now, oid_or_404
from ..config import settings
from ..db import get_db
from ..schemas import TaskState
from ..security import Principal, maintainer_only, any_user

log = logging.getLogger("job-status")
router = APIRouter(tags=["history"])
COLL = f"{BASE}/history"
WS_COLL = f"{COLL}/notifications"


async def _scope(db, principal: Principal) -> dict:
    """Admins see every task, maintainers only the tasks they requested."""
    if principal.is_admin:
        return {}
    user = await db.users.find_one({"username": principal.username}, {"_id": 1})
    return {"id_user": user["_id"] if user else None}


async def _names(db, docs: list[dict]):
    def ids(key):
        return list({d[key] for d in docs if d.get(key)})
    users = {u["_id"]: u["username"] async for u in db.users.find({"_id": {"$in": ids("id_user")}}, {"username": 1})}
    scripts = {s["_id"]: s["name"] async for s in db.scripts.find({"_id": {"$in": ids("id_script")}}, {"name": 1})}
    hosts = {c["_id"]: c["hostname"] async for c in
             db.computers.find({"_id": {"$in": ids("id_computer")}}, {"hostname": 1})}
    return users, scripts, hosts


def _view(doc, names):
    users, scripts, hosts = names
    return views.task(doc, users.get(doc["id_user"]), scripts.get(doc["id_script"]), hosts.get(doc.get("id_computer")))


@router.get(COLL, summary="Task history (read-only; refresh it periodically)")
async def list_history(
        pg: PageParams = Depends(),
        state: Optional[TaskState] = Query(None, description="pending | active | finished"),
        scriptId: Optional[str] = Query(None, description="only tasks of this script"),
        computerId: Optional[str] = Query(None, description="only tasks run on this computer"),
        clusterId: Optional[str] = Query(None, description="only tasks started on this cluster"),
        notification: Optional[bool] = Query(None, description="notified (true) or not yet (false)"),
        since: Optional[datetime] = Query(None, description="only tasks updated at or after this date"),
        principal: Principal = Depends(any_user),
        db: AsyncIOMotorDatabase = Depends(get_db)):
    flt = await _scope(db, principal)
    if state == TaskState.pending:      # a missing `state` means pending
        flt["$or"] = [{"state": "pending"}, {"state": {"$exists": False}}]
    elif state:
        flt["state"] = state.value
    for param, key, what in ((scriptId, "id_script", "Script"), (computerId, "id_computer", "Computer"),
                             (clusterId, "id_cluster", "Cluster")):
        if param:
            flt[key] = oid_or_404(param, what)
    if notification is not None:
        flt["notification"] = notification
    if since:
        flt["date"] = {"$gte": since}
    total = await db.task_history.count_documents(flt)
    docs = await (db.task_history.find(flt).sort([("date", -1), ("_id", -1)])
                  .skip(pg.skip).limit(pg.items_per_page).to_list(None))
    names = await _names(db, docs)
    query = {"state": state.value if state else None, "scriptId": scriptId, "computerId": computerId,
             "clusterId": clusterId, "since": since.isoformat() if since else None,
             "notification": None if notification is None else str(notification).lower()}
    return views.collection("history", [_view(d, names) for d in docs], href=COLL, parent=BASE, page=pg.page,
                            items_per_page=pg.items_per_page, total=total, query=query,
                            extra_links={} if principal.is_admin else
                            {"notifications": views.link(WS_COLL, "POST")})


# ---------------------------------------------------------------- notification session
@router.post(WS_COLL, status_code=status.HTTP_201_CREATED,
             summary="Start a notification session: returns a one-time WebSocket URI (maintainer)")
async def start_notification_session(request: Request, principal: Principal = Depends(maintainer_only),
                                     db: AsyncIOMotorDatabase = Depends(get_db)):
    """Only maintainers: the stream marks tasks as notified, so an admin listening would consume
    the notifications that belong to the maintainer who requested the task."""
    await db.users.update_one({"username": principal.username}, {"$setOnInsert": {"role": principal.role.value}},
                              upsert=True)
    token = secrets.token_urlsafe(32)
    expires = (now() + timedelta(seconds=settings.ws_session_ttl)).replace(tzinfo=None)   # naive UTC, as Mongo returns
    await db.ws_sessions.insert_one({"token": token, "username": principal.username, "used": False,
                                     "created_at": now(), "expires_at": expires})
    scheme = "wss" if request.url.scheme == "https" else "ws"
    uri = f"{scheme}://{request.url.netloc}{WS_COLL}/{token}"
    return {"token": token, "uri": uri, "expires_in": settings.ws_session_ttl,
            "_links": {"self": views.link(WS_COLL), "parent": views.link(COLL), "websocket": views.link(uri)}}


@router.get(f"{COLL}/{{task_id}}", summary="One task of the history")
async def get_task(task_id: str, principal: Principal = Depends(any_user),
                   db: AsyncIOMotorDatabase = Depends(get_db)):
    flt = {"_id": oid_or_404(task_id, "Task"), **await _scope(db, principal)}
    doc = await db.task_history.find_one(flt)
    if doc is None:
        raise not_found("Task")
    return _view(doc, await _names(db, [doc]))


# ---------------------------------------------------------------- WebSocket
async def _deliver(ws: WebSocket, db, user_id) -> None:
    """Send every finished task whose notification is FALSE (or missing), then mark it TRUE.

    A task is claimed (notification=true) *before* sending, atomically, so two connections of the
    same user never get the same notification; if the send fails the claim is released."""
    cursor = db.task_history.find({"id_user": user_id, "state": "finished",
                                   "notification": {"$ne": True}}).sort("date", 1)
    for doc in await cursor.to_list(100):
        claim = await db.task_history.update_one({"_id": doc["_id"], "notification": {"$ne": True}},
                                                 {"$set": {"notification": True}})
        if claim.modified_count == 0:
            continue                     # another connection took it
        doc["notification"] = True
        try:
            await ws.send_json({"event": "task.finished", "task": _view(doc, await _names(db, [doc]))})
        except Exception:
            await db.task_history.update_one({"_id": doc["_id"]}, {"$set": {"notification": False}})
            raise


@router.websocket(f"{WS_COLL}/{{token}}")
async def notifications(ws: WebSocket, token: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    session = await db.ws_sessions.find_one_and_update(
        {"token": token, "used": False, "expires_at": {"$gt": now().replace(tzinfo=None)}},
        {"$set": {"used": True}})
    if session is None:                  # unknown, expired or already used -> handshake refused
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    user = await db.users.find_one({"username": session["username"]}, {"_id": 1})
    if user is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()
    log.info("notification stream opened for %s", session["username"])
    try:
        while True:
            await _deliver(ws, db, user["_id"])
            try:      # waits for the next poll, but notices at once when the client leaves
                await asyncio.wait_for(ws.receive_text(), timeout=settings.ws_poll_interval)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        log.info("notification stream closed for %s", session["username"])
