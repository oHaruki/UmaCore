"""Daily database backups, run from inside the bot.

Shells out to ``pg_dump`` and keeps the N most recent dumps, so no cron entry or
VPS-side configuration is needed — enabling it is a config flag.

Works against both deployment shapes: a local PostgreSQL on the VPS (per
VPS_SETUP.md) and a managed host like Neon. Connection details are passed to
pg_dump through libpq environment variables rather than argv, so the password
never appears in the process table.

Everything here is best-effort: a backup failure logs and reports, it never
propagates into the bot's other work.
"""
import asyncio
import gzip
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

FILENAME_PREFIX = "umacore"
FILENAME_GLOB = f"{FILENAME_PREFIX}-*.sql.gz"
_TIMESTAMP_FMT = "%Y-%m-%d_%H%M%S"


@dataclass
class BackupResult:
    ok: bool
    path: Optional[Path] = None
    size_bytes: int = 0
    duration_sec: float = 0.0
    pruned: int = 0
    error: Optional[str] = None

    @property
    def size_human(self) -> str:
        n = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} GB"


def libpq_env(database_url: str) -> Dict[str, str]:
    """Translate a connection URL into libpq environment variables.

    Passing credentials this way keeps them off the command line, where anything
    on the host could read them out of ``ps``.
    """
    parsed = urlparse(database_url)
    env: Dict[str, str] = {}

    if parsed.hostname:
        env["PGHOST"] = parsed.hostname
    if parsed.port:
        env["PGPORT"] = str(parsed.port)
    if parsed.username:
        env["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)

    dbname = (parsed.path or "").lstrip("/")
    if dbname:
        env["PGDATABASE"] = dbname

    # Managed providers (Neon and friends) require TLS. Honour an explicit
    # sslmode from the URL, and default to `require` for anything not local.
    match = re.search(r"[?&]sslmode=([^&]+)", database_url)
    if match:
        env["PGSSLMODE"] = match.group(1)
    elif parsed.hostname not in ("localhost", "127.0.0.1", "::1", None):
        env["PGSSLMODE"] = "require"

    return env


def _timestamped_name(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{FILENAME_PREFIX}-{now.strftime(_TIMESTAMP_FMT)}.sql.gz"


def list_backups(backup_dir: Path) -> List[Path]:
    """Existing dumps, newest first."""
    if not backup_dir.is_dir():
        return []
    files = [p for p in backup_dir.glob(FILENAME_GLOB) if p.is_file()]
    # Sort by mtime, falling back to name so the order is stable when two dumps
    # land in the same second.
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)


def prune_backups(backup_dir: Path, keep: int) -> int:
    """Delete all but the ``keep`` newest dumps. Returns how many were removed."""
    if keep < 1:
        logger.warning(f"Backup retention of {keep} is invalid; refusing to prune")
        return 0

    removed = 0
    for stale in list_backups(backup_dir)[keep:]:
        try:
            stale.unlink()
            removed += 1
            logger.info(f"Pruned old backup {stale.name}")
        except OSError as e:
            logger.warning(f"Could not delete old backup {stale.name}: {e}")
    return removed


def find_pg_dump() -> Optional[str]:
    """Locate pg_dump, honouring an explicit override."""
    override = os.getenv("PG_DUMP_PATH")
    if override:
        return override if Path(override).exists() else None
    return shutil.which("pg_dump")


async def create_backup(database_url: str, backup_dir: Path, keep: int,
                        timeout_sec: int = 600) -> BackupResult:
    """Dump the database to a gzipped file and prune old ones.

    Writes to a ``.partial`` file and renames on success, so an interrupted dump
    can never be mistaken for a usable backup by the retention pass.
    """
    if not database_url:
        return BackupResult(ok=False, error="DATABASE_URL is not set")

    pg_dump = find_pg_dump()
    if not pg_dump:
        return BackupResult(
            ok=False,
            error="pg_dump not found. Install the postgresql client "
                  "(`sudo apt install postgresql-client`) or set PG_DUMP_PATH.",
        )

    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return BackupResult(ok=False, error=f"Cannot create {backup_dir}: {e}")

    target = backup_dir / _timestamped_name()
    partial = target.with_suffix(target.suffix + ".partial")
    started = asyncio.get_running_loop().time()

    env = {**os.environ, **libpq_env(database_url)}
    # --no-owner/--no-acl keep the dump restorable into a database owned by a
    # different role, which is what makes these usable on a fresh host.
    args = [pg_dump, "--no-owner", "--no-acl", "--format=plain"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            partial.unlink(missing_ok=True)
            return BackupResult(ok=False, error=f"pg_dump timed out after {timeout_sec}s")

        if proc.returncode != 0:
            partial.unlink(missing_ok=True)
            detail = (stderr or b"").decode("utf-8", "replace").strip()[:400]
            return BackupResult(ok=False, error=f"pg_dump exited {proc.returncode}: {detail}")

        if not stdout:
            partial.unlink(missing_ok=True)
            return BackupResult(ok=False, error="pg_dump produced no output")

        # gzip is CPU-bound; keep it off the event loop.
        await asyncio.to_thread(_write_gzip, partial, stdout)
        partial.replace(target)

    except Exception as e:
        partial.unlink(missing_ok=True)
        logger.error(f"Backup failed: {e}", exc_info=True)
        return BackupResult(ok=False, error=f"{type(e).__name__}: {e}")

    duration = asyncio.get_running_loop().time() - started
    size = target.stat().st_size
    pruned = prune_backups(backup_dir, keep)

    logger.info(
        f"💾 Backup written: {target.name} ({size:,} bytes) in {duration:.1f}s"
        + (f", pruned {pruned} old" if pruned else "")
    )
    return BackupResult(ok=True, path=target, size_bytes=size,
                       duration_sec=duration, pruned=pruned)


def _write_gzip(path: Path, payload: bytes) -> None:
    with gzip.open(path, "wb", compresslevel=6) as fh:
        fh.write(payload)
