import os
import threading
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
from uuid7 import uuid7

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_TTL_SECONDS = int(os.getenv("JWT_TTL_SECONDS", "3600"))
JWT_ISSUER = os.getenv("JWT_ISSUER", "http://localhost:50051")
ROLES = ("administrator", "maintainer")

# Serverul rulează cu un ThreadPool, deci accesul la blacklist e protejat de un lock.
BLACKLIST: set[str] = set()
BL_LOCK = threading.Lock()

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
        uid=str(uuid.UUID(bytes=row["uid"])),
        username=row["username"],
        role=row["role"],
        is_active=bool(row["is_active"]),
    )

def ok(message):
    return idm_pb2.StatusResponse(success=True, message=message)

def abort(context, code, message):
    context.abort(code, message)

def validate_password(context, password):
    if len(password) < 12:
        abort(context, grpc.StatusCode.INVALID_ARGUMENT, "Parola trebuie să aibă minimum 12 caractere.")
    # bcrypt folosește doar primii 72 de octeți; refuzăm parolele mai lungi în loc să le trunchiem.
    if len(password.encode()) > 72:
        abort(context, grpc.StatusCode.INVALID_ARGUMENT, "Parola poate avea maximum 72 de octeți.")

def blacklist(token):
    with BL_LOCK:
        BLACKLIST.add(token)

def decode_token(token):
    """Returnează claim-urile sau None. Tokenurile invalide/expirate ajung în blacklist."""
    with BL_LOCK:
        if token in BLACKLIST:
            return None
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"], issuer=JWT_ISSUER,
                          options={"require": ["iss", "sub", "exp", "jti", "role"]})
    except jwt.InvalidTokenError:
        blacklist(token)
        return None

def require_admin(context, token):
    claims = decode_token(token)
    if claims is None or claims["role"] != "administrator":
        abort(context, grpc.StatusCode.PERMISSION_DENIED, "Token invalid sau rol insuficient.")
    return claims

def password_matches(password, password_hash):
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        # hash corupt
        return False

class IdentityService(idm_pb2_grpc.IdentityServiceServicer):
    def Login(self, request, context):
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT uid, password, role, is_active FROM users WHERE username=%s",
                    (request.username,),
                )
                row = cur.fetchone()
        if not row:
            abort(context, grpc.StatusCode.UNAUTHENTICATED, "Credențiale invalide.")
        if not row["is_active"]:
            abort(context, grpc.StatusCode.PERMISSION_DENIED, "Cont inactiv.")
        if not row["password"] or not password_matches(request.password, row["password"]):
            abort(context, grpc.StatusCode.UNAUTHENTICATED, "Credențiale invalide.")
        now = datetime.now(timezone.utc)
        token = jwt.encode({
            "iss": JWT_ISSUER,
            "sub": str(uuid.UUID(bytes=row["uid"])),
            "exp": now + timedelta(seconds=JWT_TTL_SECONDS),
            "jti": str(uuid.uuid4()),
            "role": row["role"],
        }, JWT_SECRET, algorithm="HS256")
        return idm_pb2.TokenMessage(token=token)

    def ValidateToken(self, request, context):
        claims = decode_token(request.token)
        if claims is None:
            abort(context, grpc.StatusCode.UNAUTHENTICATED, "Token invalid.")
        return idm_pb2.TokenClaims(sub=claims["sub"], role=claims["role"])

    def DestroyToken(self, request, context):
        if decode_token(request.token) is None:
            abort(context, grpc.StatusCode.UNAUTHENTICATED, "Token invalid.")
        blacklist(request.token)
        return ok("Token distrus.")

    def ListUsers(self, request, context):
        require_admin(context, request.token)
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT uid, username, role, is_active FROM users ORDER BY username")
                rows = cur.fetchall()
        return idm_pb2.ListUsersResponse(users=[user_message(r) for r in rows])

    def CreateUser(self, request, context):
        require_admin(context, request.token)
        role = request.role.strip().lower()
        if role not in ROLES:
            abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Rol invalid; roluri permise: {', '.join(ROLES)}.")
        username = request.username.strip()
        if not username or len(username) > 255:
            abort(context, grpc.StatusCode.INVALID_ARGUMENT, "Username-ul trebuie să aibă 1-255 caractere.")
        # Contul se creează inactiv și fără parolă; utilizatorul îl activează prin ActivateUser.
        try:
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO users (uid, username, password, role, is_active) VALUES (%s,%s,NULL,%s,FALSE)",
                        (uuid7().bytes, username, role),
                    )
        except pymysql.err.IntegrityError:
            abort(context, grpc.StatusCode.ALREADY_EXISTS, "Username-ul este deja folosit.")
        return ok(f"Contul '{username}' a fost creat; trebuie activat cu ActivateUser.")

    def ActivateUser(self, request, context):
        validate_password(context, request.password)
        password_hash = bcrypt.hashpw(request.password.encode(), bcrypt.gensalt()).decode()
        with db() as conn:
            with conn.cursor() as cur:
                # Un singur UPDATE atomic: doar conturile inactive și fără parolă pot fi activate.
                cur.execute(
                    "UPDATE users SET password=%s, is_active=TRUE "
                    "WHERE username=%s AND is_active=FALSE AND password IS NULL",
                    (password_hash, request.username),
                )
                if cur.rowcount == 1:
                    return ok("Cont activat.")
                cur.execute("SELECT 1 FROM users WHERE username=%s", (request.username,))
                if not cur.fetchone():
                    abort(context, grpc.StatusCode.NOT_FOUND, "Contul nu există.")
        abort(context, grpc.StatusCode.PERMISSION_DENIED, "Contul e deja activ sau are deja parolă.")

    def DeactivateUser(self, request, context):
        claims = require_admin(context, request.token)
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT uid FROM users WHERE username=%s", (request.username,))
                row = cur.fetchone()
                if not row:
                    abort(context, grpc.StatusCode.NOT_FOUND, "Contul nu există.")
                if str(uuid.UUID(bytes=row["uid"])) == claims["sub"]:
                    abort(context, grpc.StatusCode.FAILED_PRECONDITION, "Nu îți poți dezactiva propriul cont.")
                cur.execute("UPDATE users SET is_active=FALSE WHERE uid=%s", (row["uid"],))
        return ok(f"Contul '{request.username}' a fost dezactivat.")

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
