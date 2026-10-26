import os
import uuid
from concurrent import futures
from datetime import datetime, timedelta, timezone

import bcrypt
import grpc
import jwt
import pymysql
from pymysql.cursors import DictCursor

import idm_pb2
import idm_pb2_grpc

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_TTL_SECONDS = int(os.getenv("JWT_TTL_SECONDS", "3600"))

def db():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "idm_app"),
        password=os.environ["DB_PASSWORD"],
        database=os.getenv("DB_NAME", "idm"),
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
    )

def user_message(row):
    return idm_pb2.User(
        uid=str(row["uid"]),
        username=row["username"],
        role=row["role"],
        is_active=bool(row["is_active"]),
    )

def abort(context, code, message):
    context.abort(code, message)

def validate_password(context, password):
    if len(password) < 12:
        abort(context, grpc.StatusCode.INVALID_ARGUMENT, "Parola trebuie să aibă minimum 12 caractere.")
    # bcrypt folosește doar primii 72 de octeți; refuzăm parolele mai lungi în loc să le trunchiem.
    if len(password.encode()) > 72:
        abort(context, grpc.StatusCode.INVALID_ARGUMENT, "Parola poate avea maximum 72 de octeți.")

def token_from_context(context):
    values = dict(context.invocation_metadata())
    auth = values.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        abort(context, grpc.StatusCode.UNAUTHENTICATED, "Lipsește tokenul Bearer.")
    return auth[7:].strip()

def authenticated_user(context):
    token = token_from_context(context)
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        uid = claims["sub"]
    except (jwt.InvalidTokenError, KeyError):
        abort(context, grpc.StatusCode.UNAUTHENTICATED, "Token invalid sau expirat.")
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT uid, username, role, is_active FROM users WHERE uid=%s", (uid,))
            row = cur.fetchone()
    if not row or not row["is_active"]:
        abort(context, grpc.StatusCode.UNAUTHENTICATED, "Cont inexistent sau dezactivat.")
    return row

def require_role(context, *roles):
    user = authenticated_user(context)
    if user["role"] not in roles:
        abort(context, grpc.StatusCode.PERMISSION_DENIED, "Nu ai permisiunea necesară.")
    return user

class IdentityService(idm_pb2_grpc.IdentityServiceServicer):
    def Login(self, request, context):
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT uid, username, password, role, is_active FROM users WHERE username=%s",
                    (request.username,),
                )
                row = cur.fetchone()
                if row and row["is_active"] and row["password"] is None:
                    # Primul login: parola trimisă devine parola contului.
                    validate_password(context, request.password)
                    password_hash = bcrypt.hashpw(request.password.encode(), bcrypt.gensalt()).decode()
                    cur.execute(
                        "UPDATE users SET password=%s WHERE uid=%s AND password IS NULL",
                        (password_hash, row["uid"]),
                    )
                    if cur.rowcount == 1:
                        row["password"] = password_hash
                    else:
                        # Altă cerere a setat parola între timp; o citim pe cea salvată.
                        cur.execute("SELECT password FROM users WHERE uid=%s", (row["uid"],))
                        row["password"] = cur.fetchone()["password"]
        if (not row or not row["is_active"] or
            not bcrypt.checkpw(request.password.encode(), row["password"].encode())):
            abort(context, grpc.StatusCode.UNAUTHENTICATED, "Credențiale invalide sau cont dezactivat.")
        now = datetime.now(timezone.utc)
        token = jwt.encode({
            "sub": str(row["uid"]),
            "role": row["role"],
            "iat": now,
            "exp": now + timedelta(seconds=JWT_TTL_SECONDS),
        }, JWT_SECRET, algorithm="HS256")
        return idm_pb2.LoginResponse(
            access_token=token,
            expires_in_seconds=JWT_TTL_SECONDS,
            uid=str(row["uid"]),
            username=row["username"],
            role=row["role"],
        )

    def ListUsers(self, request, context):
        require_role(context, "admin")
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT uid, username, role, is_active FROM users ORDER BY username")
                rows = cur.fetchall()
        return idm_pb2.ListUsersResponse(users=[user_message(r) for r in rows])

    def CreateUser(self, request, context):
        caller = authenticated_user(context)
        role = request.role.strip().lower()
        if role not in ("admin", "maintainer", "user"):
            abort(context, grpc.StatusCode.INVALID_ARGUMENT, "Rol invalid.")
        # Only admins can create maintainers or other admins. Maintainers can create regular users only.
        if caller["role"] == "admin":
            pass
        elif caller["role"] == "maintainer" and role == "user":
            pass
        else:
            abort(context, grpc.StatusCode.PERMISSION_DENIED,
                  "Doar adminul poate crea conturi admin/maintainer; maintainerul poate crea doar user.")
        username = request.username.strip()
        if not username or len(username) > 100:
            abort(context, grpc.StatusCode.INVALID_ARGUMENT, "Username-ul trebuie să aibă 1-100 caractere.")
        uid = str(uuid.uuid4())
        # Creatorul setează doar username-ul și rolul; parola rămâne NULL până la primul login.
        try:
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO users (uid, username, password, role, is_active) VALUES (%s,%s,NULL,%s,TRUE)",
                        (uid, username, role),
                    )
                    cur.execute(
                        "SELECT uid, username, role, is_active FROM users WHERE uid=%s", (uid,)
                    )
                    row = cur.fetchone()
        except pymysql.err.IntegrityError:
            abort(context, grpc.StatusCode.ALREADY_EXISTS, "Username-ul este deja folosit.")
        return idm_pb2.UserResponse(user=user_message(row))

    def DeactivateUser(self, request, context):
        caller = require_role(context, "admin")
        if request.uid == str(caller["uid"]):
            abort(context, grpc.StatusCode.FAILED_PRECONDITION, "Nu îți poți dezactiva propriul cont.")
        return self._set_active(request.uid, False, context)

    def ActivateUser(self, request, context):
        require_role(context, "admin")
        return self._set_active(request.uid, True, context)

    def _set_active(self, uid, active, context):
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET is_active=%s WHERE uid=%s", (active, uid))
                if cur.rowcount == 0:
                    cur.execute("SELECT uid FROM users WHERE uid=%s", (uid,))
                    if not cur.fetchone():
                        abort(context, grpc.StatusCode.NOT_FOUND, "Contul nu există.")
                cur.execute("SELECT uid, username, role, is_active FROM users WHERE uid=%s", (uid,))
                row = cur.fetchone()
        return idm_pb2.UserResponse(user=user_message(row))

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    idm_pb2_grpc.add_IdentityServiceServicer_to_server(IdentityService(), server)
    host = os.getenv("GRPC_HOST", "0.0.0.0")
    port = os.getenv("GRPC_PORT", "50051")
    server.add_insecure_port(f"{host}:{port}")
    server.start()
    print(f"IDM gRPC ascultă pe {host}:{port}")
    server.wait_for_termination()

if __name__ == "__main__":
    serve()
