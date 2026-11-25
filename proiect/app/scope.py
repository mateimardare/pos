"""What a maintainer is allowed to see: stations of the subgroups assigned to them (FR-CLUST-05)."""
from typing import Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from .security import Principal


async def assigned_computer_ids(db: AsyncIOMotorDatabase, principal: Principal) -> Optional[set[ObjectId]]:
    """None means 'no restriction' (admin)."""
    if principal.is_admin:
        return None
    ids: set[ObjectId] = set()
    async for c in db.clusters.find({"maintainers": principal.username}, {"computer_ids": 1}):
        ids.update(c.get("computer_ids", []))
    return ids


def computer_filter(ids: Optional[set[ObjectId]]) -> dict:
    return {} if ids is None else {"_id": {"$in": list(ids)}}


def cluster_filter(principal: Principal) -> dict:
    return {} if principal.is_admin else {"maintainers": principal.username}
