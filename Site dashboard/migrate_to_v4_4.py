"""
SIGA v4.4 — Script de migration : Multi-photos accessoires & consommables

Problème résolu :
  Les accessoires et consommables n'avaient qu'un seul champ drive_file_id
  (photo unique). L'API ne savait pas utiliser les tables accessory_media et
  consumable_media déjà créées par Streamlit — elle n'en renvoyait qu'une photo.

Opérations effectuées (toutes idempotentes) :
  1. Création table accessory_media  si inexistante
  2. Création table consumable_media  si inexistante
  3. Backfill : drive_file_id existants → accessory_media / consumable_media
     (uniquement pour les entités qui n'ont pas encore de ligne dans la table media)

Usage :
    python migrate_to_v4_4.py
    python migrate_to_v4_4.py --db /chemin/vers/siga.duckdb
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
    print("  SIGA v4.4 — Migration Multi-photos Accessoires & Consommables")
    print(f"{'='*60}")
    print(f"  Base : {db_path}\n")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    errors = 0

    with duckdb.connect(db_path, read_only=False) as conn:

        # ── 1. Table accessory_media ──────────────────────────────────────────
        print("[1] Création table accessory_media…")
        ok = _run(
            conn,
            """
            CREATE TABLE IF NOT EXISTS accessory_media (
                media_id              VARCHAR PRIMARY KEY,
                accessory_id          VARCHAR NOT NULL,
                final_drive_file_id   VARCHAR,
                final_drive_folder_id VARCHAR,
                filename              VARCHAR,
                mime_type             VARCHAR,
                image_role            VARCHAR DEFAULT 'overview',
                image_index           INTEGER DEFAULT 0,
                is_primary            BOOLEAN DEFAULT FALSE,
                web_view_link         VARCHAR,
                attached_by           VARCHAR DEFAULT 'api',
                attached_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source_drive_folder_id VARCHAR,
                created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "table accessory_media",
        )
        if not ok:
            errors += 1

        # ── 2. Table consumable_media ─────────────────────────────────────────
        print("\n[2] Création table consumable_media…")
        ok = _run(
            conn,
            """
            CREATE TABLE IF NOT EXISTS consumable_media (
                media_id              VARCHAR PRIMARY KEY,
                consumable_id         VARCHAR NOT NULL,
                final_drive_file_id   VARCHAR,
                final_drive_folder_id VARCHAR,
                filename              VARCHAR,
                mime_type             VARCHAR,
                image_role            VARCHAR DEFAULT 'overview',
                image_index           INTEGER DEFAULT 0,
                is_primary            BOOLEAN DEFAULT FALSE,
                web_view_link         VARCHAR,
                attached_by           VARCHAR DEFAULT 'api',
                attached_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source_drive_folder_id VARCHAR,
                created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "table consumable_media",
        )
        if not ok:
            errors += 1

        # ── 3. Backfill accessory_media depuis drive_file_id ─────────────────
        print("\n[3] Backfill accessory_media depuis accessories.drive_file_id…")
        try:
            result = conn.execute("""
                INSERT INTO accessory_media
                    (media_id, accessory_id, final_drive_file_id, image_role, image_index, is_primary, attached_by)
                SELECT
                    gen_random_uuid()::VARCHAR,
                    accessory_id,
                    drive_file_id,
                    'overview',
                    0,
                    TRUE,
                    'migration_v4_4'
                FROM accessories
                WHERE drive_file_id IS NOT NULL
                  AND drive_file_id NOT IN ('', 'None', 'nan')
                  AND accessory_id NOT IN (SELECT DISTINCT accessory_id FROM accessory_media)
            """)
            count = conn.execute(
                "SELECT COUNT(*) FROM accessory_media WHERE attached_by = 'migration_v4_4'"
            ).fetchone()[0]
            print(f"  ✓  {count} photos migrées depuis accessories.drive_file_id")
        except Exception as e:
            print(f"  ✗  Backfill accessory_media → {e}", file=sys.stderr)
            errors += 1

        # ── 4. Backfill consumable_media depuis drive_file_id ────────────────
        print("\n[4] Backfill consumable_media depuis consumables.drive_file_id…")
        try:
            conn.execute("""
                INSERT INTO consumable_media
                    (media_id, consumable_id, final_drive_file_id, image_role, image_index, is_primary, attached_by)
                SELECT
                    gen_random_uuid()::VARCHAR,
                    consumable_id,
                    drive_file_id,
                    'overview',
                    0,
                    TRUE,
                    'migration_v4_4'
                FROM consumables
                WHERE drive_file_id IS NOT NULL
                  AND drive_file_id NOT IN ('', 'None', 'nan')
                  AND consumable_id NOT IN (SELECT DISTINCT consumable_id FROM consumable_media)
            """)
            count = conn.execute(
                "SELECT COUNT(*) FROM consumable_media WHERE attached_by = 'migration_v4_4'"
            ).fetchone()[0]
            print(f"  ✓  {count} photos migrées depuis consumables.drive_file_id")
        except Exception as e:
            print(f"  ✗  Backfill consumable_media → {e}", file=sys.stderr)
            errors += 1

        # ── Vérification ──────────────────────────────────────────────────────
        print("\n[check] Comptage final…")
        try:
            acc_n = conn.execute("SELECT COUNT(*) FROM accessory_media").fetchone()[0]
            con_n = conn.execute("SELECT COUNT(*) FROM consumable_media").fetchone()[0]
            print(f"  ✓  accessory_media  : {acc_n} ligne(s)")
            print(f"  ✓  consumable_media : {con_n} ligne(s)")
        except Exception as e:
            print(f"  ✗  Vérification : {e}", file=sys.stderr)
            errors += 1

    print(f"\n{'='*60}")
    if errors == 0:
        print("  Migration SIGA v4.4 terminée avec succès.")
        print("  Relancer l'API pour activer le support multi-photos.")
    else:
        print(f"  Migration terminée avec {errors} erreur(s).")
        sys.exit(1)
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SIGA v4.4 — Migration Multi-photos Accessoires & Consommables"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Chemin DuckDB (défaut : {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()
    migrate(db_path=args.db)
