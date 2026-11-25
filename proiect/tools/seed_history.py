"""Fill the `users`, `scripts`/`computers` (if needed) and `task_history` collections with mock data (lab 4).

    python -m tools.seed_history [--count 60]
In Docker:  docker compose exec api python -m tools.seed_history

Safe to run again: task_history is rebuilt from scratch, users are upserted by username,
and mock scripts are created only when fewer than 3 scripts exist.
"""
import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import settings  # noqa: E402

USERS = [("admin", "admin"), ("ana", "maintainer"), ("bob", "maintainer"), ("carla", "maintainer")]
MOCK_SCRIPTS = ["mock-clean-tmp", "mock-update-packages", "mock-disk-report"]
MESSAGES = ["Completed successfully", "Completed with warnings", "Failed: exit code 1",
            "Failed: station unreachable", "Cancelled by user"]


def seed(db, count: int = 60, rng: random.Random | None = None) -> int:
    rng = rng or random.Random()
    for username, role in USERS:
        db.users.update_one({"username": username}, {"$setOnInsert": {"role": role}}, upsert=True)
    users = list(db.users.find({}, {"_id": 1}))
    if db.scripts.count_documents({}) < 3:
        for name in MOCK_SCRIPTS:
            db.scripts.update_one({"name": name}, {"$setOnInsert": {
                "name": name, "description": "mock script (history seed)", "interpreter": "bash",
                "marked_for_deletion": False, "created_at": datetime.now(timezone.utc)}}, upsert=True)
    scripts = list(db.scripts.find({}, {"_id": 1}))
    if db.computers.count_documents({}) == 0:
        for i in range(1, 4):
            db.computers.update_one({"hostname": f"mock-station-{i:02d}"}, {"$setOnInsert": {
                "hostname": f"mock-station-{i:02d}", "ip_address": f"10.99.0.{i}", "ssh_port": 22,
                "ssh_user": "maintenance", "status": "unknown", "scripts": []}}, upsert=True)
    computers = list(db.computers.find({}, {"_id": 1}))
    clusters = list(db.clusters.find({}, {"_id": 1}))

    now = datetime.now(timezone.utc)
    docs = []
    for _ in range(count):
        doc = {"id_user": rng.choice(users)["_id"], "id_script": rng.choice(scripts)["_id"],
               "id_computer": rng.choice(computers)["_id"]}
        if clusters and rng.random() < 0.5:            # id_cluster only when a cluster was targeted
            doc["id_cluster"] = rng.choice(clusters)["_id"]
        state = rng.choices(["pending", "active", "finished", None], weights=[2, 2, 6, 1])[0]
        if state:                                      # `state` may be missing (== pending)
            doc["state"] = state
        if state != "pending" or rng.random() < 0.3:   # `date` may be missing
            doc["date"] = now - timedelta(minutes=rng.randint(0, 7 * 24 * 60))
        if state == "finished":                        # `status` only once the task is finished
            doc["status"] = rng.choice(MESSAGES)
        if rng.random() < 0.8:                         # `notification` may be missing
            doc["notification"] = rng.random() < 0.5
        docs.append(doc)
    db.task_history.delete_many({})
    if docs:
        db.task_history.insert_many(docs)
    return len(docs)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=60)
    n = ap.parse_args().count
    client = MongoClient(settings.mongo_uri)
    print(f"inserted {seed(client[settings.mongo_db], n)} mock task_history documents")
