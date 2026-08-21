from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from sqlalchemy.engine import make_url


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import models  # noqa: E402,F401
from app.database import Base  # noqa: E402
from app.services.database_sync import (  # noqa: E402
    create_sync_engine,
    normalize_database_url,
    safe_database_label,
    sync_databases,
    verify_database_connection,
)


ENV_FILE = BACKEND_ROOT / ".env"
DEFAULT_LOCAL_URL = "sqlite:///./data/moneymoney-backend.db"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def replace_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replacement = f"{key}={value}"
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.lstrip().startswith(f"{key}="):
            updated.append(replacement)
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(replacement)

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write("\n".join(updated) + "\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def backup_sqlite_database(database_url: str) -> Path | None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        return None
    database_path = Path(url.database)
    if not database_path.exists() or database_path.stat().st_size == 0:
        return None
    backup_dir = BACKEND_ROOT / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{database_path.stem}-{timestamp}{database_path.suffix}"
    shutil.copy2(database_path, backup_path)
    return backup_path


def configured_url(name: str, file_values: dict[str, str], default: str = "") -> str:
    return os.getenv(name) or file_values.get(name) or default


def discover_railway_remote_url() -> str:
    executable = shutil.which("railway.cmd" if os.name == "nt" else "railway")
    if not executable:
        return ""
    service = os.getenv("RAILWAY_POSTGRES_SERVICE", "Postgres")
    try:
        completed = subprocess.run(
            [executable, "variables", "--service", service, "--json"],
            cwd=BACKEND_ROOT.parent,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        values = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return ""
    # Railway's DATABASE_URL normally uses a private *.railway.internal host,
    # which cannot be reached from a developer computer. Only use its public URL.
    return str(values.get("DATABASE_PUBLIC_URL") or "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synchronize the active database, then safely switch DATABASE_URL."
    )
    parser.add_argument("--to", choices=("local", "remote"), required=True)
    parser.add_argument("--env-file", type=Path, default=ENV_FILE)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check both connections without copying data or changing .env.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_file = args.env_file.resolve()
    file_values = read_env_file(env_file)
    current_raw = configured_url("DATABASE_URL", file_values)
    local_raw = configured_url("LOCAL_DATABASE_URL", file_values, DEFAULT_LOCAL_URL)
    remote_raw = configured_url("REMOTE_DATABASE_URL", file_values)
    if args.to == "remote" and not remote_raw:
        remote_raw = discover_railway_remote_url()
        if remote_raw:
            print("Remote URL: discovered from the linked Railway Postgres service")
    target_raw = local_raw if args.to == "local" else remote_raw

    if not current_raw:
        print(f"ERROR: DATABASE_URL is not configured in {env_file}", file=sys.stderr)
        return 2
    if not target_raw:
        print(
            f"ERROR: {'LOCAL' if args.to == 'local' else 'REMOTE'}_DATABASE_URL is not configured",
            file=sys.stderr,
        )
        return 2

    current_url = normalize_database_url(current_raw, base_dir=BACKEND_ROOT)
    target_url = normalize_database_url(target_raw, base_dir=BACKEND_ROOT)
    print(f"Current: {safe_database_label(current_url)}")
    print(f"Target:  {safe_database_label(target_url)}")

    if make_url(current_url) == make_url(target_url):
        print(f"Already using the {args.to} database; no switch is needed.")
        return 0

    if args.dry_run:
        for label, database_url in (("current", current_url), ("target", target_url)):
            engine = create_sync_engine(database_url)
            try:
                verify_database_connection(engine)
                print(f"{label} connection: OK")
            finally:
                engine.dispose()
        print("Dry run complete; no data or settings were changed.")
        return 0

    print("Synchronizing current database into target (target-only rows are preserved)...")
    backup = backup_sqlite_database(target_url)
    if backup:
        print(f"Local backup: {backup}")
    sync_result = sync_databases(
        current_url,
        target_url,
        metadata=Base.metadata,
        batch_size=args.batch_size,
    )
    replace_env_value(env_file, "DATABASE_URL", target_raw)
    print(
        f"Synchronized {sync_result.total_rows} rows across "
        f"{len(sync_result.tables)} tables."
    )
    if sync_result.skipped_tables:
        print("Source did not contain: " + ", ".join(sync_result.skipped_tables))
    print(f"Switched DATABASE_URL to {args.to}. Restart the backend now.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from error
