"""Request payload schemas (input DTOs).

Validation rules map to HTTP codes (see main.validation_handler):
  * missing / unknown properties or wrong JSON types  -> 415 Unsupported Media Type
  * correct types but values not allowed (pattern...)  -> 422 Unprocessable Content
"""
from enum import Enum
from ipaddress import IPv4Address
from typing import Literal, Optional

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator

HOSTNAME_RE = r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$"
SCRIPT_NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$"
UNIX_USER_RE = r"^[a-z_][a-z0-9_-]{0,31}$"

Interpreter = Literal["bash", "sh", "python3"]


class TaskState(str, Enum):
    """State of a task in the history (lab 4, Table 1)."""
    pending = "pending"
    active = "active"
    finished = "finished"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _check_oids(values: list[str]) -> list[str]:
    for v in values:
        if not ObjectId.is_valid(v):
            raise ValueError(f"'{v}' is not a valid ObjectId")
    if len(set(values)) != len(values):
        raise ValueError("duplicate ids")
    return values


# ---------------------------------------------------------------- computers
class ComputerIn(_Strict):
    hostname: str = Field(max_length=253, pattern=HOSTNAME_RE, examples=["station-01"])
    ip_address: IPv4Address = Field(examples=["172.28.0.11"])
    ssh_port: int = Field(22, ge=1, le=65535)
    ssh_user: str = Field("maintenance", pattern=UNIX_USER_RE)
    description: Optional[str] = Field(None, max_length=500)


class ComputerPatch(_Strict):
    hostname: Optional[str] = Field(None, max_length=253, pattern=HOSTNAME_RE)
    ip_address: Optional[IPv4Address] = None
    ssh_port: Optional[int] = Field(None, ge=1, le=65535)
    ssh_user: Optional[str] = Field(None, pattern=UNIX_USER_RE)
    description: Optional[str] = Field(None, max_length=500)


class ComputerScriptLink(_Strict):
    is_active: bool = True


# ---------------------------------------------------------------- clusters
class ClusterIn(_Strict):
    name: str = Field(pattern=NAME_RE, examples=["lab-a"])
    description: Optional[str] = Field(None, max_length=500)
    computer_ids: list[str] = Field(default_factory=list)
    maintainers: list[str] = Field(default_factory=list, description="usernames from the Auth service")

    _v_ids = field_validator("computer_ids")(_check_oids)

    @field_validator("maintainers")
    @classmethod
    def _unique_maintainers(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate maintainers")
        return v


class ClusterPatch(_Strict):
    name: Optional[str] = Field(None, pattern=NAME_RE)
    description: Optional[str] = Field(None, max_length=500)


# ---------------------------------------------------------------- scripts
class ScriptIn(_Strict):
    name: str = Field(pattern=SCRIPT_NAME_RE, examples=["clean-tmp"])
    description: Optional[str] = Field(None, max_length=1000)
    interpreter: Interpreter = "bash"


class ScriptPatch(_Strict):
    """Admins may change anything; maintainers may only send {"marked_for_deletion": true}."""
    name: Optional[str] = Field(None, pattern=SCRIPT_NAME_RE)
    description: Optional[str] = Field(None, max_length=1000)
    interpreter: Optional[Interpreter] = None
    marked_for_deletion: Optional[bool] = None


# ---------------------------------------------------------------- executions
class ExecutionIn(_Strict):
    """Target: one computer, one cluster, or (neither given) every computer assigned to you."""
    computer_id: Optional[str] = None
    cluster_id: Optional[str] = None
    arguments: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("computer_id", "cluster_id")
    @classmethod
    def _oid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not ObjectId.is_valid(v):
            raise ValueError(f"'{v}' is not a valid ObjectId")
        return v

    @field_validator("arguments")
    @classmethod
    def _args(cls, v: list[str]) -> list[str]:
        for a in v:
            if len(a) > 200 or "\x00" in a:
                raise ValueError("arguments must be at most 200 characters, without NUL bytes")
        return v


# ---------------------------------------------------------------- script requests
class ScriptRequestIn(_Strict):
    """FR-SCR-05: a maintainer asks the admin to install a new script."""
    name: str = Field(pattern=SCRIPT_NAME_RE)
    description: str = Field(min_length=1, max_length=1000)
    justification: Optional[str] = Field(None, max_length=1000)


class ScriptRequestDecision(_Strict):
    status: Literal["approved", "rejected"]
    comment: Optional[str] = Field(None, max_length=1000)
