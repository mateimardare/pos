"""View layer: resource representations (output DTOs) with HATEOAS links.

Every representation carries at least `self` and `parent`. Extra links depend on the
caller's role and on the resource state (e.g. a script without uploaded content 
offers no `executions` link).
"""
from typing import Any, Iterable, Optional
from urllib.parse import urlencode

from .common import BASE
from .security import Principal, Role


def link(href: str, method: Optional[str] = None, **extra: Any) -> dict:
    d: dict[str, Any] = {"href": href}
    if method:
        d["type"] = method
    d.update(extra)
    return d


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------- collections
def collection(name: str, items: list[dict], *, href: str, parent: str, page: int,
               items_per_page: int, total: int, query: Optional[dict] = None,
               extra_links: Optional[dict] = None) -> dict:
    query = {k: v for k, v in (query or {}).items() if v is not None}
    last = max(1, -(-total // items_per_page))

    def page_href(p: int) -> str:
        return f"{href}?{urlencode({**query, 'page': p, 'items_per_page': items_per_page})}"

    links = {"self": link(page_href(page)), "parent": link(parent),
             "first": link(page_href(1)), "last": link(page_href(last))}
    if page > 1:
        links["prev"] = link(page_href(min(page - 1, last)))
    if page < last:
        links["next"] = link(page_href(page + 1))
    links.update(extra_links or {})
    return {
        "_embedded": {name: items},
        "page": {"number": page, "items_per_page": items_per_page,
                 "total_items": total, "total_pages": last},
        "_links": links,
    }


# ---------------------------------------------------------------- computers
def computer(doc: dict, principal: Principal, clusters: Iterable[dict] = ()) -> dict:
    cid = str(doc["_id"])
    self_href = f"{BASE}/computers/{cid}"
    links = {
        "self": link(self_href),
        "parent": link(f"{BASE}/computers"),
        "scripts": link(f"{self_href}/scripts"),
        "active-scripts": link(f"{self_href}/scripts?isActive=true"),
    }
    if principal.is_admin:
        links["replace"] = link(self_href, "PUT")
        links["update"] = link(self_href, "PATCH")
        links["delete"] = link(self_href, "DELETE")
        links["attach-script"] = link(f"{self_href}/scripts/{{scriptId}}", "PUT", templated=True)
    return {
        "id": cid,
        "hostname": doc["hostname"],
        "ip_address": doc["ip_address"],
        "ssh_port": doc.get("ssh_port", 22),
        "ssh_user": doc.get("ssh_user"),
        "description": doc.get("description"),
        "status": doc.get("status", "unknown"),
        "last_seen": _iso(doc.get("last_seen")),
        "scripts_count": len(doc.get("scripts", [])),
        # DTO aggregation: the subgroups this station belongs to
        "clusters": [{"id": str(c["_id"]), "name": c["name"],
                      "_links": {"self": link(f"{BASE}/clusters/{c['_id']}")}} for c in clusters],
        "_links": links,
    }


def computer_script(computer_id: str, entry: dict, script: Optional[dict], verbose: bool) -> dict:
    sid = str(entry["script_id"])
    rep: dict[str, Any] = {"script_id": sid, "is_active": entry.get("is_active", True)}
    if verbose and script is not None:
        rep.update({
            "name": script["name"],
            "description": script.get("description"),
            "interpreter": script.get("interpreter", "bash"),
            "filename": script.get("filename"),
            "storage_path": script.get("storage_path"),
            "has_content": bool(script.get("storage_path")),
            "marked_for_deletion": script.get("marked_for_deletion", False),
        })
    rep["_links"] = {
        "self": link(f"{BASE}/computers/{computer_id}/scripts/{sid}"),
        "parent": link(f"{BASE}/computers/{computer_id}/scripts"),
        "script": link(f"{BASE}/scripts/{sid}"),
        "computer": link(f"{BASE}/computers/{computer_id}"),
    }
    return rep


# ---------------------------------------------------------------- clusters
def cluster(doc: dict, principal: Principal, computers: Iterable[dict] = ()) -> dict:
    cid = str(doc["_id"])
    self_href = f"{BASE}/clusters/{cid}"
    links = {
        "self": link(self_href),
        "parent": link(f"{BASE}/clusters"),
        "computers": link(f"{self_href}/computers"),
    }
    if principal.role == Role.MAINTAINER:
        links["run-script"] = link(f"{BASE}/scripts/{{scriptId}}/executions", "POST", templated=True,
                                   body={"cluster_id": cid})
    if principal.is_admin:
        links["replace"] = link(self_href, "PUT")
        links["update"] = link(self_href, "PATCH")
        links["delete"] = link(self_href, "DELETE")
        links["add-computer"] = link(f"{self_href}/computers/{{computerId}}", "PUT", templated=True)
        links["assign-maintainer"] = link(f"{self_href}/maintainers/{{username}}", "PUT", templated=True)
    return {
        "id": cid,
        "name": doc["name"],
        "description": doc.get("description"),
        "maintainers": doc.get("maintainers", []),
        "computers": [{"id": str(c["_id"]), "hostname": c["hostname"],
                       "status": c.get("status", "unknown"),
                       "_links": {"self": link(f"{BASE}/computers/{c['_id']}")}} for c in computers],
        "_links": links,
    }


# ---------------------------------------------------------------- scripts
def script(doc: dict, principal: Principal) -> dict:
    sid = str(doc["_id"])
    self_href = f"{BASE}/scripts/{sid}"
    has_content = bool(doc.get("storage_path"))
    marked = doc.get("marked_for_deletion", False)
    links = {"self": link(self_href), "parent": link(f"{BASE}/scripts"),
             "executions": link(f"{self_href}/executions")}
    if has_content:
        links["content"] = link(f"{self_href}/content")
    if principal.is_admin:
        links["replace"] = link(self_href, "PUT")
        links["update"] = link(self_href, "PATCH")
        links["delete"] = link(self_href, "DELETE")
        links["upload-content"] = link(f"{self_href}/content", "PUT")
    else:
        if has_content and not marked:
            links["run"] = link(f"{self_href}/executions", "POST")
        if not marked:
            links["mark-for-deletion"] = link(self_href, "PATCH", body={"marked_for_deletion": True})
    return {
        "id": sid,
        "name": doc["name"],
        "description": doc.get("description"),
        "interpreter": doc.get("interpreter", "bash"),
        "filename": doc.get("filename"),
        "storage_path": doc.get("storage_path"),
        "size": doc.get("size"),
        "sha256": doc.get("sha256"),
        "marked_for_deletion": marked,
        "deletion_requested_by": doc.get("deletion_requested_by"),
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
        "_links": links,
    }


# ---------------------------------------------------------------- executions
def execution(doc: dict) -> dict:
    eid = str(doc["_id"])
    target = doc.get("target", {})
    links = {
        "self": link(f"{BASE}/executions/{eid}"),
        "parent": link(f"{BASE}/executions"),
        "script": link(f"{BASE}/scripts/{doc['script_id']}"),
    }
    if target.get("type") == "computer":
        links["target"] = link(f"{BASE}/computers/{target['id']}")
    elif target.get("type") == "cluster":
        links["target"] = link(f"{BASE}/clusters/{target['id']}")
    return {
        "id": eid,
        "script_id": str(doc["script_id"]),
        "script_name": doc.get("script_name"),
        "target": target,
        "arguments": doc.get("arguments", []),
        "requested_by": doc.get("requested_by"),
        "status": doc["status"],
        "error": doc.get("error"),
        "created_at": _iso(doc.get("created_at")),
        "started_at": _iso(doc.get("started_at")),
        "finished_at": _iso(doc.get("finished_at")),
        "results": [{
            "computer_id": str(r["computer_id"]),
            "hostname": r.get("hostname"),
            "status": r.get("status"),
            "exit_code": r.get("exit_code"),
            "stdout": r.get("stdout"),
            "stderr": r.get("stderr"),
            "started_at": _iso(r.get("started_at")),
            "finished_at": _iso(r.get("finished_at")),
            "_links": {"computer": link(f"{BASE}/computers/{r['computer_id']}")},
        } for r in doc.get("results", [])],
        "_links": links,
    }


# ---------------------------------------------------------------- task history
def task(doc: dict, username: Optional[str], script_name: Optional[str],
         hostname: Optional[str] = None) -> dict:
    tid = str(doc["_id"])
    return {
        "id": tid,                                      # the job id
        "id_user": str(doc["id_user"]),
        "username": username,
        "id_script": str(doc["id_script"]),
        "script_name": script_name,
        "id_computer": str(doc["id_computer"]) if doc.get("id_computer") else None,
        "computer": hostname,
        "id_cluster": str(doc["id_cluster"]) if doc.get("id_cluster") else None,   # only if a cluster was targeted
        "date": _iso(doc.get("date")),
        "state": doc.get("state", "pending"),          # missing state == pending
        "status": doc.get("status"),
        "notification": doc.get("notification"),
        "_links": {
            "self": link(f"{BASE}/history/{tid}"),
            "parent": link(f"{BASE}/history"),
            "script": link(f"{BASE}/scripts/{doc['id_script']}"),
            **({"computer": link(f"{BASE}/computers/{doc['id_computer']}")} if doc.get("id_computer") else {}),
            **({"cluster": link(f"{BASE}/clusters/{doc['id_cluster']}")} if doc.get("id_cluster") else {}),
        },
    }


# ---------------------------------------------------------------- script requests
def script_request(doc: dict, principal: Principal) -> dict:
    rid = str(doc["_id"])
    self_href = f"{BASE}/script-requests/{rid}"
    links = {"self": link(self_href), "parent": link(f"{BASE}/script-requests")}
    if principal.is_admin and doc["status"] == "pending":
        links["decide"] = link(self_href, "PATCH")
        links["create-script"] = link(f"{BASE}/scripts", "POST")
    return {
        "id": rid,
        "name": doc["name"],
        "description": doc["description"],
        "justification": doc.get("justification"),
        "requested_by": doc["requested_by"],
        "status": doc["status"],
        "decided_by": doc.get("decided_by"),
        "comment": doc.get("comment"),
        "created_at": _iso(doc.get("created_at")),
        "decided_at": _iso(doc.get("decided_at")),
        "_links": links,
    }
