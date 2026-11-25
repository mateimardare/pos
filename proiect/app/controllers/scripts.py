"""/api/manager/scripts - maintenance scripts (FR-SCR-01, FR-SCR-05) + file upload (project)."""
import hashlib
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError
from starlette.datastructures import UploadFile

from .. import views
from ..common import (BASE, PageParams, conflict, not_found, now, oid_or_404, oid_or_422,
                      require_json, unprocessable)
from ..config import settings
from ..db import get_db
from ..schemas import ScriptIn, ScriptPatch
from ..security import Principal, admin_only, any_user

router = APIRouter(prefix=f"{BASE}/scripts", tags=["scripts"])
COLL = f"{BASE}/scripts"
EXT = {"bash": ".sh", "sh": ".sh", "python3": ".py"}
TEXT_TYPES = ("text/plain", "text/x-shellscript", "text/x-sh", "text/x-python",
              "application/x-sh", "application/x-shellscript", "application/octet-stream")


def _storage_dir() -> Path:
    p = Path(settings.scripts_dir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_filename(name: Optional[str], fallback: str) -> str:
    name = os.path.basename(name or "") or fallback
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:100]


@router.get("", summary="List scripts")
async def list_scripts(pg: PageParams = Depends(),
                       marked: Optional[bool] = Query(None, alias="markedForDeletion"),
                       principal: Principal = Depends(any_user),
                       db: AsyncIOMotorDatabase = Depends(get_db)):
    flt = {} if marked is None else {"marked_for_deletion": marked}
    total = await db.scripts.count_documents(flt)
    docs = await db.scripts.find(flt).sort("name", 1).skip(pg.skip).limit(pg.items_per_page).to_list(None)
    extra = {"create": views.link(COLL, "POST")} if principal.is_admin else None
    return views.collection("scripts", [views.script(d, principal) for d in docs], href=COLL, parent=BASE,
                            page=pg.page, items_per_page=pg.items_per_page, total=total,
                            query={"markedForDeletion": None if marked is None else str(marked).lower()},
                            extra_links=extra)


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_json)],
             summary="Add a script (admin). Upload its file afterwards with PUT /content.")
async def create_script(body: ScriptIn, response: Response, principal: Principal = Depends(admin_only),
                        db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = {**body.model_dump(), "marked_for_deletion": False, "storage_path": None,
           "created_by": principal.username, "created_at": now(), "updated_at": now()}
    try:
        res = await db.scripts.insert_one(doc)
    except DuplicateKeyError:
        raise conflict(f"A script named '{body.name}' already exists")
    doc["_id"] = res.inserted_id
    response.headers["Location"] = f"{COLL}/{res.inserted_id}"
    return views.script(doc, principal)


async def _load(db, script_id: str) -> dict:
    doc = await db.scripts.find_one({"_id": oid_or_404(script_id, "Script")})
    if doc is None:
        raise not_found("Script")
    return doc


@router.get("/{script_id}", summary="Get one script")
async def get_script(script_id: str, principal: Principal = Depends(any_user),
                     db: AsyncIOMotorDatabase = Depends(get_db)):
    return views.script(await _load(db, script_id), principal)


@router.put("/{script_id}", dependencies=[Depends(require_json)],
            responses={201: {"description": "Created"}, 204: {"description": "Replaced"}},
            summary="Create or replace a script's metadata (admin)")
async def put_script(script_id: str, body: ScriptIn, response: Response,
                     principal: Principal = Depends(admin_only),
                     db: AsyncIOMotorDatabase = Depends(get_db)):
    oid = oid_or_422(script_id)
    try:
        res = await db.scripts.update_one(
            {"_id": oid},
            {"$set": {**body.model_dump(), "updated_at": now()},
             "$setOnInsert": {"marked_for_deletion": False, "storage_path": None,
                              "created_by": principal.username, "created_at": now()}},
            upsert=True)
    except DuplicateKeyError:
        raise conflict(f"A script named '{body.name}' already exists")
    if res.upserted_id is not None:
        response.status_code = status.HTTP_201_CREATED
        response.headers["Location"] = f"{COLL}/{oid}"
        return views.script(await db.scripts.find_one({"_id": oid}), principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{script_id}", dependencies=[Depends(require_json)],
              summary="Update a script (admin) or mark it for deletion (maintainer, FR-SCR-05)")
async def patch_script(script_id: str, body: ScriptPatch, principal: Principal = Depends(any_user),
                       db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = await _load(db, script_id)
    changes = body.model_dump(exclude_unset=True)
    if not principal.is_admin:
        if changes != {"marked_for_deletion": True}:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail='Maintainers may only send {"marked_for_deletion": true}')
        changes["deletion_requested_by"] = principal.username
    else:
        for required in ("name", "interpreter", "marked_for_deletion"):
            if required in changes and changes[required] is None:
                raise unprocessable(f"'{required}' cannot be null")
        if changes.get("marked_for_deletion") is False:
            changes["deletion_requested_by"] = None
    try:
        await db.scripts.update_one({"_id": doc["_id"]}, {"$set": {**changes, "updated_at": now()}})
    except DuplicateKeyError:
        raise conflict(f"A script named '{changes.get('name')}' already exists")
    return views.script(await db.scripts.find_one({"_id": doc["_id"]}), principal)


@router.delete("/{script_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a script (admin)")
async def delete_script(script_id: str, principal: Principal = Depends(admin_only),
                        db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = await _load(db, script_id)
    await db.scripts.delete_one({"_id": doc["_id"]})
    await db.computers.update_many({"scripts.script_id": doc["_id"]},
                                   {"$pull": {"scripts": {"script_id": doc["_id"]}}})
    if doc.get("storage_path"):
        Path(doc["storage_path"]).unlink(missing_ok=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------ script file (project task)
_UPLOAD_DOC = {"requestBody": {"required": True, "content": {
    "multipart/form-data": {"schema": {"type": "object", "required": ["file"],
                                       "properties": {"file": {"type": "string", "format": "binary"}}}},
    "text/plain": {"schema": {"type": "string"}},
}}}


@router.put("/{script_id}/content", openapi_extra=_UPLOAD_DOC,
            responses={201: {"description": "Uploaded"}, 204: {"description": "Replaced"},
                       413: {"description": "File too large"}, 415: {"description": "Not a text upload"}},
            summary="Upload the script's text file (admin) - multipart field `file` or raw text body")
async def upload_content(script_id: str, request: Request, response: Response,
                         principal: Principal = Depends(admin_only),
                         db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = await _load(db, script_id)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    filename = None
    if ctype == "multipart/form-data":
        form = await request.form(max_files=1, max_fields=1)
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                                detail="multipart body must contain a 'file' part")
        data = await upload.read(settings.max_script_size + 1)
        filename = upload.filename
    elif ctype.startswith("text/") or ctype in TEXT_TYPES:
        data = await request.body()
    else:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail="Send multipart/form-data (field 'file') or text/plain")
    if len(data) > settings.max_script_size:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            detail=f"Script larger than {settings.max_script_size} bytes")
    if not data.strip():
        raise unprocessable("Script file is empty")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise unprocessable("Script file must be UTF-8 text")
    if b"\x00" in data:
        raise unprocessable("Script file must not contain NUL bytes")

    target = _storage_dir() / f"{doc['_id']}{EXT.get(doc.get('interpreter', 'bash'), '.txt')}"
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(data.replace(b"\r\n", b"\n"))
    os.replace(tmp, target)

    first_upload = not doc.get("storage_path")
    await db.scripts.update_one({"_id": doc["_id"]}, {"$set": {
        "storage_path": str(target),
        "filename": _safe_filename(filename, f"{doc['name']}{target.suffix}"),
        "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "content_uploaded_at": now(), "updated_at": now()}})
    if first_upload:
        response.status_code = status.HTTP_201_CREATED
        response.headers["Location"] = f"{COLL}/{script_id}/content"
        return views.script(await db.scripts.find_one({"_id": doc["_id"]}), principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{script_id}/content", response_class=PlainTextResponse,
            summary="Download the script's text")
async def get_content(script_id: str, principal: Principal = Depends(any_user),
                      db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = await _load(db, script_id)
    path = doc.get("storage_path")
    if not path or not Path(path).is_file():
        raise not_found("Script content")
    return PlainTextResponse(Path(path).read_text("utf-8"),
                             headers={"Content-Disposition": f'inline; filename="{doc.get("filename")}"'})
