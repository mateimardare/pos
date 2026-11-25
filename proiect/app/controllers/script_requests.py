"""/api/manager/script-requests - maintainers ask for new scripts (FR-SCR-05)."""
from fastapi import APIRouter, Depends, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from .. import views
from ..common import BASE, PageParams, conflict, not_found, now, oid_or_404, require_json
from ..db import get_db
from ..schemas import ScriptRequestDecision, ScriptRequestIn
from ..security import Principal, admin_only, any_user, maintainer_only

router = APIRouter(prefix=f"{BASE}/script-requests", tags=["script-requests"])
COLL = f"{BASE}/script-requests"


def _scope(p: Principal) -> dict:
    return {} if p.is_admin else {"requested_by": p.username}


@router.get("", summary="List script requests (maintainers: their own)")
async def list_requests(pg: PageParams = Depends(), principal: Principal = Depends(any_user),
                        db: AsyncIOMotorDatabase = Depends(get_db)):
    flt = _scope(principal)
    total = await db.script_requests.count_documents(flt)
    docs = await db.script_requests.find(flt).sort("created_at", -1).skip(pg.skip).limit(pg.items_per_page).to_list(None)
    extra = None if principal.is_admin else {"create": views.link(COLL, "POST")}
    return views.collection("script_requests", [views.script_request(d, principal) for d in docs],
                            href=COLL, parent=BASE, page=pg.page, items_per_page=pg.items_per_page,
                            total=total, extra_links=extra)


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_json)],
             summary="Ask the admin to install a new script (maintainer)")
async def create_request(body: ScriptRequestIn, response: Response,
                         principal: Principal = Depends(maintainer_only),
                         db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = {**body.model_dump(), "requested_by": principal.username, "status": "pending", "created_at": now()}
    res = await db.script_requests.insert_one(doc)
    doc["_id"] = res.inserted_id
    response.headers["Location"] = f"{COLL}/{res.inserted_id}"
    return views.script_request(doc, principal)


@router.get("/{request_id}", summary="Get one script request")
async def get_request(request_id: str, principal: Principal = Depends(any_user),
                      db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = await db.script_requests.find_one({"_id": oid_or_404(request_id, "Script request"), **_scope(principal)})
    if doc is None:
        raise not_found("Script request")
    return views.script_request(doc, principal)


@router.patch("/{request_id}", dependencies=[Depends(require_json)], summary="Approve / reject (admin)")
async def decide_request(request_id: str, body: ScriptRequestDecision,
                         principal: Principal = Depends(admin_only),
                         db: AsyncIOMotorDatabase = Depends(get_db)):
    oid = oid_or_404(request_id, "Script request")
    doc = await db.script_requests.find_one({"_id": oid})
    if doc is None:
        raise not_found("Script request")
    if doc["status"] != "pending":
        raise conflict(f"Request already {doc['status']}")
    await db.script_requests.update_one({"_id": oid}, {"$set": {
        "status": body.status, "comment": body.comment, "decided_by": principal.username, "decided_at": now()}})
    return views.script_request(await db.script_requests.find_one({"_id": oid}), principal)
