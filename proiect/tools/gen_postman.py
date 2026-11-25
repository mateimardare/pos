"""Generate a Postman collection + environment from the app's OpenAPI schema.

    python -m tools.gen_postman            # writes postman/*.json
Import both files in Postman, select the "Cluster Manager (local)" environment, run
"Auth > Use admin" or "Auth > Use maintainer", then use any request.
Ids returned by POST requests are stored automatically in {{computer_id}}, {{cluster_id}}, ...
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.main import app  # noqa: E402
from tools.mint_token import mint  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "postman")
ID_VARS = {"computer_id", "cluster_id", "script_id", "execution_id", "request_id"}
CREATED = {"computers": "computer_id", "clusters": "cluster_id", "scripts": "script_id",
           "script-requests": "request_id", "executions": "execution_id"}
SAMPLE = {
    "hostname": "station-01", "ip_address": "172.28.0.11", "ssh_port": 22, "ssh_user": "maintenance",
    "name": "lab-a", "interpreter": "bash", "is_active": True, "status": "approved",
    "description": "created from Postman", "justification": "needed for maintenance",
}


def resolve(schema, spec):
    if "$ref" in schema:
        return resolve(spec["components"]["schemas"][schema["$ref"].split("/")[-1]], spec)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return resolve(next((s for s in schema[key] if s.get("type") != "null"), schema[key][0]), spec)
    return schema


def sample_body(schema, spec):
    schema = resolve(schema, spec)
    body = {}
    for prop, ps in schema.get("properties", {}).items():
        required = prop in schema.get("required", [])
        if prop in SAMPLE and (required or prop in ("description",)):
            body[prop] = SAMPLE[prop]
        elif required:
            body[prop] = resolve(ps, spec).get("examples", [""])[0]
    return body


def folder_tests(tag, method, status_ok=(200, 201)):
    var = CREATED.get(tag)
    if method == "post" and var:
        return [f'if ([201, 202].includes(pm.response.code)) pm.environment.set("{var}", pm.response.json().id);']
    return []


def build():
    spec = app.openapi()
    folders = {}
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            tag = (op.get("tags") or ["root"])[0]
            url_path = re.sub(r"\{(\w+)\}", lambda m: "{{%s}}" % (
                m.group(1) if m.group(1) in ID_VARS else m.group(1)), path)
            query = [{"key": p["name"], "value": "", "disabled": True}
                     for p in op.get("parameters", []) if p["in"] == "query"]
            req = {"method": method.upper(),
                   "header": [], "url": {"raw": "{{base_url}}" + url_path.replace("/api/manager", "", 1) + "",
                                        "host": ["{{base_url}}"],
                                        "path": [x for x in url_path.replace("/api/manager", "", 1).split("/") if x],
                                        "query": query}}
            item = {"name": f"{method.upper()} {path.replace('/api/manager', '') or '/'}", "request": req}
            rb = op.get("requestBody", {}).get("content", {})
            if "application/json" in rb:
                req["header"].append({"key": "Content-Type", "value": "application/json"})
                req["body"] = {"mode": "raw", "raw": json.dumps(sample_body(rb["application/json"]["schema"], spec), indent=2),
                               "options": {"raw": {"language": "json"}}}
            elif "multipart/form-data" in rb or "text/plain" in rb or path.endswith("/content"):
                req["header"].append({"key": "Content-Type", "value": "text/plain"})
                req["body"] = {"mode": "raw", "raw": "echo \"hello from $(hostname)\"; whoami"}
            lines = folder_tests(tag, method)
            if lines:
                item["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": lines}}]
            folders.setdefault(tag, []).append(item)

    auth = {"name": "Auth", "item": [
        {"name": "Use admin", "request": {"method": "GET", "header": [], "url": {"raw": "{{base_url}}/", "host": ["{{base_url}}"], "path": [""]}},
         "event": [{"listen": "prerequest", "script": {"type": "text/javascript", "exec": [
             'pm.environment.set("token", pm.environment.get("admin_token"));']}}]},
        {"name": "Use maintainer", "request": {"method": "GET", "header": [], "url": {"raw": "{{base_url}}/", "host": ["{{base_url}}"], "path": [""]}},
         "event": [{"listen": "prerequest", "script": {"type": "text/javascript", "exec": [
             'pm.environment.set("token", pm.environment.get("maintainer_token"));']}}]},
    ]}
    order = ["root", "computers", "clusters", "scripts", "executions", "script-requests"]
    items = [auth] + [{"name": t, "item": folders[t]} for t in order if t in folders]
    collection = {
        "info": {"name": "Cluster Manager", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{token}}", "type": "string"}]},
        "item": items,
    }
    admin, maint = mint("admin", "admin", 24 * 30), mint("ana", "maintainer", 24 * 30)
    env = {"name": "Cluster Manager (local)", "_postman_variable_scope": "environment", "values": [
        {"key": "base_url", "value": "http://localhost:8000/api/manager", "enabled": True},
        {"key": "admin_token", "value": admin, "type": "secret", "enabled": True},
        {"key": "maintainer_token", "value": maint, "type": "secret", "enabled": True},
        {"key": "token", "value": admin, "type": "secret", "enabled": True},
        *[{"key": v, "value": "", "enabled": True} for v in sorted(ID_VARS)],
        {"key": "username", "value": "ana", "enabled": True},
    ]}
    os.makedirs(OUT, exist_ok=True)
    json.dump(collection, open(os.path.join(OUT, "cluster-manager.postman_collection.json"), "w"), indent=2)
    json.dump(env, open(os.path.join(OUT, "cluster-manager.postman_environment.json"), "w"), indent=2)
    print("written to", OUT)


if __name__ == "__main__":
    build()
