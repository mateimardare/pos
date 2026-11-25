"""Dev helper until the Auth service exists: print a JWT for Postman.

    python -m tools.mint_token admin admin
    python -m tools.mint_token ana maintainer --hours 8
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import jwt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import settings  # noqa: E402


def mint(username: str, role: str, hours: float = 8) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=hours)
    return jwt.encode({"sub": username, "role": role, "exp": exp}, settings.jwt_secret,
                      algorithm=settings.jwt_algorithm)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("username")
    ap.add_argument("role", choices=["admin", "maintainer"])
    ap.add_argument("--hours", type=float, default=8)
    a = ap.parse_args()
    print(mint(a.username, a.role, a.hours))
