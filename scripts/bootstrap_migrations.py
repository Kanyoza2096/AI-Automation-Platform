#!/usr/bin/env python3
"""Apply migrations 001→N (structure gate + optional real DB apply)."""
from __future__ import annotations
import argparse, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "migrations"

def structure_check():
    files = sorted(MIG.glob("*.sql"), key=lambda p: p.name)
    other = [p for p in MIG.iterdir() if p.is_file() and p.suffix not in (".sql", ".md")]
    errors = []
    if other:
        errors.append(f"Non-SQL: {[p.name for p in other]}")
    nums = []
    for p in files:
        m = re.match(r"^(\d+)_", p.name)
        if not m:
            errors.append(f"Unnumbered: {p.name}")
        else:
            nums.append(int(m.group(1)))
    if nums and nums != sorted(nums):
        errors.append(f"Not sorted: {nums}")
    if nums:
        missing = [n for n in range(nums[0], nums[-1]+1) if n not in nums]
        if missing:
            errors.append(f"Missing: {missing}")
    if errors:
        for e in errors:
            print("STRUCTURE FAIL:", e)
        raise SystemExit(1)
    print(f"STRUCTURE OK: {len(files)} SQL files")
    return files

def apply(files, dry_run=False):
    url = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
    if not url:
        print("SKIP APPLY: set DATABASE_URL for real from-zero bootstrap")
        return
    if dry_run:
        for p in files:
            print("DRY-RUN:", p.name)
        return
    try:
        import psycopg
        conn = psycopg.connect(url)
    except Exception:
        try:
            import psycopg2
            conn = psycopg2.connect(url)
        except Exception as exc:
            print("FAIL: need psycopg:", exc)
            raise SystemExit(2)
    try:
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations (filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        for p in files:
            cur.execute("SELECT 1 FROM schema_migrations WHERE filename=%s", (p.name,))
            if cur.fetchone():
                print("SKIP:", p.name); continue
            print("APPLY:", p.name)
            cur.execute(p.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO schema_migrations(filename) VALUES (%s)", (p.name,))
        conn.commit()
        print("APPLY OK")
    except Exception as exc:
        conn.rollback()
        print("APPLY FAIL:", exc)
        raise SystemExit(3)
    finally:
        conn.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--structure-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    files = structure_check()
    if not args.structure_only:
        apply(files, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
