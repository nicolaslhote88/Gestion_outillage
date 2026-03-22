"""
SIGA v4.1 — Script de migration : Gouvernance, Audit & Migration Support

Opérations effectuées (toutes idempotentes) :
  1.  equipment.archived                    — soft-delete flag
  2.  equipment.migration_status            — NOT_REVIEWED / REVIEWED / MIGRATED / ARCHIVED
  3.  equipment.legacy_source_id            — traçabilité vers fiche d'origine
  4.  equipment.migrated_at / migrated_by   — horodatage de migration
  5.  equipment.classification_confidence   — score de confiance de classification
  6.  accessories.archived                  — soft-delete
  7.  accessories.ai_metadata               — JSON capacités sémantiques
  8.  accessories.migration_status          — gouvernance
  9.  accessories.legacy_source_id
  10. consumables.archived
  11. consumables.ai_metadata
  12. consumables.migration_status
  13. consumables.legacy_source_id
  14. Table legacy_mappings                 — mapping legacy_id → canonical IDs
  15. Table migration_logs                  — journal d'audit complet

Usage :
    python migrate_to_v4_1.py
    python migrate_to_v4_1.py --db /chemin/vers/siga.duckdb
"""

import sys
import argparse
from pathlib import Path

try:
    import duckdb
except ImportError:
    print("Erreur : duckdb non installé. pip install duckdb", file=sys.stderr)
    sys.exit(1)

DEFAULT_DB_PATH = "/files/duckdb/siga_v1.duckdb"


def _run(conn, sql: str, label: str = "") -> bool:
    try:
        conn.execute(sql)
        print(f"  ✓  {label}" if label else "  ✓  OK")
        return True
    except Exception as e:
        print(f"  ✗  {label or sql[:80]} → {e}", file=sys.stderr)
        return False


def migrate(db_path: str = DEFAULT_DB_PATH) -> None:
    print(f"\n{'='*60}")
    print("  SIGA v4.1 — Migration Gouvernance & Audit")
    print(f"{'='*60}")
    print(f"  Base : {db_path}\n")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    errors = 0

    with duckdb.connect(db_path, read_only=False) as conn:

        # ── 1-5. Colonnes de gouvernance sur equipment ────────────────────────
        print("[1-5] Gouvernance sur table equipment…")
        for sql, label in [
            ("ALTER TABLE equipment ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE",
             "equipment.archived"),
            ("ALTER TABLE equipment ADD COLUMN IF NOT EXISTS migration_status VARCHAR DEFAULT 'NOT_REVIEWED'",
             "equipment.migration_status"),
            ("ALTER TABLE equipment ADD COLUMN IF NOT EXISTS legacy_source_id VARCHAR",
             "equipment.legacy_source_id"),
            ("ALTER TABLE equipment ADD COLUMN IF NOT EXISTS migrated_at TIMESTAMP",
             "equipment.migrated_at"),
            ("ALTER TABLE equipment ADD COLUMN IF NOT EXISTS migrated_by VARCHAR",
             "equipment.migrated_by"),
            ("ALTER TABLE equipment ADD COLUMN IF NOT EXISTS classification_confidence DOUBLE",
             "equipment.classification_confidence"),
        ]:
            if not _run(conn, sql, label):
                errors += 1

        # ── 6-9. Colonnes de gouvernance sur accessories ──────────────────────
        print("\n[6-9] Gouvernance sur table accessories…")
        for sql, label in [
            ("ALTER TABLE accessories ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE",
             "accessories.archived"),
            ("ALTER TABLE accessories ADD COLUMN IF NOT EXISTS ai_metadata VARCHAR",
             "accessories.ai_metadata"),
            ("ALTER TABLE accessories ADD COLUMN IF NOT EXISTS migration_status VARCHAR DEFAULT 'NOT_REVIEWED'",
             "accessories.migration_status"),
            ("ALTER TABLE accessories ADD COLUMN IF NOT EXISTS legacy_source_id VARCHAR",
             "accessories.legacy_source_id"),
        ]:
            if not _run(conn, sql, label):
                errors += 1

        # ── 10-13. Colonnes de gouvernance sur consumables ────────────────────
        print("\n[10-13] Gouvernance sur table consumables…")
        for sql, label in [
            ("ALTER TABLE consumables ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE",
             "consumables.archived"),
            ("ALTER TABLE consumables ADD COLUMN IF NOT EXISTS ai_metadata VARCHAR",
             "consumables.ai_metadata"),
            ("ALTER TABLE consumables ADD COLUMN IF NOT EXISTS migration_status VARCHAR DEFAULT 'NOT_REVIEWED'",
             "consumables.migration_status"),
            ("ALTER TABLE consumables ADD COLUMN IF NOT EXISTS legacy_source_id VARCHAR",
             "consumables.legacy_source_id"),
        ]:
            if not _run(conn, sql, label):
                errors += 1

        # ── 14. Table legacy_mappings ─────────────────────────────────────────
        print("\n[14] Création table legacy_mappings…")
        if not _run(conn, """
            CREATE TABLE IF NOT EXISTS legacy_mappings (
                mapping_id              VARCHAR PRIMARY KEY,
                legacy_equipment_id     VARCHAR NOT NULL,
                canonical_equipment_id  VARCHAR,
                derived_accessory_ids   VARCHAR,   -- JSON array d'accessory_id
                derived_consumable_ids  VARCHAR,   -- JSON array de consumable_id
                notes                   VARCHAR,
                created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (legacy_equipment_id)
            )
        """, "table legacy_mappings (traçabilité legacy → canonical)"):
            errors += 1

        # ── 15. Table migration_logs ──────────────────────────────────────────
        print("\n[15] Création table migration_logs…")
        if not _run(conn, """
            CREATE TABLE IF NOT EXISTS migration_logs (
                log_id              VARCHAR PRIMARY KEY,
                operation           VARCHAR NOT NULL,
                operator            VARCHAR NOT NULL DEFAULT 'openclaw',
                source_entity_type  VARCHAR,
                source_entity_id    VARCHAR,
                target_entities     VARCHAR,   -- JSON
                details             VARCHAR,   -- JSON
                dry_run             BOOLEAN DEFAULT FALSE,
                status              VARCHAR DEFAULT 'COMPLETED',
                error_message       VARCHAR,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "table migration_logs (journal d'audit complet)"):
            errors += 1

    print(f"\n{'='*60}")
    if errors == 0:
        print("  Migration SIGA v4.1 terminée avec succès.")
    else:
        print(f"  Migration terminée avec {errors} erreur(s).")
        sys.exit(1)
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SIGA v4.1 — Migration Gouvernance")
    parser.add_argument("--db", default=DEFAULT_DB_PATH,
                        help=f"Chemin DuckDB (défaut : {DEFAULT_DB_PATH})")
    args = parser.parse_args()
    migrate(db_path=args.db)
