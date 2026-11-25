"""Runtime configuration, read from environment variables."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    # MongoDB - the service connects with the readWrite user (see deploy/mongo-init)
    mongo_uri: str = field(default_factory=lambda: _env("MONGO_URI", "mongodb://localhost:27017"))
    mongo_db: str = field(default_factory=lambda: _env("MONGO_DB", "cluster_manager"))

    # JWT tokens are issued by the separate Auth service; we only verify them.
    jwt_secret: str = field(default_factory=lambda: _env("JWT_SECRET", "dev-secret-change-me-please-32-bytes-min"))
    jwt_algorithm: str = "HS256"

    # Uploaded script files
    scripts_dir: str = field(default_factory=lambda: _env("SCRIPTS_DIR", "./data/scripts"))
    max_script_size: int = field(default_factory=lambda: int(_env("MAX_SCRIPT_SIZE", str(256 * 1024))))

    # IPC channel between the REST service and the script runner process
    ipc_host: str = field(default_factory=lambda: _env("IPC_HOST", "127.0.0.1"))
    ipc_port: int = field(default_factory=lambda: int(_env("IPC_PORT", "6000")))
    ipc_authkey: bytes = field(default_factory=lambda: _env("IPC_AUTHKEY", "dev-ipc-key").encode())

    # Runner (worker) settings
    runner_mode: str = field(default_factory=lambda: _env("RUNNER_MODE", "ssh"))  # ssh | dry-run
    runner_threads: int = field(default_factory=lambda: int(_env("RUNNER_THREADS", "4")))
    ssh_key_path: str = field(default_factory=lambda: _env("SSH_KEY_PATH", "./secrets/id_ed25519"))
    ssh_known_hosts: str = field(default_factory=lambda: _env("SSH_KNOWN_HOSTS", ""))
    ssh_accept_unknown_hosts: bool = field(default_factory=lambda: _env("SSH_ACCEPT_UNKNOWN_HOSTS", "0") == "1")
    ssh_timeout: int = field(default_factory=lambda: int(_env("SSH_TIMEOUT", "10")))
    script_timeout: int = field(default_factory=lambda: int(_env("SCRIPT_TIMEOUT", "300")))
    # Job Status Service: WebSocket notifications
    ws_poll_interval: float = field(default_factory=lambda: float(_env("WS_POLL_INTERVAL", "2")))
    ws_session_ttl: int = field(default_factory=lambda: int(_env("WS_SESSION_TTL", "60")))
    # Optional webhook of the Notification service, called when an execution finishes
    notify_url: str = field(default_factory=lambda: _env("NOTIFY_URL", ""))


settings = Settings()
