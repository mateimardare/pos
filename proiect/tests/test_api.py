
"""End-to-end tests of the REST service against an in-memory MongoDB (mongomock-motor)."""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

os.environ.setdefault("SCRIPTS_DIR", tempfile.mkdtemp(prefix="scripts-"))
os.environ.setdefault("WS_POLL_INTERVAL", "0.05")

from app import db as dbmod  # noqa: E402
from app.ipc import dispatcher  # noqa: E402
from app.main import app  # noqa: E402
from tools.mint_token import mint  # noqa: E402

B = "/api/manager"
ADMIN = {"Authorization": f"Bearer {mint('admin', 'admin')}"}
ANA = {"Authorization": f"Bearer {mint('ana', 'maintainer')}"}
BOB = {"Authorization": f"Bearer {mint('bob', 'maintainer')}"}


@pytest.fixture()
def client(monkeypatch):
    dbmod.set_database(AsyncMongoMockClient()["test"])
    sent = []
    monkeypatch.setattr(dispatcher, "dispatch", lambda eid, timeout=5.0: sent.append(eid))
    with TestClient(app) as c:
        c.dispatched = sent
        yield c


def mk_computer(c, host="station-01", ip="172.28.0.11"):
    r = c.post(f"{B}/computers", json={"hostname": host, "ip_address": ip}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def mk_script(c, name="clean-tmp", content="#!/bin/bash\necho hi\n"):
    r = c.post(f"{B}/scripts", json={"name": name}, headers=ADMIN)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    if content is not None:
        r = c.put(f"{B}/scripts/{sid}/content", files={"file": ("clean.sh", content, "text/plain")}, headers=ADMIN)
        assert r.status_code == 201, r.text
    return sid


# ---------------------------------------------------------------- auth
def test_auth_required_and_roles(client):
    assert client.get(f"{B}/computers").status_code == 401
    assert client.get(f"{B}/computers", headers={"Authorization": "Bearer junk"}).status_code == 401
    r = client.post(f"{B}/computers", json={"hostname": "x", "ip_address": "10.0.0.1"}, headers=ANA)
    assert r.status_code == 403


def test_openapi_json_is_public(client):
    r = client.get(f"{B}/openapi.json")
    assert r.status_code == 200 and r.json()["openapi"].startswith("3.")


# ---------------------------------------------------------------- status codes
def test_create_conflict_and_validation_codes(client):
    cid = mk_computer(client)
    r = client.get(f"{B}/computers/{cid}", headers=ADMIN)
    body = r.json()
    assert r.status_code == 200
    assert body["_links"]["self"]["href"] == f"{B}/computers/{cid}"
    assert body["_links"]["parent"]["href"] == f"{B}/computers"
    # duplicate hostname -> 409
    assert client.post(f"{B}/computers", json={"hostname": "station-01", "ip_address": "10.0.0.2"},
                       headers=ADMIN).status_code == 409
    # missing mandatory property -> 415
    assert client.post(f"{B}/computers", json={"hostname": "s2"}, headers=ADMIN).status_code == 415
    # unknown property -> 415
    assert client.post(f"{B}/computers", json={"hostname": "s2", "ip_address": "10.0.0.2", "x": 1},
                       headers=ADMIN).status_code == 415
    # not JSON -> 415
    assert client.post(f"{B}/computers", content="hostname=s2", headers={**ADMIN, "Content-Type": "text/plain"}
                       ).status_code == 415
    # right shape, bad values -> 422
    assert client.post(f"{B}/computers", json={"hostname": "bad host!", "ip_address": "10.0.0.2"},
                       headers=ADMIN).status_code == 422
    assert client.post(f"{B}/computers", json={"hostname": "s2", "ip_address": "999.1.1.1"},
                       headers=ADMIN).status_code == 422
    # unknown / malformed id -> 404
    assert client.get(f"{B}/computers/0123456789abcdef01234567", headers=ADMIN).status_code == 404
    assert client.get(f"{B}/computers/nope", headers=ADMIN).status_code == 404


def test_put_creates_then_replaces(client):
    oid = "6553f0c2a1b2c3d4e5f60718"
    payload = {"hostname": "station-09", "ip_address": "172.28.0.19"}
    r = client.put(f"{B}/computers/{oid}", json=payload, headers=ADMIN)
    assert r.status_code == 201 and r.headers["location"].endswith(oid)
    r = client.put(f"{B}/computers/{oid}", json={**payload, "ssh_port": 2222}, headers=ADMIN)
    assert r.status_code == 204
    assert client.get(f"{B}/computers/{oid}", headers=ADMIN).json()["ssh_port"] == 2222
    assert client.put(f"{B}/computers/not-an-id", json=payload, headers=ADMIN).status_code == 422


def test_delete(client):
    cid = mk_computer(client)
    assert client.delete(f"{B}/computers/{cid}", headers=ADMIN).status_code == 204
    assert client.delete(f"{B}/computers/{cid}", headers=ADMIN).status_code == 404


# ---------------------------------------------------------------- pagination
def test_pagination(client):
    for i in range(5):
        mk_computer(client, f"st-{i}", f"10.0.0.{i + 1}")
    r = client.get(f"{B}/computers?page=2&items_per_page=2", headers=ADMIN).json()
    assert [c["hostname"] for c in r["_embedded"]["computers"]] == ["st-2", "st-3"]
    assert r["page"] == {"number": 2, "items_per_page": 2, "total_items": 5, "total_pages": 3}
    assert {"self", "parent", "first", "last", "prev", "next"} <= set(r["_links"])
    assert client.get(f"{B}/computers?page=0", headers=ADMIN).status_code == 400


# ---------------------------------------------------------------- maintainer scope
def test_maintainer_sees_only_assigned(client):
    c1 = mk_computer(client, "a1", "10.0.1.1")
    c2 = mk_computer(client, "b1", "10.0.2.1")
    r = client.post(f"{B}/clusters", json={"name": "lab-a", "computer_ids": [c1], "maintainers": ["ana"]},
                    headers=ADMIN)
    assert r.status_code == 201
    assert r.json()["computers"][0]["hostname"] == "a1"
    ana = client.get(f"{B}/computers", headers=ANA).json()
    assert [c["hostname"] for c in ana["_embedded"]["computers"]] == ["a1"]
    assert ana["_embedded"]["computers"][0]["clusters"][0]["name"] == "lab-a"
    assert client.get(f"{B}/computers/{c2}", headers=ANA).status_code == 404
    assert client.get(f"{B}/clusters", headers=BOB).json()["page"]["total_items"] == 0
    # assign through navigation route
    clid = r.json()["id"]
    assert client.put(f"{B}/clusters/{clid}/maintainers/bob", headers=ADMIN).status_code == 201
    assert client.put(f"{B}/clusters/{clid}/maintainers/bob", headers=ADMIN).status_code == 204
    assert client.get(f"{B}/clusters/{clid}", headers=BOB).status_code == 200
    assert client.put(f"{B}/clusters/{clid}/computers/{c2}", headers=ADMIN).status_code == 201
    assert client.get(f"{B}/clusters/{clid}/computers", headers=BOB).json()["page"]["total_items"] == 2
    # unknown computer in cluster body -> 422
    assert client.post(f"{B}/clusters", json={"name": "x", "computer_ids": ["0123456789abcdef01234567"]},
                       headers=ADMIN).status_code == 422


# ---------------------------------------------------------------- computer scripts
def test_computer_scripts_filters(client):
    cid = mk_computer(client)
    s1, s2 = mk_script(client, "s-one"), mk_script(client, "s-two")
    assert client.put(f"{B}/computers/{cid}/scripts/{s1}", json={"is_active": True}, headers=ADMIN).status_code == 201
    assert client.put(f"{B}/computers/{cid}/scripts/{s2}", json={"is_active": False}, headers=ADMIN).status_code == 201
    assert client.put(f"{B}/computers/{cid}/scripts/{s2}", json={"is_active": False}, headers=ADMIN).status_code == 204

    all_ = client.get(f"{B}/computers/{cid}/scripts", headers=ADMIN).json()
    assert all_["page"]["total_items"] == 2 and "name" not in all_["_embedded"]["scripts"][0]
    active = client.get(f"{B}/computers/{cid}/scripts?isActive=true", headers=ADMIN).json()
    assert [s["script_id"] for s in active["_embedded"]["scripts"]] == [s1]
    verbose = client.get(f"{B}/computers/{cid}/scripts?verbose", headers=ADMIN).json()
    first = verbose["_embedded"]["scripts"][0]
    assert first["name"] == "s-one" and first["storage_path"].endswith(".sh")
    assert client.delete(f"{B}/computers/{cid}/scripts/{s2}", headers=ADMIN).status_code == 204
    # deleting a script detaches it everywhere
    assert client.delete(f"{B}/scripts/{s1}", headers=ADMIN).status_code == 204
    assert client.get(f"{B}/computers/{cid}/scripts", headers=ADMIN).json()["page"]["total_items"] == 0


# ---------------------------------------------------------------- upload
def test_script_upload(client):
    sid = mk_script(client, content=None)
    links = client.get(f"{B}/scripts/{sid}", headers=ANA).json()["_links"]
    assert "content" not in links and "run" not in links
    r = client.put(f"{B}/scripts/{sid}/content", content="echo one\r\n",
                   headers={**ADMIN, "Content-Type": "text/plain"})
    assert r.status_code == 201 and r.json()["size"] == 10
    r = client.put(f"{B}/scripts/{sid}/content", files={"file": ("x.sh", b"echo two\n")}, headers=ADMIN)
    assert r.status_code == 204
    assert client.get(f"{B}/scripts/{sid}/content", headers=ANA).text == "echo two\n"
    assert client.put(f"{B}/scripts/{sid}/content", content=b"\xff\xfe", headers={**ADMIN, "Content-Type": "text/plain"}
                      ).status_code == 422
    assert client.put(f"{B}/scripts/{sid}/content", json={"a": 1}, headers=ADMIN).status_code == 415
    assert client.put(f"{B}/scripts/{sid}/content", content="echo", headers={**ANA, "Content-Type": "text/plain"}
                      ).status_code == 403
    assert "run" in client.get(f"{B}/scripts/{sid}", headers=ANA).json()["_links"]


def test_mark_for_deletion(client):
    sid = mk_script(client)
    assert client.patch(f"{B}/scripts/{sid}", json={"name": "hack"}, headers=ANA).status_code == 403
    r = client.patch(f"{B}/scripts/{sid}", json={"marked_for_deletion": True}, headers=ANA)
    assert r.status_code == 200 and r.json()["deletion_requested_by"] == "ana"
    assert client.get(f"{B}/scripts?markedForDeletion=true", headers=ADMIN).json()["page"]["total_items"] == 1


# ---------------------------------------------------------------- executions
def _setup_run(client):
    c1 = mk_computer(client, "a1", "10.0.1.1")
    c2 = mk_computer(client, "a2", "10.0.1.2")
    c3 = mk_computer(client, "z9", "10.0.9.9")
    cl = client.post(f"{B}/clusters", json={"name": "lab-a", "computer_ids": [c1, c2], "maintainers": ["ana"]},
                     headers=ADMIN).json()["id"]
    sid = mk_script(client)
    for c in (c1, c2, c3):
        client.put(f"{B}/computers/{c}/scripts/{sid}", json={"is_active": c != c2}, headers=ADMIN)
    return c1, c2, c3, cl, sid


def test_execution_flow(client):
    c1, c2, c3, cl, sid = _setup_run(client)
    # admins cannot launch scripts (role matrix)
    assert client.post(f"{B}/scripts/{sid}/executions", json={}, headers=ADMIN).status_code == 403
    # whole assigned cluster: only computers where the script is active (c1)
    r = client.post(f"{B}/scripts/{sid}/executions", json={"cluster_id": cl, "arguments": ["--dry"]}, headers=ANA)
    assert r.status_code == 202, r.text
    ex = r.json()
    assert ex["status"] == "queued" and [x["hostname"] for x in ex["results"]] == ["a1"]
    assert r.headers["location"] == f"{B}/executions/{ex['id']}"
    assert client.dispatched == [ex["id"]]
    assert client.get(f"{B}/executions/{ex['id']}", headers=ANA).status_code == 200
    assert client.get(f"{B}/executions/{ex['id']}", headers=BOB).status_code == 404
    # computer outside her clusters -> 403; script inactive there -> 422; both targets -> 422
    assert client.post(f"{B}/scripts/{sid}/executions", json={"computer_id": c3}, headers=ANA).status_code == 403
    assert client.post(f"{B}/scripts/{sid}/executions", json={"computer_id": c2}, headers=ANA).status_code == 422
    assert client.post(f"{B}/scripts/{sid}/executions", json={"computer_id": c1, "cluster_id": cl},
                       headers=ANA).status_code == 422
    # no target: all assigned computers
    assert client.post(f"{B}/scripts/{sid}/executions", json={}, headers=ANA).status_code == 202
    assert client.get(f"{B}/scripts/{sid}/executions", headers=ANA).json()["page"]["total_items"] == 2


def test_runner_down_gives_503(client, monkeypatch):
    *_, sid = _setup_run(client)

    def down(eid, timeout=5.0):
        raise dispatcher.RunnerUnavailable("down")
    monkeypatch.setattr(dispatcher, "dispatch", down)
    r = client.post(f"{B}/scripts/{sid}/executions", json={}, headers=ANA)
    assert r.status_code == 503
    ex = client.get(f"{B}/executions", headers=ANA).json()["_embedded"]["executions"][0]
    assert ex["status"] == "failed"


def test_script_requests(client):
    r = client.post(f"{B}/script-requests", json={"name": "disk-report", "description": "df -h"}, headers=ANA)
    assert r.status_code == 201
    rid = r.json()["id"]
    assert client.post(f"{B}/script-requests", json={"name": "x", "description": "y"}, headers=ADMIN).status_code == 403
    assert client.get(f"{B}/script-requests", headers=BOB).json()["page"]["total_items"] == 0
    assert client.patch(f"{B}/script-requests/{rid}", json={"status": "approved"}, headers=ADMIN).status_code == 200
    assert client.patch(f"{B}/script-requests/{rid}", json={"status": "rejected"}, headers=ADMIN).status_code == 409
    assert client.patch(f"{B}/script-requests/{rid}", json={"status": "maybe"}, headers=ADMIN).status_code == 422


# ---------------------------------------------------------------- task history (lab 4)
def seed_history(c):
    """Insert users, scripts and tasks straight into the test database."""
    import asyncio
    from datetime import datetime, timedelta, timezone

    async def go():
        db = dbmod.init_database()
        ana = (await db.users.insert_one({"username": "ana", "role": "maintainer"})).inserted_id
        bob = (await db.users.insert_one({"username": "bob", "role": "maintainer"})).inserted_id
        s = (await db.scripts.insert_one({"name": "hist-script"})).inserted_id
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await db.task_history.insert_many([
            {"id_user": ana, "id_script": s, "date": t, "state": "finished", "status": "ok", "notification": True},
            {"id_user": ana, "id_script": s, "date": t + timedelta(hours=1), "state": "active"},
            {"id_user": bob, "id_script": s},                     # no state/date -> pending
        ])
    asyncio.run(go())


def test_history_is_read_only_and_scoped(client):
    seed_history(client)
    admin = client.get(f"{B}/history", headers=ADMIN)
    assert admin.status_code == 200 and admin.json()["page"]["total_items"] == 3
    items = admin.json()["_embedded"]["history"]
    assert items[0]["date"] > items[1]["date"] and items[0]["username"] == "ana"
    assert items[2]["state"] == "pending" and items[2]["script_name"] == "hist-script"
    # a maintainer only sees their own tasks
    assert client.get(f"{B}/history", headers=ANA).json()["page"]["total_items"] == 2
    bob_task = client.get(f"{B}/history", headers=BOB).json()["_embedded"]["history"][0]
    assert client.get(bob_task["_links"]["self"]["href"], headers=ANA).status_code == 404
    assert client.get(bob_task["_links"]["self"]["href"], headers=BOB).status_code == 200
    # filters
    assert client.get(f"{B}/history?state=pending", headers=ADMIN).json()["page"]["total_items"] == 1
    assert client.get(f"{B}/history?notification=true", headers=ADMIN).json()["page"]["total_items"] == 1
    assert client.get(f"{B}/history?state=bogus", headers=ADMIN).status_code == 400
    # nothing but GET
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(f"{B}/history", headers=ADMIN).status_code == 405
    assert client.get(f"{B}/history").status_code == 401


# ---------------------------------------------------------------- job status: WebSocket + runner
def _insert(c, docs, **ids):
    import asyncio

    async def go():
        db = dbmod.init_database()
        if "ana" not in ids:
            ids["ana"] = (await db.users.insert_one({"username": "ana", "role": "maintainer"})).inserted_id
            ids["bob"] = (await db.users.insert_one({"username": "bob", "role": "maintainer"})).inserted_id
            ids["script"] = (await db.scripts.insert_one({"name": "ws-script"})).inserted_id
            ids["pc"] = (await db.computers.insert_one({"hostname": "ws-pc"})).inserted_id
        await db.task_history.insert_many([{**d(ids)} for d in docs])
    asyncio.run(go())
    return ids


def _task(user, state, **extra):
    return lambda ids: {"id_user": ids[user], "id_script": ids["script"], "id_computer": ids["pc"],
                        "state": state, **extra}


def test_notification_session_and_websocket(client):
    from starlette.websockets import WebSocketDisconnect
    ids = _insert(client, [_task("ana", "finished", status="ok", notification=False),
                           _task("ana", "finished", status="no flag"),                  # notification missing
                           _task("ana", "finished", status="old", notification=True),   # already sent
                           _task("ana", "active", notification=False),                  # not finished
                           _task("bob", "finished", status="bob's", notification=False)])
    assert client.post(f"{B}/history/notifications").status_code == 401
    assert client.post(f"{B}/history/notifications", headers=ADMIN).status_code == 403
    r = client.post(f"{B}/history/notifications", headers=ANA)
    assert r.status_code == 201
    body = r.json()
    assert body["uri"].startswith("ws://") and body["uri"].endswith(body["token"])
    path = f"{B}/history/notifications/{body['token']}"
    with client.websocket_connect(path) as ws:
        got = sorted([ws.receive_json(), ws.receive_json()], key=lambda m: m["task"]["status"])
    assert [m["event"] for m in got] == ["task.finished"] * 2
    assert [m["task"]["status"] for m in got] == ["no flag", "ok"]
    assert got[0]["task"]["username"] == "ana" and got[0]["task"]["computer"] == "ws-pc"
    # the session token is one-time
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"{B}/history/notifications/bogus"):
            pass
    # delivered tasks are now notification=true: a new session only gets what is new, never bob's
    flags = {t["status"]: t["notification"] for t in
             client.get(f"{B}/history?items_per_page=50", headers=ADMIN).json()["_embedded"]["history"]
             if t["status"]}
    assert flags == {"ok": True, "no flag": True, "old": True, "bob's": False}
    _insert(client, [_task("ana", "finished", status="newest", notification=False)], **ids)
    token = client.post(f"{B}/history/notifications", headers=ANA).json()["token"]
    with client.websocket_connect(f"{B}/history/notifications/{token}") as ws:
        assert ws.receive_json()["task"]["status"] == "newest"


def test_runner_maintains_task_history(client):
    """The runner (dry-run executor) moves tasks pending -> active -> finished with notification=false."""
    import asyncio
    from datetime import datetime
    import mongomock
    from app.ipc.runner import DryRunExecutor, Runner

    db = mongomock.MongoClient()["runner"]
    script_file = os.path.join(tempfile.mkdtemp(), "s.sh")
    open(script_file, "w").write("echo hi")
    sid = db.scripts.insert_one({"name": "s", "storage_path": script_file, "interpreter": "bash"}).inserted_id
    pcs = [db.computers.insert_one({"hostname": h, "ip_address": "10.0.0.1", "ssh_user": "maintenance"}).inserted_id
           for h in ("a", "b")]
    cluster = "64b64b64b64b64b64b64b64b"
    eid = db.executions.insert_one({
        "script_id": sid, "script_name": "s", "target": {"type": "cluster", "id": cluster}, "arguments": [],
        "requested_by": "ana", "status": "queued", "created_at": datetime.now(),
        "results": [{"computer_id": p, "hostname": h, "status": "pending"} for p, h in zip(pcs, "ab")]}).inserted_id
    runner = Runner(db, DryRunExecutor())
    runner._create_tasks(eid)
    tasks = list(db.task_history.find())
    assert len(tasks) == 2 and {t["state"] for t in tasks} == {"pending"}
    assert all(t["notification"] is False and str(t["id_cluster"]) == cluster for t in tasks)
    runner._create_tasks(eid)                                     # idempotent (recovery resubmits)
    assert db.task_history.count_documents({}) == 2
    runner.process(eid)
    tasks = list(db.task_history.find())
    assert {t["state"] for t in tasks} == {"finished"}
    assert all(t["status"].startswith("Completed successfully") and t["notification"] is False for t in tasks)
    assert db.users.find_one({"_id": tasks[0]["id_user"]})["username"] == "ana"
