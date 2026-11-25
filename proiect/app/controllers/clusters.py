"""/api/manager/clusters - subgroups of stations and their maintainers (FR-CLUST-04, 05)."""
from fastapi import APIRouter, Depends, Path, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from .. import views
from ..common import (BASE, PageParams, conflict, not_found, now, oid_or_404, oid_or_422,
                      require_json, unprocessable)
from ..db import get_db
from ..schemas import ClusterIn, ClusterPatch
from ..scope import cluster_filter
from ..security import Principal, admin_only, any_user

router = APIRouter(prefix=f"{BASE}/clusters", tags=["clusters"])
COLL = f"{BASE}/clusters"
USERNAME = Path(pattern=r"^[A-Za-z0-9_.@-]{1,64}$")


async def _computers_of(db, docs: list[dict]) -> dict:
    ids = {i for d in docs for i in d.get("computer_ids", [])}
    found = {c["_id"]: c async for c in db.computers.find({"_id": {"$in": list(ids)}},
                                                           {"hostname": 1, "status": 1})}
    return {d["_id"]: [found[i] for i in d.get("computer_ids", []) if i in found] for d in docs}


async def _represent(db, doc, principal):
    return views.cluster(doc, principal, (await _computers_of(db, [doc]))[doc["_id"]])


async def _validated_computer_ids(db, ids: list[str]):
    oids = [oid_or_422(i) for i in ids]
    if oids and await db.computers.count_documents({"_id": {"$in": oids}}) != len(oids):
        raise unprocessable("computer_ids contains unknown computers")
    return oids


async def _load_visible(db, principal: Principal, cluster_id: str) -> dict:
    oid = oid_or_404(cluster_id, "Cluster")
    doc = await db.clusters.find_one({"_id": oid, **cluster_filter(principal)})
    if doc is None:
        raise not_found("Cluster")
    return doc


@router.get("", summary="List clusters (maintainers: only assigned ones)")
async def list_clusters(pg: PageParams = Depends(), principal: Principal = Depends(any_user),
                        db: AsyncIOMotorDatabase = Depends(get_db)):
    flt = cluster_filter(principal)
    total = await db.clusters.count_documents(flt)
    docs = await db.clusters.find(flt).sort("name", 1).skip(pg.skip).limit(pg.items_per_page).to_list(None)
    comps = await _computers_of(db, docs)
    items = [views.cluster(d, principal, comps[d["_id"]]) for d in docs]
    extra = {"create": views.link(COLL, "POST")} if principal.is_admin else None
    return views.collection("clusters", items, href=COLL, parent=BASE, page=pg.page,
                            items_per_page=pg.items_per_page, total=total, extra_links=extra)


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_json)],
             summary="Create a cluster (admin)")
async def create_cluster(body: ClusterIn, response: Response,
                         principal: Principal = Depends(admin_only),
                         db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = {"name": body.name, "description": body.description,
           "computer_ids": await _validated_computer_ids(db, body.computer_ids),
           "maintainers": body.maintainers, "created_at": now(), "updated_at": now()}
    try:
        res = await db.clusters.insert_one(doc)
    except DuplicateKeyError:
        raise conflict(f"A cluster named '{body.name}' already exists")
    doc["_id"] = res.inserted_id
    response.headers["Location"] = f"{COLL}/{res.inserted_id}"
    return await _represent(db, doc, principal)


@router.get("/{cluster_id}", summary="Get one cluster")
async def get_cluster(cluster_id: str, principal: Principal = Depends(any_user),
                      db: AsyncIOMotorDatabase = Depends(get_db)):
    return await _represent(db, await _load_visible(db, principal, cluster_id), principal)


@router.put("/{cluster_id}", dependencies=[Depends(require_json)],
            responses={201: {"description": "Created"}, 204: {"description": "Replaced"}},
            summary="Create or replace a cluster (admin)")
async def put_cluster(cluster_id: str, body: ClusterIn, response: Response,
                      principal: Principal = Depends(admin_only),
                      db: AsyncIOMotorDatabase = Depends(get_db)):
    oid = oid_or_422(cluster_id)
    fields = {"name": body.name, "description": body.description,
              "computer_ids": await _validated_computer_ids(db, body.computer_ids),
              "maintainers": body.maintainers, "updated_at": now()}
    try:
        res = await db.clusters.update_one({"_id": oid}, {"$set": fields,
                                                          "$setOnInsert": {"created_at": now()}},
                                           upsert=True)
    except DuplicateKeyError:
        raise conflict(f"A cluster named '{body.name}' already exists")
    if res.upserted_id is not None:
        response.status_code = status.HTTP_201_CREATED
        response.headers["Location"] = f"{COLL}/{oid}"
        return await _represent(db, await db.clusters.find_one({"_id": oid}), principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{cluster_id}", dependencies=[Depends(require_json)],
              summary="Rename / re-describe a cluster (admin)")
async def patch_cluster(cluster_id: str, body: ClusterPatch,
                        principal: Principal = Depends(admin_only),
                        db: AsyncIOMotorDatabase = Depends(get_db)):
    oid = oid_or_404(cluster_id, "Cluster")
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise unprocessable("'name' cannot be null")
    try:
        res = await db.clusters.update_one({"_id": oid}, {"$set": {**changes, "updated_at": now()}})
    except DuplicateKeyError:
        raise conflict(f"A cluster named '{changes.get('name')}' already exists")
    if res.matched_count == 0:
        raise not_found("Cluster")
    return await _represent(db, await db.clusters.find_one({"_id": oid}), principal)


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete a cluster (admin; the stations themselves are kept)")
async def delete_cluster(cluster_id: str, principal: Principal = Depends(admin_only),
                         db: AsyncIOMotorDatabase = Depends(get_db)):
    res = await db.clusters.delete_one({"_id": oid_or_404(cluster_id, "Cluster")})
    if res.deleted_count == 0:
        raise not_found("Cluster")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------ navigation: cluster <-> computers
@router.get("/{cluster_id}/computers", summary="Computers of a cluster")
async def list_cluster_computers(cluster_id: str, pg: PageParams = Depends(),
                                 principal: Principal = Depends(any_user),
                                 db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = await _load_visible(db, principal, cluster_id)
    flt = {"_id": {"$in": doc.get("computer_ids", [])}}
    total = await db.computers.count_documents(flt)
    docs = await db.computers.find(flt).sort("hostname", 1).skip(pg.skip).limit(pg.items_per_page).to_list(None)
    items = [views.computer(d, principal) for d in docs]
    return views.collection("computers", items, href=f"{COLL}/{cluster_id}/computers",
                            parent=f"{COLL}/{cluster_id}", page=pg.page,
                            items_per_page=pg.items_per_page, total=total)


@router.put("/{cluster_id}/computers/{computer_id}",
            responses={201: {"description": "Added"}, 204: {"description": "Already a member"}},
            summary="Add a computer to a cluster (admin)")
async def add_cluster_computer(cluster_id: str, computer_id: str, response: Response,
                               principal: Principal = Depends(admin_only),
                               db: AsyncIOMotorDatabase = Depends(get_db)):
    oid = oid_or_404(cluster_id, "Cluster")
    cid = oid_or_404(computer_id, "Computer")
    if await db.computers.count_documents({"_id": cid}) == 0:
        raise not_found("Computer")
    res = await db.clusters.update_one({"_id": oid}, {"$addToSet": {"computer_ids": cid}})
    if res.matched_count == 0:
        raise not_found("Cluster")
    if res.modified_count == 0:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    response.status_code = status.HTTP_201_CREATED
    response.headers["Location"] = f"{BASE}/computers/{computer_id}"
    return await _represent(db, await db.clusters.find_one({"_id": oid}), principal)


@router.delete("/{cluster_id}/computers/{computer_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Remove a computer from a cluster (admin)")
async def remove_cluster_computer(cluster_id: str, computer_id: str,
                                  principal: Principal = Depends(admin_only),
                                  db: AsyncIOMotorDatabase = Depends(get_db)):
    oid = oid_or_404(cluster_id, "Cluster")
    cid = oid_or_404(computer_id, "Computer")
    res = await db.clusters.update_one({"_id": oid, "computer_ids": cid}, {"$pull": {"computer_ids": cid}})
    if res.matched_count == 0:
        raise not_found("Cluster membership")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------ cluster <-> maintainers (FR-CLUST-05)
@router.put("/{cluster_id}/maintainers/{username}",
            responses={201: {"description": "Assigned"}, 204: {"description": "Already assigned"}},
            summary="Assign a cluster to a maintainer (admin)")
async def assign_maintainer(cluster_id: str, response: Response, username: str = USERNAME,
                            principal: Principal = Depends(admin_only),
                            db: AsyncIOMotorDatabase = Depends(get_db)):
    oid = oid_or_404(cluster_id, "Cluster")
    res = await db.clusters.update_one({"_id": oid}, {"$addToSet": {"maintainers": username}})
    if res.matched_count == 0:
        raise not_found("Cluster")
    if res.modified_count == 0:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    response.status_code = status.HTTP_201_CREATED
    return await _represent(db, await db.clusters.find_one({"_id": oid}), principal)


@router.delete("/{cluster_id}/maintainers/{username}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Unassign a maintainer (admin)")
async def unassign_maintainer(cluster_id: str, username: str = USERNAME,
                              principal: Principal = Depends(admin_only),
                              db: AsyncIOMotorDatabase = Depends(get_db)):
    oid = oid_or_404(cluster_id, "Cluster")
    res = await db.clusters.update_one({"_id": oid, "maintainers": username},
                                       {"$pull": {"maintainers": username}})
    if res.matched_count == 0:
        raise not_found("Maintainer assignment")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
