# Cluster Manager – REST service (POS, Lab 3 + project)

The **Cluster Management** web service of the distributed system described in Lab 1 v2: it manages
stations (computers), subgroups (clusters), and maintenance scripts. It also launches scripts
asynchronously through a separate **runner** process, reached over IPC.

Stack: Python 3.10+, FastAPI, MongoDB (motor/pymongo), paramiko (SSH), JWT for authorization.

```
app/
  main.py              app, 415/422 error mapping, root entry point, OpenAPI
  config.py            settings from environment variables
  db.py                Model: Mongo connection + unique indexes
  schemas.py           Model: input DTOs + validation rules
  views.py             View: output DTOs with HATEOAS links (self, parent, actions)
  controllers/         Controller: computers, clusters, scripts, executions, script-requests
  security.py          JWT verification + RBAC (admin / maintainer)
  scope.py             what a maintainer may see (stations of their assigned clusters)
  ipc/dispatcher.py    REST -> runner IPC client
  ipc/runner.py        runner process: SSH execution, results, station monitoring, notifications
deploy/                Mongo users init script, station (sshd) image
tools/                 mint_token.py (dev JWTs), gen_keys.sh (runner SSH keys)
tests/                 API tests on an in-memory MongoDB
```

## Running it

**Full stack (Docker):**
```bash
./tools/gen_keys.sh
cp .env.example .env          # change the secrets
docker compose up --build
```
The API runs at `http://localhost:8000/api/manager`, with Swagger UI at `/api/manager/docs`.
The stack also starts a secured MongoDB (`--auth` and the lab's users), the runner, and two stations at
`172.28.0.11` and `172.28.0.12` (user `maintenance`, port 22).

**Locally without Docker** (for week 1 and 2, when you only need a mongod on localhost):
```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload                       # terminal 1
RUNNER_MODE=dry-run python -m app.ipc.runner        # terminal 2 (pretends to run scripts)
python -m tools.mint_token admin admin              # paste into Postman as Bearer token
python -m tools.mint_token ana maintainer
pytest -q
```

**Postman:** *Import → Link* → `http://localhost:8000/api/manager/openapi.json`. This generates the
whole collection. Set the auth type to *Bearer Token*.

## Authorization

JWTs are issued by the separate **Auth service** (NFR-ARCH-01). This service only verifies them,
using the shared `JWT_SECRET` (HS256), and reads the `sub` and `role` claims (`admin` / `maintainer`).
Until the Auth service exists, `tools/mint_token.py` issues tokens.

| Action | Admin | Maintainer |
|---|---|---|
| Create, edit and delete computers, clusters, scripts; upload script files | ✔ | ✘ |
| Assign clusters to maintainers, attach scripts to computers | ✔ | ✘ |
| View computers / clusters | all | only stations in clusters assigned to them |
| Mark a script for deletion (`PATCH {"marked_for_deletion": true}`) | ✔ | ✔ |
| Request a new script (`POST /script-requests`) | ✘ | ✔ |
| Launch scripts (`POST /scripts/{id}/executions`) | ✘ | ✔ (on assigned stations) |

## Endpoints (`/api/manager`)

| Route | Methods |
|---|---|
| `/` | GET – links to every collection |
| `/computers` | GET (`?page&items_per_page&status`), POST |
| `/computers/{id}` | GET, PUT (create/replace), PATCH, DELETE |
| `/computers/{id}/scripts` | GET (`?isActive=true|false`, `?verbose`, pagination) |
| `/computers/{id}/scripts/{scriptId}` | GET, PUT `{"is_active": bool}`, DELETE |
| `/clusters` · `/clusters/{id}` | same pattern as computers |
| `/clusters/{id}/computers[/{computerId}]` | GET · PUT / DELETE (membership) |
| `/clusters/{id}/maintainers/{username}` | PUT / DELETE (assignment) |
| `/scripts` · `/scripts/{id}` | GET (`?markedForDeletion`), POST · GET, PUT, PATCH, DELETE |
| `/scripts/{id}/content` | PUT (multipart field `file` or `text/plain` body), GET |
| `/scripts/{id}/executions` | POST → **202** + `Location`; GET |
| `/executions` · `/executions/{id}` | GET (status and per-station results) |
| `/script-requests` · `/script-requests/{id}` | GET, POST · GET, PATCH (approve/reject) |
| `/openapi.json` | GET – OpenAPI 3.1 document (public) |

### Status codes (as specified in Lab 3)
* **201** on creation (with `Location`). **204** when PUT replaces an existing resource or DELETE succeeds.
* **404** for an unknown id. Maintainers also get 404 for stations outside their clusters, so those stations are not revealed.
* **409** for a unique constraint (hostname, cluster name, script name) or an impossible state
  (running a script with no content, or one marked for deletion).
* **415** when the body is not JSON, or a mandatory property is missing, unknown or of the wrong JSON type.
* **422** when the shape is right but a value is not allowed (bad IP, hostname, port, interpreter…).
* **400** for bad query/path parameters (e.g. `page=0`). **401/403** for missing tokens or wrong roles.
* **202** for script launches. **503** (+`Retry-After`) when the runner is unreachable.

### HATEOAS
Every representation has `self` and `parent`. The other links depend on the role and the state.
For example, a script without uploaded content has no `content`/`run` link, and a script already
marked for deletion has no `mark-for-deletion` link. Collections also expose `first/prev/next/last`.
Links use the `{"href": ..., "type": "POST"}` form from the lab.

## Asynchronous execution (project)
1. A maintainer calls `POST /scripts/{id}/executions` with `{"computer_id"}`, `{"cluster_id"}` or `{}`.
   An empty body targets all their stations (FR-SCR-04). Only stations where the script is attached
   **and active** are targeted.
2. The service stores an `executions` document (`queued`). It sends the id to the runner over
   `multiprocessing.connection` (TCP, HMAC-authenticated with `IPC_AUTHKEY`) and answers **202** at once (NFR-PERF-01).
3. The runner streams the script over SSH, using key auth only, into `bash -s` / `sh -s` / `python3 -`.
   Arguments are shell-quoted, and nothing is written on the station. Up to 8 stations run in parallel.
   The runner records `exit_code/stdout/stderr` for each station. The final status is `succeeded`, `partial` or `failed`.
4. The runner also checks every station's SSH port each `MONITOR_INTERVAL` seconds and updates
   `status`/`last_seen` (FR-CLUST-03). If `NOTIFY_URL` is set, it POSTs an `execution.finished` event (FR-ASYNC-03).


## Job Status Service (task history + WebSocket)
The runner keeps one `task_history` document per execution and station (`id_user`, `id_script`,
`id_computer`, `id_cluster` if a cluster was targeted, `date`, `state` pending/active/finished, `status`
message, `notification`). The REST side is read-only:

| Route | Who | Purpose |
|---|---|---|
| `GET /history`, `GET /history/{id}` | admin: all tasks; maintainer: own | filters `state`, `scriptId`, `computerId`, `clusterId`, `notification`, `since` |
| `POST /history/notifications` | maintainer | starts a session; returns a one-time `uri` (valid `WS_SESSION_TTL`=60 s) |
| `GET /history/notifications/{token}` | the holder of the token | WebSocket upgrade |

Over the WebSocket the service polls every `WS_POLL_INTERVAL` seconds (default 2) and sends each task
**once**, when it is `finished` and its `notification` is false or missing, then sets it to true:
`{"event": "task.finished", "task": {...}}`. Admins cannot open a stream (it would consume the
notifications of the maintainers who requested the tasks); they use `GET /history`.

```bash
python -m tools.seed_history --count 60                 # mock tasks (docker: docker compose exec api python -m tools.seed_history)
python -m tools.ws_client ana                           # demo client: session + WebSocket, prints notifications
```

Keep the IPC port on the internal network only, and keep `IPC_AUTHKEY` secret.
