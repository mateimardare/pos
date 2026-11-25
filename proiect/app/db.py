"""Model layer: MongoDB connection and indexes."""
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from .config import settings

_db: Optional[AsyncIOMotorDatabase] = None


def set_database(db: AsyncIOMotorDatabase) -> None:
    """Inject a database (used by tests)."""
    global _db
    _db = db


def init_database() -> AsyncIOMotorDatabase:
    global _db
    if _db is None:
        client = AsyncIOMotorClient(settings.mongo_uri, uuidRepresentation="standard")
        _db = client[settings.mongo_db]
    return _db


async def get_db() -> AsyncIOMotorDatabase:
    return init_database()


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.computers.create_index([("hostname", ASCENDING)], unique=True)
    await db.clusters.create_index([("name", ASCENDING)], unique=True)
    await db.clusters.create_index([("maintainers", ASCENDING)])
    await db.scripts.create_index([("name", ASCENDING)], unique=True)
    await db.executions.create_index([("requested_by", ASCENDING)])
    await db.executions.create_index([("script_id", ASCENDING)])
    await db.script_requests.create_index([("requested_by", ASCENDING)])
    await db.users.create_index([("username", ASCENDING)], unique=True)
    await db.task_history.create_index([("id_user", ASCENDING), ("date", DESCENDING)])
    await db.task_history.create_index([("id_script", ASCENDING)])
    await db.task_history.create_index([("state", ASCENDING)])
    await db.task_history.create_index([("id_execution", ASCENDING), ("id_computer", ASCENDING)])
    await db.ws_sessions.create_index([("token", ASCENDING)], unique=True)
    await db.ws_sessions.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
