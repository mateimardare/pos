"""Script runner - a separate process that receives jobs over IPC (FR-ASYNC-02, FR-SCR-03).

Run with:  python -m app.ipc.runner

* Listens on IPC_HOST:IPC_PORT (multiprocessing.connection, HMAC-authenticated with IPC_AUTHKEY).
* For every job it loads the execution document, runs the script on each target station over
  SSH using key authentication only (NFR-SEC-01), and writes per-station results back to MongoDB.
* It also keeps the `task_history` collection (Job Status Service): one document per execution and
  station, moving pending -> active -> finished, with notification=false after every change.
* A monitor thread probes every station's SSH port periodically and updates its status (FR-CLUST-03).
* When an execution finishes, the Notification service is called if NOTIFY_URL is set (FR-ASYNC-03).
"""
import json
import logging
import shlex
import socket
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from multiprocessing.connection import Listener
from pathlib import Path

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient

from ..config import settings

log = logging.getLogger("runner")
MAX_OUTPUT = 64 * 1024
COMMANDS = {"bash": "bash -s --", "sh": "sh -s --", "python3": "python3 -"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StationUnreachable(Exception):
    pass


# ------------------------------------------------------------------ executors
class DryRunExecutor:
    """Pretends to run the script - handy when no station containers are up."""

    def run(self, computer: dict, script_text: str, interpreter: str, args: list[str]):
        time.sleep(0.2)
        cmd = f"{COMMANDS[interpreter]} {' '.join(shlex.quote(a) for a in args)}".strip()
        return 0, f"[dry-run] {computer['ssh_user']}@{computer['ip_address']}: {cmd} ({len(script_text)} bytes)\n", ""


class SSHExecutor:
    """Streams the script to the station's interpreter over SSH; nothing is written on the station."""

    def __init__(self):
        import paramiko  # imported lazily so the REST service does not need it
        self.paramiko = paramiko
        key_path = Path(settings.ssh_key_path)
        if not key_path.is_file():
            raise SystemExit(f"SSH private key not found at {key_path} (set SSH_KEY_PATH)")
        self.key_path = str(key_path)

    def _client(self):
        client = self.paramiko.SSHClient()
        client.load_system_host_keys()
        if settings.ssh_known_hosts:
            client.load_host_keys(settings.ssh_known_hosts)
        client.set_missing_host_key_policy(self.paramiko.AutoAddPolicy() if settings.ssh_accept_unknown_hosts
                                           else self.paramiko.RejectPolicy())
        return client

    def run(self, computer: dict, script_text: str, interpreter: str, args: list[str]):
        client = self._client()
        try:
            try:
                client.connect(hostname=computer["ip_address"], port=computer.get("ssh_port", 22),
                               username=computer["ssh_user"], key_filename=self.key_path,
                               look_for_keys=False, allow_agent=False,
                               timeout=settings.ssh_timeout, banner_timeout=settings.ssh_timeout,
                               auth_timeout=settings.ssh_timeout)
            except (OSError, self.paramiko.SSHException) as exc:
                raise StationUnreachable(str(exc)) from exc
            chan = client.get_transport().open_session()
            chan.exec_command(f"{COMMANDS[interpreter]} {' '.join(shlex.quote(a) for a in args)}".strip())
            chan.sendall(script_text.encode())
            chan.shutdown_write()
            out, err = bytearray(), bytearray()
            deadline = time.monotonic() + settings.script_timeout
            while True:
                busy = False
                if chan.recv_ready():
                    out += chan.recv(65536); busy = True
                if chan.recv_stderr_ready():
                    err += chan.recv_stderr(65536); busy = True
                if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                    break
                if time.monotonic() > deadline:
                    chan.close()
                    raise TimeoutError(f"script exceeded {settings.script_timeout}s")
                if not busy:
                    time.sleep(0.05)
            return chan.recv_exit_status(), _clip(out), _clip(err)
        finally:
            client.close()


def _summary(update: dict) -> str:
    """The completion message stored in task_history.status."""
    kind = update["status"]
    if kind in ("succeeded", "failed"):
        return f"{'Completed successfully' if kind == 'succeeded' else 'Failed'} (exit code {update['exit_code']})"
    return f"{kind.capitalize()}: {update.get('stderr', '')}".strip()


def _clip(data: bytes) -> str:
    text = bytes(data[:MAX_OUTPUT]).decode("utf-8", errors="replace")
    return text + ("\n...[output truncated]" if len(data) > MAX_OUTPUT else "")


# ------------------------------------------------------------------ job processing
class Runner:
    def __init__(self, db, executor):
        self.db = db
        self.executor = executor
        self.pool = ThreadPoolExecutor(max_workers=settings.runner_threads, thread_name_prefix="job")

    def submit(self, execution_id: str) -> None:
        try:
            self._create_tasks(ObjectId(execution_id))      # visible as `pending` right away
        except Exception:
            log.exception("could not create task_history for %s", execution_id)
        self.pool.submit(self._safe_process, execution_id)

    # ------------------------------------------------------------ task_history (Job Status Service)
    def _user_id(self, username: str):
        # users come from the Auth service's tokens; make sure a document exists to reference
        self.db.users.update_one({"username": username}, {"$setOnInsert": {"role": "maintainer"}}, upsert=True)
        return self.db.users.find_one({"username": username}, {"_id": 1})["_id"]

    def _create_tasks(self, eid: ObjectId) -> None:
        ex = self.db.executions.find_one({"_id": eid})
        if ex is None or ex.get("status") != "queued":
            return
        user_id = self._user_id(ex.get("requested_by", "unknown"))
        target = ex.get("target", {})
        cluster = ObjectId(target["id"]) if target.get("type") == "cluster" and target.get("id") else None
        for r in ex["results"]:
            base = {"id_user": user_id, "id_script": ex["script_id"], "id_computer": r["computer_id"],
                    "id_execution": eid}
            if cluster:
                base["id_cluster"] = cluster
            self.db.task_history.update_one(
                {"id_execution": eid, "id_computer": r["computer_id"]},
                {"$setOnInsert": {**base, "date": utcnow(), "state": "pending", "notification": False}},
                upsert=True)

    def _task(self, eid, computer_id, state: str, status: str | None = None) -> None:
        """Record a state change; notification=false means 'not yet transmitted to the user'."""
        upd = {"state": state, "date": utcnow(), "notification": False}
        if status is not None:
            upd["status"] = status
        self.db.task_history.update_one({"id_execution": eid, "id_computer": computer_id}, {"$set": upd})

    def _safe_process(self, execution_id: str) -> None:
        try:
            self.process(ObjectId(execution_id))
        except Exception:  # never let a job kill the worker thread silently
            log.exception("execution %s crashed", execution_id)
            self.db.executions.update_one({"_id": ObjectId(execution_id), "status": {"$in": ["queued", "running"]}},
                                          {"$set": {"status": "failed", "error": "internal runner error",
                                                    "finished_at": utcnow()}})
            self.db.task_history.update_many({"id_execution": ObjectId(execution_id), "state": {"$ne": "finished"}},
                                             {"$set": {"state": "finished", "status": "Failed: internal runner error",
                                                       "date": utcnow(), "notification": False}})

    def process(self, eid: ObjectId) -> None:
        ex = self.db.executions.find_one_and_update(
            {"_id": eid, "status": "queued"}, {"$set": {"status": "running", "started_at": utcnow()}})
        if ex is None:
            return  # unknown, or already taken
        script = self.db.scripts.find_one({"_id": ex["script_id"]})
        if script is None or not script.get("storage_path") or not Path(script["storage_path"]).is_file():
            for r in ex["results"]:
                self._task(eid, r["computer_id"], "finished", "Failed: script or its content no longer exists")
            self._finish(ex, "failed", "script or its content no longer exists")
            return
        text = Path(script["storage_path"]).read_text("utf-8")
        interpreter = script.get("interpreter", "bash")

        def run_one(idx_result):
            idx, r = idx_result
            prefix = f"results.{idx}"
            self.db.executions.update_one({"_id": eid}, {"$set": {f"{prefix}.status": "running",
                                                                   f"{prefix}.started_at": utcnow()}})
            self._task(eid, r["computer_id"], "active")
            computer = self.db.computers.find_one({"_id": r["computer_id"]})
            update = {}
            if computer is None:
                update = {"status": "skipped", "stderr": "computer was removed"}
            else:
                try:
                    code, out, err = self.executor.run(computer, text, interpreter, ex.get("arguments", []))
                    update = {"status": "succeeded" if code == 0 else "failed",
                              "exit_code": code, "stdout": out, "stderr": err}
                    self._station_status(computer["_id"], True)
                except StationUnreachable as exc:
                    update = {"status": "unreachable", "stderr": str(exc)}
                    self._station_status(computer["_id"], False)
                except TimeoutError as exc:
                    update = {"status": "timeout", "stderr": str(exc)}
            update["finished_at"] = utcnow()
            self.db.executions.update_one({"_id": eid}, {"$set": {f"{prefix}.{k}": v for k, v in update.items()}})
            self._task(eid, r["computer_id"], "finished", _summary(update))
            return update["status"]

        with ThreadPoolExecutor(max_workers=8, thread_name_prefix=f"ex-{eid}") as inner:
            statuses = list(inner.map(run_one, enumerate(ex["results"])))
        ok = statuses.count("succeeded")
        overall = "succeeded" if ok == len(statuses) else ("failed" if ok == 0 else "partial")
        self._finish(ex, overall, None)

    def _finish(self, ex: dict, status: str, error: str | None) -> None:
        self.db.executions.update_one({"_id": ex["_id"]}, {"$set": {"status": status, "error": error,
                                                                    "finished_at": utcnow()}})
        log.info("execution %s -> %s", ex["_id"], status)
        self._notify(ex, status)

    def _station_status(self, computer_id, online: bool) -> None:
        upd = {"status": "online" if online else "offline"}
        if online:
            upd["last_seen"] = utcnow()
        self.db.computers.update_one({"_id": computer_id}, {"$set": upd})

    def _notify(self, ex: dict, status: str) -> None:
        if not settings.notify_url:
            return
        payload = json.dumps({"event": "execution.finished", "execution_id": str(ex["_id"]),
                              "script": ex.get("script_name"), "status": status,
                              "recipient": ex.get("requested_by"),
                              "href": f"/api/manager/executions/{ex['_id']}"}).encode()
        req = urllib.request.Request(settings.notify_url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except OSError as exc:
            log.warning("notification failed: %s", exc)

    def recover(self) -> None:
        """Jobs interrupted by a runner restart."""
        for ex in self.db.executions.find({"status": "running"}, {"_id": 1}):
            self.db.task_history.update_many({"id_execution": ex["_id"], "state": {"$ne": "finished"}},
                                             {"$set": {"state": "finished", "status": "Failed: runner restarted",
                                                       "date": utcnow(), "notification": False}})
        self.db.executions.update_many({"status": "running"}, {"$set": {
            "status": "failed", "error": "runner restarted during execution", "finished_at": utcnow()}})
        for ex in self.db.executions.find({"status": "queued"}, {"_id": 1}):
            self.submit(str(ex["_id"]))


def monitor_loop(db, interval: int) -> None:
    """FR-CLUST-03: periodic reachability probe of every station's SSH port."""
    while True:
        for c in db.computers.find({}, {"ip_address": 1, "ssh_port": 1}):
            try:
                socket.create_connection((c["ip_address"], c.get("ssh_port", 22)), timeout=3).close()
                db.computers.update_one({"_id": c["_id"]}, {"$set": {"status": "online", "last_seen": utcnow()}})
            except OSError:
                db.computers.update_one({"_id": c["_id"]}, {"$set": {"status": "offline"}})
        time.sleep(interval)


def serve() -> None:
    import os
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = MongoClient(settings.mongo_uri)[settings.mongo_db]
    executor = DryRunExecutor() if settings.runner_mode == "dry-run" else SSHExecutor()
    runner = Runner(db, executor)
    runner.recover()
    interval = int(os.getenv("MONITOR_INTERVAL", "60"))
    if interval > 0 and settings.runner_mode != "dry-run":
        threading.Thread(target=monitor_loop, args=(db, interval), daemon=True, name="monitor").start()

    address = (settings.ipc_host, settings.ipc_port)
    with Listener(address, authkey=settings.ipc_authkey) as listener:
        log.info("runner (%s mode) listening on %s:%s", settings.runner_mode, *address)
        while True:
            try:
                conn = listener.accept()
            except Exception as exc:  # bad authkey, aborted handshake...
                log.warning("rejected IPC connection: %s", exc)
                continue
            with conn:
                try:
                    msg = conn.recv()
                    if not isinstance(msg, dict) or msg.get("op") != "run":
                        conn.send({"ok": False, "error": "unknown operation"})
                        continue
                    ObjectId(msg["execution_id"])
                    conn.send({"ok": True})
                    runner.submit(msg["execution_id"])
                except (EOFError, OSError, KeyError, InvalidId, TypeError) as exc:
                    log.warning("bad IPC message: %s", exc)
                    try:
                        conn.send({"ok": False, "error": "bad message"})
                    except OSError:
                        pass


if __name__ == "__main__":
    serve()
