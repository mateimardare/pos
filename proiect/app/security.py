"""Authorization (FR-AUTH-02).

Tokens are issued by the separate Authentication service (NFR-ARCH-01).
This service only verifies them (shared HS256 secret) and enforces RBAC.
Expected claims: sub (username), role ("admin" | "maintainer"), exp.
"""
from dataclasses import dataclass
from enum import Enum

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

bearer_scheme = HTTPBearer(auto_error=False, description="JWT issued by the Auth service")


class Role(str, Enum):
    ADMIN = "admin"
    MAINTAINER = "maintainer"


@dataclass(frozen=True)
class Principal:
    username: str
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": "Bearer"})


def get_principal(creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> Principal:
    if creds is None:
        raise _unauthorized("Missing bearer token")
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_secret,
                             algorithms=[settings.jwt_algorithm],
                             options={"require": ["sub", "role", "exp"]})
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token expired")
    except jwt.PyJWTError:
        raise _unauthorized("Invalid token")
    try:
        role = Role(payload["role"])
    except ValueError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Unknown role")
    return Principal(username=str(payload["sub"]), role=role)


def require(*roles: Role):
    """Dependency factory: allow only the given roles."""
    def checker(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail=f"Role '{principal.role.value}' is not allowed to do this")
        return principal
    return checker


any_user = require(Role.ADMIN, Role.MAINTAINER)
admin_only = require(Role.ADMIN)
maintainer_only = require(Role.MAINTAINER)
