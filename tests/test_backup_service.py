"""Tests for the daily database backup.

pg_dump itself is stubbed, so these cover the parts that are ours: credential
handling, gzip output, the atomic rename, retention, and every failure path.
"""
import asyncio
import gzip
import sys
from pathlib import Path

import pytest

from services import backup_service
from services.backup_service import (
    BackupResult, create_backup, find_pg_dump, libpq_env, list_backups,
    prune_backups,
)

NEON = "postgresql://u:p%40ss@ep-x.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
LOCAL = "postgresql://umacore:pw@localhost:5432/umacore"
REMOTE_PLAIN = "postgresql://u:pw@db.example.com/umacore"


class TestConnectionEnv:
    def test_parses_neon_url(self):
        env = libpq_env(NEON)
        assert env["PGHOST"] == "ep-x.eu-central-1.aws.neon.tech"
        assert env["PGDATABASE"] == "neondb"
        assert env["PGSSLMODE"] == "require"

    def test_url_encoded_password_is_decoded(self):
        assert libpq_env(NEON)["PGPASSWORD"] == "p@ss"

    def test_local_url_gets_no_forced_ssl(self):
        env = libpq_env(LOCAL)
        assert env["PGPORT"] == "5432"
        assert "PGSSLMODE" not in env

    def test_remote_url_without_sslmode_defaults_to_require(self):
        """Managed providers reject plaintext; don't silently try it."""
        assert libpq_env(REMOTE_PLAIN)["PGSSLMODE"] == "require"

    def test_missing_pieces_are_omitted_not_blank(self):
        env = libpq_env("postgresql:///umacore")
        assert env == {"PGDATABASE": "umacore"}


class TestRetention:
    def _make(self, d: Path, names):
        import os, time
        for i, n in enumerate(names):
            p = d / n
            p.write_bytes(b"x")
            os.utime(p, (time.time() + i, time.time() + i))   # ascending mtime
        return d

    def test_lists_newest_first(self, tmp_path):
        self._make(tmp_path, ["umacore-2026-07-01_000000.sql.gz",
                              "umacore-2026-07-02_000000.sql.gz",
                              "umacore-2026-07-03_000000.sql.gz"])
        names = [p.name for p in list_backups(tmp_path)]
        assert names[0] == "umacore-2026-07-03_000000.sql.gz"
        assert names[-1] == "umacore-2026-07-01_000000.sql.gz"

    def test_keeps_only_the_newest_n(self, tmp_path):
        self._make(tmp_path, [f"umacore-2026-07-{d:02d}_000000.sql.gz"
                              for d in range(1, 11)])
        assert prune_backups(tmp_path, 7) == 3
        remaining = [p.name for p in list_backups(tmp_path)]
        assert len(remaining) == 7
        assert "umacore-2026-07-10_000000.sql.gz" in remaining
        assert "umacore-2026-07-01_000000.sql.gz" not in remaining

    def test_noop_when_under_the_limit(self, tmp_path):
        self._make(tmp_path, ["umacore-2026-07-01_000000.sql.gz"])
        assert prune_backups(tmp_path, 7) == 0
        assert len(list_backups(tmp_path)) == 1

    def test_refuses_to_prune_everything(self, tmp_path):
        """keep=0 would wipe the directory; treat it as a misconfiguration."""
        self._make(tmp_path, ["umacore-2026-07-01_000000.sql.gz"])
        assert prune_backups(tmp_path, 0) == 0
        assert len(list_backups(tmp_path)) == 1

    def test_ignores_unrelated_files(self, tmp_path):
        (tmp_path / "notes.txt").write_text("keep me")
        (tmp_path / "umacore-2026-07-01_000000.sql.gz.partial").write_bytes(b"x")
        self._make(tmp_path, [f"umacore-2026-07-{d:02d}_000000.sql.gz"
                              for d in range(2, 12)])
        prune_backups(tmp_path, 3)
        assert (tmp_path / "notes.txt").exists()
        # A partial from an interrupted run must never be counted as a backup.
        assert (tmp_path / "umacore-2026-07-01_000000.sql.gz.partial").exists()

    def test_missing_directory_is_empty_not_an_error(self, tmp_path):
        assert list_backups(tmp_path / "nope") == []


class TestFindPgDump:
    def test_explicit_path_is_used(self, monkeypatch):
        monkeypatch.setenv("PG_DUMP_PATH", sys.executable)
        assert find_pg_dump() == sys.executable

    def test_explicit_path_that_does_not_exist_is_rejected(self, monkeypatch):
        monkeypatch.setenv("PG_DUMP_PATH", "/definitely/not/here/pg_dump")
        assert find_pg_dump() is None


# --------------------------------------------------------------------------- #
# create_backup, with pg_dump stubbed
# --------------------------------------------------------------------------- #

DUMP_SQL = b"-- PostgreSQL database dump\nCREATE TABLE clubs (club_id uuid);\n" * 50


class FakeProc:
    def __init__(self, stdout=b"", stderr=b"", returncode=0, hang=False):
        self._out, self._err, self.returncode, self._hang = stdout, stderr, returncode, hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(30)
        return self._out, self._err

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


@pytest.fixture
def stub_pg_dump(monkeypatch):
    """Replace pg_dump with a controllable fake. Yields a setter."""
    monkeypatch.setenv("PG_DUMP_PATH", sys.executable)
    captured = {}

    def install(proc: FakeProc):
        async def fake_exec(*args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs.get("env") or {}
            return proc
        monkeypatch.setattr(backup_service.asyncio, "create_subprocess_exec", fake_exec)
        return captured

    return install


def run(coro):
    return asyncio.run(coro)


class TestCreateBackupSuccess:
    def test_writes_a_readable_gzip(self, tmp_path, stub_pg_dump):
        stub_pg_dump(FakeProc(stdout=DUMP_SQL))
        res = run(create_backup(LOCAL, tmp_path, keep=7))

        assert res.ok, res.error
        assert res.path.exists() and res.path.suffix == ".gz"
        with gzip.open(res.path, "rb") as fh:
            assert fh.read() == DUMP_SQL

    def test_reports_size_and_compresses(self, tmp_path, stub_pg_dump):
        stub_pg_dump(FakeProc(stdout=DUMP_SQL))
        res = run(create_backup(LOCAL, tmp_path, keep=7))
        assert 0 < res.size_bytes < len(DUMP_SQL), "dump was not compressed"
        assert res.size_human.endswith(("B", "KB", "MB"))

    def test_leaves_no_partial_file(self, tmp_path, stub_pg_dump):
        stub_pg_dump(FakeProc(stdout=DUMP_SQL))
        run(create_backup(LOCAL, tmp_path, keep=7))
        assert list(tmp_path.glob("*.partial")) == []

    def test_creates_the_directory(self, tmp_path, stub_pg_dump):
        stub_pg_dump(FakeProc(stdout=DUMP_SQL))
        target = tmp_path / "nested" / "backups"
        res = run(create_backup(LOCAL, target, keep=7))
        assert res.ok and target.is_dir()

    def test_prunes_within_the_same_run(self, tmp_path, stub_pg_dump):
        import os, time
        for d in range(1, 8):
            p = tmp_path / f"umacore-2026-07-{d:02d}_000000.sql.gz"
            p.write_bytes(b"old")
            os.utime(p, (time.time() - 10_000 + d, time.time() - 10_000 + d))
        stub_pg_dump(FakeProc(stdout=DUMP_SQL))
        res = run(create_backup(LOCAL, tmp_path, keep=7))
        assert res.ok
        assert res.pruned == 1
        assert len(list_backups(tmp_path)) == 7

    def test_credentials_never_reach_the_command_line(self, tmp_path, stub_pg_dump):
        """Anything on the host can read argv out of `ps`."""
        captured = stub_pg_dump(FakeProc(stdout=DUMP_SQL))
        run(create_backup(NEON, tmp_path, keep=7))

        joined = " ".join(str(a) for a in captured["args"])
        assert "p@ss" not in joined
        assert "p%40ss" not in joined
        assert captured["env"]["PGPASSWORD"] == "p@ss"      # passed via env instead


class TestCreateBackupFailures:
    def test_missing_pg_dump_is_reported_actionably(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PG_DUMP_PATH", "/nope/pg_dump")
        res = run(create_backup(LOCAL, tmp_path, keep=7))
        assert not res.ok
        assert "pg_dump not found" in res.error
        assert "postgresql-client" in res.error       # tells the operator the fix

    def test_missing_database_url(self, tmp_path):
        res = run(create_backup("", tmp_path, keep=7))
        assert not res.ok and "DATABASE_URL" in res.error

    def test_nonzero_exit_reports_stderr_and_cleans_up(self, tmp_path, stub_pg_dump):
        stub_pg_dump(FakeProc(stderr=b"FATAL: role does not exist", returncode=1))
        res = run(create_backup(LOCAL, tmp_path, keep=7))
        assert not res.ok
        assert "role does not exist" in res.error
        assert list(tmp_path.glob("*")) == [], "left a file behind after failure"

    def test_empty_output_is_a_failure_not_an_empty_backup(self, tmp_path, stub_pg_dump):
        stub_pg_dump(FakeProc(stdout=b""))
        res = run(create_backup(LOCAL, tmp_path, keep=7))
        assert not res.ok and "no output" in res.error
        assert list_backups(tmp_path) == []

    def test_timeout_kills_the_process_and_cleans_up(self, tmp_path, stub_pg_dump):
        proc = FakeProc(hang=True)
        stub_pg_dump(proc)
        res = run(create_backup(LOCAL, tmp_path, keep=7, timeout_sec=1))
        assert not res.ok and "timed out" in res.error
        assert proc.killed, "hung pg_dump was not killed"
        assert list(tmp_path.glob("*.partial")) == []

    def test_a_failed_run_never_deletes_existing_backups(self, tmp_path, stub_pg_dump):
        """Retention must not run when there's no new backup to make room for."""
        for d in range(1, 8):
            (tmp_path / f"umacore-2026-07-{d:02d}_000000.sql.gz").write_bytes(b"old")
        stub_pg_dump(FakeProc(stderr=b"boom", returncode=2))
        res = run(create_backup(LOCAL, tmp_path, keep=3))
        assert not res.ok
        assert len(list_backups(tmp_path)) == 7, "pruned despite the backup failing"


class TestResultFormatting:
    @pytest.mark.parametrize("size,expected", [
        (512, "512 B"), (2048, "2.0 KB"), (5 * 1024 * 1024, "5.0 MB"),
    ])
    def test_human_size(self, size, expected):
        assert BackupResult(ok=True, size_bytes=size).size_human == expected
