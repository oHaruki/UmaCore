#!/usr/bin/env python3
"""
Copy club data from one UmaCore PostgreSQL database to another.

Typical use: Neon (private bot) -> VPS (public bot), for specific clubs only.

Examples:
  # Dry run — shows what would be copied, changes nothing
  python scripts/migrate_clubs.py \\
    --source "postgresql://..." \\
    --target "postgresql://umacore:pass@localhost:5432/umacore" \\
    --clubs Horsecore Turfcore \\
    --dry-run

  # Real run with backup + guild update
  python scripts/migrate_clubs.py \\
    --source "postgresql://..." \\
    --target "postgresql://umacore:pass@localhost:5432/umacore" \\
    --clubs Horsecore Turfcore \\
    --guild-id 123456789012345678 \\
    --enable-public
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import asyncpg

# Tables copied in dependency order (parent -> children).
CLUB_SCOPED_TABLES: list[tuple[str, str]] = [
    ("clubs", "club_id"),
    ("members", "club_id"),
    ("quota_history", "club_id"),
    ("bombs", "club_id"),
    ("quota_requirements", "club_id"),
    ("scrape_history", "club_id"),
    ("club_rank_history", "club_id"),
    ("club_role_permissions", "club_id"),
    ("scrape_locks", "club_id"),
    ("audit_logs", "club_id"),
]


def _parse_db_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "database": (parsed.path or "/").lstrip("/"),
        "ssl": "sslmode=disable" not in url
        and (query.get("sslmode", ["prefer"])[0] != "disable"),
    }


async def _connect(url: str) -> asyncpg.Connection:
    cfg = _parse_db_url(url)
    return await asyncpg.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        ssl="require" if cfg["ssl"] else False,
    )


def _backup_with_pg_dump(url: str, backup_dir: Path, label: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = backup_dir / f"umacore_{label}_{stamp}.dump"
    cfg = _parse_db_url(url)

    env = os.environ.copy()
    if cfg["password"]:
        env["PGPASSWORD"] = cfg["password"]
    if cfg["ssl"]:
        env.setdefault("PGSSLMODE", "require")

    cmd = [
        "pg_dump",
        "-h",
        cfg["host"],
        "-p",
        str(cfg["port"]),
        "-U",
        cfg["user"],
        "-d",
        cfg["database"],
        "-Fc",
        "-f",
        str(out),
    ]
    print(f"  Backing up {label} -> {out}")
    subprocess.run(cmd, env=env, check=True)
    return out


async def _count_rows(
    conn: asyncpg.Connection, table: str, column: str, ids: list[UUID]
) -> int:
    if not ids:
        return 0
    return await conn.fetchval(
        f"SELECT COUNT(*) FROM {table} WHERE {column} = ANY($1::uuid[])",
        ids,
    )


async def _fetch_clubs(conn: asyncpg.Connection, names: list[str]) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT * FROM clubs WHERE club_name = ANY($1::text[]) ORDER BY club_name",
        names,
    )


async def _insert_rows(
    conn: asyncpg.Connection, table: str, rows: list[asyncpg.Record]
) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    col_list = ", ".join(cols)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
    async with conn.transaction():
        for row in rows:
            await conn.execute(sql, *[row[c] for c in cols])
    return len(rows)


async def _delete_clubs_on_target(
    conn: asyncpg.Connection, names: list[str], dry_run: bool
) -> list[str]:
    existing = await conn.fetch(
        "SELECT club_id, club_name FROM clubs WHERE club_name = ANY($1::text[])",
        names,
    )
    if not existing:
        return []
    deleted = [r["club_name"] for r in existing]
    if dry_run:
        print(f"  [dry-run] Would delete existing clubs on target: {', '.join(deleted)}")
        return deleted
    await conn.execute(
        "DELETE FROM clubs WHERE club_name = ANY($1::text[])",
        names,
    )
    print(f"  Deleted existing target clubs (cascade): {', '.join(deleted)}")
    return deleted


async def _check_user_link_conflicts(
    source: asyncpg.Connection,
    target: asyncpg.Connection,
    member_ids: list[UUID],
) -> list[dict[str, Any]]:
    if not member_ids:
        return []
    source_links = await source.fetch(
        """
        SELECT ul.discord_user_id, ul.member_id, m.trainer_name, c.club_name
        FROM user_links ul
        JOIN members m ON m.member_id = ul.member_id
        JOIN clubs c ON c.club_id = m.club_id
        WHERE ul.member_id = ANY($1::uuid[])
        """,
        member_ids,
    )
    conflicts = []
    for link in source_links:
        existing = await target.fetchrow(
            """
            SELECT ul.discord_user_id, m.trainer_name AS existing_trainer, c.club_name AS existing_club
            FROM user_links ul
            JOIN members m ON m.member_id = ul.member_id
            JOIN clubs c ON c.club_id = m.club_id
            WHERE ul.discord_user_id = $1
            """,
            link["discord_user_id"],
        )
        if existing and existing["existing_trainer"] != link["trainer_name"]:
            conflicts.append(
                {
                    "discord_user_id": link["discord_user_id"],
                    "from_neon": f"{link['trainer_name']} ({link['club_name']})",
                    "on_vps": f"{existing['existing_trainer']} ({existing['existing_club']})",
                }
            )
    return conflicts


async def migrate(args: argparse.Namespace) -> int:
    missing = [n for n in args.clubs if n]
    if not missing:
        print("ERROR: pass at least one club name with --clubs")
        return 1

    print("=" * 60)
    print("UmaCore club migration")
    print("=" * 60)
    print(f"  Clubs:     {', '.join(missing)}")
    print(f"  Dry run:   {args.dry_run}")
    print(f"  Guild ID:  {args.guild_id or '(unchanged)'}")
    print(f"  Public:    {'enable' if args.enable_public else 'unchanged'}")
    print()

    source = await _connect(args.source)
    target = await _connect(args.target)

    try:
        source_clubs = await _fetch_clubs(source, missing)
        found_names = {r["club_name"] for r in source_clubs}
        not_found = [n for n in missing if n not in found_names]
        if not_found:
            print(f"ERROR: not found on SOURCE: {', '.join(not_found)}")
            return 1

        club_ids = [r["club_id"] for r in source_clubs]
        member_ids = [
            r["member_id"]
            for r in await source.fetch(
                "SELECT member_id FROM members WHERE club_id = ANY($1::uuid[])",
                club_ids,
            )
        ]

        print("SOURCE row counts:")
        for table, col in CLUB_SCOPED_TABLES:
            n = await _count_rows(source, table, col, club_ids)
            print(f"  {table}: {n}")
        user_link_count = await source.fetchval(
            "SELECT COUNT(*) FROM user_links WHERE member_id = ANY($1::uuid[])",
            member_ids,
        )
        print(f"  user_links: {user_link_count}")
        print()

        conflicts = await _check_user_link_conflicts(source, target, member_ids)
        if conflicts:
            print("WARNING: user_links conflicts (same Discord user, different member on VPS):")
            for c in conflicts:
                print(
                    f"  Discord {c['discord_user_id']}: "
                    f"Neon={c['from_neon']} vs VPS={c['on_vps']}"
                )
            if not args.overwrite_user_links and not args.dry_run:
                print()
                print("  Re-run with --overwrite-user-links to replace VPS links with Neon data,")
                print("  or fix manually after migration.")
                return 1
            print()

        if args.dry_run:
            await _delete_clubs_on_target(target, missing, dry_run=True)
            print("[dry-run] No changes made.")
            return 0

        backup_dir = Path(args.backup_dir)
        print("STEP 1 — Backups")
        try:
            _backup_with_pg_dump(args.target, backup_dir, "vps_before")
            if args.backup_source:
                _backup_with_pg_dump(args.source, backup_dir, "neon_before")
        except FileNotFoundError:
            print()
            print("ERROR: pg_dump not found. Install PostgreSQL client tools:")
            print("  Ubuntu: sudo apt install postgresql-client")
            print("  Windows: install PostgreSQL from postgresql.org (includes pg_dump)")
            return 1
        except subprocess.CalledProcessError as e:
            print(f"ERROR: pg_dump failed (exit {e.returncode})")
            return 1
        print()

        print("STEP 2 — Remove existing target clubs (if any)")
        await _delete_clubs_on_target(target, missing, dry_run=False)
        print()

        print("STEP 3 — Copy club data")
        for table, col in CLUB_SCOPED_TABLES:
            rows = await source.fetch(
                f"SELECT * FROM {table} WHERE {col} = ANY($1::uuid[]) ORDER BY 1",
                club_ids,
            )
            n = await _insert_rows(target, table, rows)
            print(f"  {table}: inserted {n}")

        link_rows = await source.fetch(
            "SELECT * FROM user_links WHERE member_id = ANY($1::uuid[])",
            member_ids,
        )
        if link_rows:
            if args.overwrite_user_links:
                for row in link_rows:
                    cols = list(row.keys())
                    col_list = ", ".join(cols)
                    updates = ", ".join(
                        f"{c} = EXCLUDED.{c}" for c in cols if c != "discord_user_id"
                    )
                    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
                    sql = (
                        f"INSERT INTO user_links ({col_list}) VALUES ({placeholders}) "
                        f"ON CONFLICT (discord_user_id) DO UPDATE SET {updates}, updated_at = NOW()"
                    )
                    await target.execute(sql, *[row[c] for c in cols])
            else:
                await _insert_rows(target, "user_links", link_rows)
        print(f"  user_links: inserted {len(link_rows)}")
        print()

        print("STEP 4 — Post-migration updates")
        if args.guild_id:
            await target.execute(
                """
                UPDATE clubs SET guild_id = $1, updated_at = NOW()
                WHERE club_name = ANY($2::text[])
                """,
                args.guild_id,
                missing,
            )
            print(f"  guild_id set to {args.guild_id}")
        if args.enable_public:
            await target.execute(
                """
                UPDATE clubs SET public_enabled = TRUE, updated_at = NOW()
                WHERE club_name = ANY($1::text[])
                """,
                missing,
            )
            print("  public_enabled = TRUE")

        print()
        print("DONE. Verify with:")
        print("  sudo systemctl restart umacore-bot")
        print("  /list_clubs  and  /force_check club:Horsecore")
        return 0

    finally:
        await source.close()
        await target.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate UmaCore clubs between databases")
    parser.add_argument(
        "--source",
        required=True,
        help="SOURCE database URL (e.g. Neon private bot)",
    )
    parser.add_argument(
        "--target",
        required=True,
        help="TARGET database URL (e.g. VPS local postgres)",
    )
    parser.add_argument(
        "--clubs",
        nargs="+",
        required=True,
        help="Club names to migrate (e.g. Horsecore Turfcore)",
    )
    parser.add_argument(
        "--guild-id",
        type=int,
        default=None,
        help="Discord server ID to set on migrated clubs",
    )
    parser.add_argument(
        "--enable-public",
        action="store_true",
        help="Set public_enabled=TRUE on migrated clubs",
    )
    parser.add_argument(
        "--backup-dir",
        default="./migration_backups",
        help="Where to store pg_dump backups (default: ./migration_backups)",
    )
    parser.add_argument(
        "--backup-source",
        action="store_true",
        help="Also backup SOURCE (Neon) before migration",
    )
    parser.add_argument(
        "--overwrite-user-links",
        action="store_true",
        help="Replace conflicting user_links on target with Neon data",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show counts only, change nothing",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(migrate(args)))


if __name__ == "__main__":
    main()
