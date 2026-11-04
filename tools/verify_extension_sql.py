#!/usr/bin/env python3
"""Verify the maintained extension SQL.

The illustrative prototype is deliberately not an input.  The runtime
generator is maintained directly in sql/pg_jsontailor--0.2.0.sql so that its
catalogue queries and emitted templates are reviewable as shipped.
"""

from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / "sql" / "pg_jsontailor--0.2.0.sql"
    source = target.read_text(encoding="utf-8")
    forbidden = ("orders_dv_prototype.sql", "validation build currently accepts only")
    if any(text in source for text in forbidden):
        raise SystemExit("extension SQL still contains prototype/reference-only generation")
    if "CREATE FUNCTION jsontailor.duality_view_ddl" not in source:
        raise SystemExit("extension SQL does not define the generator")


if __name__ == "__main__":
    main()
