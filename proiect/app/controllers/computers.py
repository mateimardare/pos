"""/api/manager/computers - stations of the cluster (FR-CLUST-01..03)."""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from .. import views
from ..common import (BASE, PageParams, conflict, flag, not_found, now, oid_or_404, oid_or_422,
                      require_json, unprocessable)
from ..db import get_db
from ..schemas import ComputerIn, ComputerPatch, ComputerScriptLink
from ..scope import assigned_computer_ids, computer_filter
from ..security import Principal, admin_only, any_user

router = APIRouter(prefix=f"{BASE}/computers", tags=["computers"])
COLL = f"{BASE}/computers"


async def _clusters_of(db, ids) -> dict:
    """Map computer_id -> list of cluster docs (one query for a whole page)."""
    result: dict = {i: [] for i in ids}
    async for c in db.clusters.find({"computer_ids": {"$in": list(ids)}}, {"name": 1, "computer_ids": 1}):
        for cid in c.get("computer_ids", []):
            if cid in result:
                result[cid].append(c)
    return result


async def _load_visible(db, principal: Principal, computer_id: str) -> dict:
    oid = oid_or_404(computer_id, "Computer")
    allowed = await assigned_computer_ids(db, principal)
    if allowed is not None and oid not in allowed:
        raise not_found("Computer")      # do not reveal stations outside your subgroups
    doc = await db.computers.find_one({"_id": oid})
    if doc is None:
        raise not_found("Computer")
    return doc


async def _represent(db, doc, principal):
    clusters = await _clusters_of(db, [doc["_id"]])
    return views.computer(doc, principal, clusters[doc["_id"]])


@router.get("", summary="List computers (maintainers: only assigned ones)")
async def list_computers(
    pg: PageParams = Depends(),
    status_: Optional[Literal["unknown", "online", "offline"]] = Query(None, alias="status"),
    principal: Principal = Depends(any_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    flt = computer_filter(await assigned_computer_ids(db, principal))
    if status_:
        flt["status"] = status_
    total = await db.computers.count_documents(flt)
    docs = await db.computers.find(flt).sort("hostname", 1).skip(pg.skip).limit(pg.items_per_page).to_list(None)
    clusters = await _clusters_of(db, [d["_id"] for d in docs])
    items = [views.computer(d, principal, clusters[d["_id"]]) for d in docs]
    extra = {"create": views.link(COLL, "POST")} if principal.is_admin else None
    return views.collection("computers", items, href=COLL, parent=BASE, page=pg.page,
                            items_per_page=pg.items_per_page, total=total,
                            query={"status": status_}, extra_links=extra)


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_json)],
             summary="Register a new computer (admin)")
async def create_computer(body: ComputerIn, response: Response,
                          principal: Principal = Depends(admin_only),
                          db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = {**body.model_dump(mode="json"), "status": "unknown", "last_seen": None,
           "scripts": [], "created_at": now(), "updated_at": now()}
    try:
        res = await db.computers.insert_one(doc)
    except DuplicateKeyError:
        raise conflict(f"A computer with hostname '{body.hostname}' already exists")
    doc["_id"] = res.inserted_id
    response.headers["Location"] = f"{COLL}/{res.inserted_id}"
    return views.computer(doc, principal)


@router.get("/{computer_id}", summary="Get one computer")
async def get_computer(computer_id: str, principal: Principal = Depends(any_user),
                       db: AsyncIOMotorDatabase = Depends(get_db)):
    return await _represent(db, await _load_visible(db, principal, computer_id), principal)


@router.put("/{computer_id}", dependencies=[Depends(require_json)],
            responses={201: {"description": "Created"}, 204: {"description": "Replaced"}},
            summary="Create or replace a computer (admin)")
async def put_computer(computer_id: str, body: ComputerIn, response: Response,
                       principal: Principal = Depends(admin_only),
                       db: AsyncIOMotorDatabase = Depends(get_db)):
    oid = oid_or_422(computer_id)
    try:
        res = await db.computers.update_one(
            {"_id": oid},
            {"$set": {**body.model_dump(mode="json"), "updated_at": now()},
             "$setOnInsert": {"status": "unknown", "last_seen": None, "scripts": [], "created_at": now()}},
            upsert=True)
    except DuplicateKeyError:
        raise conflict(f"A computer with hostname '{body.hostname}' already exists")
    if res.upserted_id is not None:
        response.status_code = status.HTTP_201_CREATED
        response.headers["Location"] = f"{COLL}/{oid}"
        return await _represent(db, await db.computers.find_one({"_id": oid}), principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{computer_id}", dependencies=[Depends(require_json)],
              summary="Partially update a computer (admin)")
async def patch_computer(computer_id: str, body: ComputerPatch,
                         principal: Principal = Depends(admin_only),
                         db: AsyncIOMotorDatabase = Depends(get_db)):
    oid = oid_or_404(computer_id, "Computer")
    changes = body.model_dump(mode="json", exclude_unset=True)
    for required in ("hostname", "ip_address", "ssh_port", "ssh_user"):
        if required in changes and changes[required] is None:
            raise unprocessable(f"'{required}' cannot be null")
    try:
        res = await db.computers.update_one({"_id": oid}, {"$set": {**changes, "updated_at": now()}})
    except DuplicateKeyError:
        raise conflict(f"A computer with hostname '{changes.get('hostname')}' already exists")
    if res.matched_count == 0:
        raise not_found("Computer")
    return await _represent(db, await db.computers.find_one({"_id": oid}), principal)


@router.delete("/{computer_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Remove a computer from the cluster (admin)")
async def delete_computer(computer_id: str, principal: Principal = Depends(admin_only),
                          db: AsyncIOMotorDatabase = Depends(get_db)):
    oid = oid_or_404(computer_id, "Computer")
    res = await db.computers.delete_one({"_id": oid})
    if res.deleted_count == 0:
        raise not_found("Computer")
    await db.clusters.update_many({"computer_ids": oid}, {"$pull": {"computer_ids": oid}})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------ navigation: computer <-> scripts
@router.get("/{computer_id}/scripts", summary="Scripts available on a computer",
            description="`?isActive=true|false` filters; `?verbose` adds names and storage paths.")
async def list_computer_scripts(
    computer_id: str,
    pg: PageParams = Depends(),
    is_active: Optional[bool] = Query(None, alias="isActive"),
    verbose: Optional[str] = Query(None, description="presence flag: ?verbose"),
    principal: Principal = Depends(any_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    doc = await _load_visible(db, principal, computer_id)
    entries = doc.get("scripts", [])
    if is_active is not None:
        entries = [e for e in entries if e.get("is_active", True) == is_active]
    total = len(entries)
    entries = entries[pg.skip: pg.skip + pg.items_per_page]
    is_verbose = flag(verbose)
    scripts = {}
    if is_verbose and entries:
        async for s in db.scripts.find({"_id": {"$in": [e["script_id"] for e in entries]}}):
            scripts[s["_id"]] = s
    items = [views.computer_script(computer_id, e, scripts.get(e["script_id"]), is_verbose) for e in entries]
    href = f"{COLL}/{computer_id}/scripts"
    return views.collection("scripts", items, href=href, parent=f"{COLL}/{computer_id}",
                            page=pg.page, items_per_page=pg.items_per_page, total=total,
                            query={"isActive": None if is_active is None else str(is_active).lower(),
                                   "verbose": "true" if is_verbose else None})


def _find_entry(doc: dict, sid) -> Optional[dict]:
    return next((e for e in doc.get("scripts", []) if e["script_id"] == sid), None)


@router.get("/{computer_id}/scripts/{script_id}", summary="One script association of a computer")
async def get_computer_script(computer_id: str, script_id: str,
                              principal: Principal = Depends(any_user),
                              db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = await _load_visible(db, principal, computer_id)
    sid = oid_or_404(script_id, "Script")
    entry = _find_entry(doc, sid)
    if entry is None:
        raise not_found("Script association")
    return views.computer_script(computer_id, entry, await db.scripts.find_one({"_id": sid}), True)


@router.put("/{computer_id}/scripts/{script_id}", dependencies=[Depends(require_json)],
            responses={201: {"description": "Attached"}, 204: {"description": "Updated"}},
            summary="Make a script available on a computer / toggle isActive (admin)")
async def put_computer_script(computer_id: str, script_id: str, body: ComputerScriptLink,
                              response: Response, principal: Principal = Depends(admin_only),
                              db: AsyncIOMotorDatabase = Depends(get_db)):
    cid = oid_or_404(computer_id, "Computer")
    sid = oid_or_404(script_id, "Script")
    if await db.scripts.count_documents({"_id": sid}) == 0:
        raise not_found("Script")
    res = await db.computers.update_one({"_id": cid, "scripts.script_id": sid},
                                        {"$set": {"scripts.$.is_active": body.is_active}})
    if res.matched_count:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    res = await db.computers.update_one(
        {"_id": cid}, {"$push": {"scripts": {"script_id": sid, "is_active": body.is_active}}})
    if res.matched_count == 0:
        raise not_found("Computer")
    response.status_code = status.HTTP_201_CREATED
    response.headers["Location"] = f"{COLL}/{computer_id}/scripts/{script_id}"
    return views.computer_script(computer_id, {"script_id": sid, "is_active": body.is_active}, None, False)


@router.delete("/{computer_id}/scripts/{script_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Detach a script from a computer (admin)")
async def delete_computer_script(computer_id: str, script_id: str,
                                 principal: Principal = Depends(admin_only),
                                 db: AsyncIOMotorDatabase = Depends(get_db)):
    cid = oid_or_404(computer_id, "Computer")
    sid = oid_or_404(script_id, "Script")
    res = await db.computers.update_one({"_id": cid, "scripts.script_id": sid},
                                        {"$pull": {"scripts": {"script_id": sid}}})
    if res.matched_count == 0:
        raise not_found("Script association")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
