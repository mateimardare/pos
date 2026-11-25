"""Script executions (FR-SCR-02..04, FR-ASYNC-01..03).

POST /api/manager/scripts/{id}/executions  -> 202 Accepted + Location of the execution resource.
The client then polls GET /api/manager/executions/{id} (or gets a notification).
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from motor.motor_asyncio import AsyncIOMotorDatabase

from .. import views
from ..common import BASE, PageParams, conflict, not_found, now, oid_or_404, unprocessable, require_json
from ..db import get_db
from ..ipc import dispatcher
from ..schemas import ExecutionIn
from ..scope import assigned_computer_ids
from ..security import Principal, any_user, maintainer_only

router = APIRouter(tags=["executions"])
COLL = f"{BASE}/executions"


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail=detail)


@router.post(f"{BASE}/scripts/{{script_id}}/executions", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_json)],
             responses={202: {"description": "Queued; poll the Location"},
                        503: {"description": "Runner process unavailable"}},
             summary="Run a script on a computer, a cluster, or all your assigned computers (maintainer)")
async def run_script(script_id: str, body: ExecutionIn, response: Response,
                     principal: Principal = Depends(maintainer_only),
                     db: AsyncIOMotorDatabase = Depends(get_db)):
    if body.computer_id and body.cluster_id:
        raise unprocessable("Give computer_id or cluster_id, not both")
    script = await db.scripts.find_one({"_id": oid_or_404(script_id, "Script")})
    if script is None:
        raise not_found("Script")
    if not script.get("storage_path"):
        raise conflict("The script has no uploaded content yet")
    if script.get("marked_for_deletion"):
        raise conflict("The script is marked for deletion")

    assigned = await assigned_computer_ids(db, principal) or set()
    if body.computer_id:
        cid = oid_or_404(body.computer_id, "Computer")
        if cid not in assigned:
            raise _forbidden("The computer is not in any of your clusters")
        candidate_ids, target = {cid}, {"type": "computer", "id": body.computer_id}
    elif body.cluster_id:
        cluster = await db.clusters.find_one({"_id": oid_or_404(body.cluster_id, "Cluster")})
        if cluster is None:
            raise not_found("Cluster")
        if principal.username not in cluster.get("maintainers", []):
            raise _forbidden("The cluster is not assigned to you")
        candidate_ids, target = set(cluster.get("computer_ids", [])), {"type": "cluster", "id": body.cluster_id}
    else:
        candidate_ids, target = assigned, {"type": "all-assigned", "id": None}

    computers = await db.computers.find({
        "_id": {"$in": list(candidate_ids)},
        "scripts": {"$elemMatch": {"script_id": script["_id"], "is_active": True}},
    }, {"hostname": 1}).sort("hostname", 1).to_list(None)
    if not computers:
        raise unprocessable("The script is not active on any of the targeted computers")

    doc = {
        "script_id": script["_id"], "script_name": script["name"], "target": target,
        "arguments": body.arguments, "requested_by": principal.username,
        "status": "queued", "created_at": now(), "started_at": None, "finished_at": None,
        "results": [{"computer_id": c["_id"], "hostname": c["hostname"], "status": "pending"}
                    for c in computers],
    }
    res = await db.executions.insert_one(doc)
    doc["_id"] = res.inserted_id
    try:
        await run_in_threadpool(dispatcher.dispatch, str(res.inserted_id))
    except dispatcher.RunnerUnavailable as exc:
        await db.executions.update_one({"_id": res.inserted_id},
                                       {"$set": {"status": "failed", "error": str(exc), "finished_at": now()}})
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Script runner is unavailable",
                            headers={"Retry-After": "30"})
    response.headers["Location"] = f"{COLL}/{res.inserted_id}"
    return views.execution(doc)


def _scope(principal: Principal) -> dict:
    return {} if principal.is_admin else {"requested_by": principal.username}


async def _page(db, flt, pg, href, parent):
    total = await db.executions.count_documents(flt)
    docs = await db.executions.find(flt).sort("created_at", -1).skip(pg.skip).limit(pg.items_per_page).to_list(None)
    return views.collection("executions", [views.execution(d) for d in docs], href=href, parent=parent,
                            page=pg.page, items_per_page=pg.items_per_page, total=total)


@router.get(COLL, summary="List executions (maintainers: their own)")
async def list_executions(pg: PageParams = Depends(), principal: Principal = Depends(any_user),
                          db: AsyncIOMotorDatabase = Depends(get_db)):
    return await _page(db, _scope(principal), pg, COLL, BASE)


@router.get(f"{BASE}/scripts/{{script_id}}/executions", summary="Executions of one script")
async def list_script_executions(script_id: str, pg: PageParams = Depends(),
                                 principal: Principal = Depends(any_user),
                                 db: AsyncIOMotorDatabase = Depends(get_db)):
    sid = oid_or_404(script_id, "Script")
    if await db.scripts.count_documents({"_id": sid}) == 0:
        raise not_found("Script")
    return await _page(db, {"script_id": sid, **_scope(principal)}, pg,
                       f"{BASE}/scripts/{script_id}/executions", f"{BASE}/scripts/{script_id}")


@router.get(f"{COLL}/{{execution_id}}", summary="Execution status and per-computer results")
async def get_execution(execution_id: str, principal: Principal = Depends(any_user),
                        db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = await db.executions.find_one({"_id": oid_or_404(execution_id, "Execution"), **_scope(principal)})
    if doc is None:
        raise not_found("Execution")
    return views.execution(doc)
