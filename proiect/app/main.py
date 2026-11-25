"""Cluster Manager REST service (Lab 3 + project extensions).

Run:  uvicorn app.main:app --reload
OpenAPI document (JSON, GET):  /api/manager/openapi.json      Swagger UI: /api/manager/docs
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import views
from .common import BASE
from .controllers import clusters, computers, executions, history, script_requests, scripts
from .db import ensure_indexes, init_database
from .security import Principal, any_user

# Pydantic error types meaning "the representation does not have the required shape"
# (missing / unknown property, wrong JSON type) -> 415. Everything else is a bad value -> 422.
_SHAPE_ERRORS = {"missing", "extra_forbidden", "json_invalid", "model_type", "model_attributes_type",
                 "dict_type", "list_type", "string_type", "int_type", "int_parsing", "bool_type",
                 "bool_parsing", "float_type", "float_parsing", "none_required"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    await ensure_indexes(init_database())
    yield


app = FastAPI(
    title="Cluster Manager - REST service",
    version="1.0.0",
    description="Manages the stations, subgroups (clusters) and maintenance scripts of a simulated "
                "computer cluster. Admins manage resources; maintainers run scripts on assigned stations.",
    openapi_url=f"{BASE}/openapi.json",
    docs_url=f"{BASE}/docs",
    redoc_url=None,
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    errors = exc.errors()
    body_errors = [e for e in errors if e.get("loc", [None])[0] == "body"]
    if not body_errors:
        code = status.HTTP_400_BAD_REQUEST                       # bad query / path parameter
    elif any(e["type"] in _SHAPE_ERRORS for e in body_errors):
        code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    else:
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in errors]
    return JSONResponse(status_code=code, content={"detail": detail})


@app.get(BASE, tags=["root"], summary="Entry point with links to every collection")
async def root(principal: Principal = Depends(any_user)):
    return {
        "user": {"username": principal.username, "role": principal.role.value},
        "_links": {
            "self": views.link(BASE),
            "computers": views.link(f"{BASE}/computers"),
            "clusters": views.link(f"{BASE}/clusters"),
            "scripts": views.link(f"{BASE}/scripts"),
            "executions": views.link(f"{BASE}/executions"),
            "script-requests": views.link(f"{BASE}/script-requests"),
            "history": views.link(f"{BASE}/history"),
            "openapi": views.link(f"{BASE}/openapi.json"),
        },
    }


for module in (computers, clusters, scripts, executions, history, script_requests):
    app.include_router(module.router)
