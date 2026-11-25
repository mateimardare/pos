"""REST side of the IPC channel (FR-ASYNC-01, NFR-PERF-01).

The REST service never runs scripts itself. It stores an execution document and sends
a short message to the runner process over an authenticated local socket
(multiprocessing.connection), then answers the HTTP call immediately with 202 Accepted.
"""
import socket
from multiprocessing.connection import Client

from ..config import settings


class RunnerUnavailable(RuntimeError):
    pass


def dispatch(execution_id: str, timeout: float = 5.0) -> None:
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        with Client((settings.ipc_host, settings.ipc_port), authkey=settings.ipc_authkey) as conn:
            conn.send({"op": "run", "execution_id": execution_id})
            if not conn.poll(timeout):
                raise RunnerUnavailable("runner did not acknowledge the job")
            ack = conn.recv()
            if not ack.get("ok"):
                raise RunnerUnavailable(ack.get("error", "runner rejected the job"))
    except (OSError, EOFError) as exc:
        raise RunnerUnavailable(f"cannot reach runner at {settings.ipc_host}:{settings.ipc_port}: {exc}")
    finally:
        socket.setdefaulttimeout(old)
