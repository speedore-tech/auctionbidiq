"""Batch-ingest result sheets (PDF or comps CSV) straight into the DB — for backfilling the
historical dataset or for n8n's "Execute Command" node.

    python scripts/ingest_folder.py path/to/results/            # shared pool
    python scripts/ingest_folder.py file.pdf --tenant-id 3      # private to tenant 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bidiq.db import SessionLocal, init_db  # noqa: E402
from bidiq.ingest import ingest_comps_csv, ingest_results  # noqa: E402
from bidiq.ingest.results_pdf import parse_results_pdf  # noqa: E402
from bidiq.ingest.sheets import read_csv_rows  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--tenant-id", type=int, default=None, help="omit for the shared comp pool")
    args = ap.parse_args()
    init_db()
    db = SessionLocal()
    files: list[Path] = []
    for p in map(Path, args.paths):
        files += sorted([*p.glob("*.pdf"), *p.glob("*.csv")]) if p.is_dir() else [p]
    failures = 0
    for f in files:
        try:
            if f.suffix.lower() == ".csv":
                r = ingest_comps_csv(db, read_csv_rows(f.read_bytes()), args.tenant_id, f.name)
                print(f"{f.name}: {r['rows']} comps across {r['sales']} sales")
            else:
                sheet = parse_results_pdf(f)
                r = ingest_results(db, sheet, args.tenant_id, f.name)
                warn = f", {len(r['skipped_lines'])} unparsed lines" if r["skipped_lines"] else ""
                print(f"{f.name}: sale {r['sale_id']} {r['location']} — {r['rows']} lots (replaced {r['replaced']}){warn}")
        except Exception as e:  # keep going; report at the end
            db.rollback()
            failures += 1
            print(f"{f.name}: FAILED — {e}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
