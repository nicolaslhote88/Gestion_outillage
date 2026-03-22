"""
SIGA v4.2 — Script de migration : Complétude média & Drive

Opérations effectuées (toutes idempotentes) :
  1.  equipment.final_drive_folder_id    — dossier Drive de la fiche (depuis workflow n8n)
  2.  equipment.final_drive_folder_path  — chemin lisible du dossier
  3.  equipment_media.final_drive_folder_id — dossier parent du fichier media
  4.  equipment_media.filename           — nom de fichier originel
  5.  equipment_media.mime_type          — type MIME (image/jpeg, etc.)
  6.  equipment_media.is_primary         — indicateur photo principale
  7.  equipment_media.web_view_link      — lien Drive direct (webViewLink)

Ces colonnes permettent :
  - à GET /api/equipment/{id}        de remonter drive_folder_id et des photos complètes
  - à GET /api/equipment/{id}/photos de retourner EquipmentPhotoRef sans erreur 500
  - à PUT /api/equipment/{id}/photos d'enregistrer folder_id, filename, mime_type
  - à POST /api/media/reassign       de conserver les métadonnées lors des déplacements

Usage :
    python migrate_to_v4_2.py
    python migrate_to_v4_2.py --db /chemin/vers/siga.duckdb
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
    print("  SIGA v4.2 — Migration Complétude Média & Drive")
    print(f"{'='*60}")
    print(f"  Base : {db_path}\n")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    errors = 0

    with duckdb.connect(db_path, read_only=False) as conn:

        # ── 1-2. Colonnes Drive sur equipment ─────────────────────────────────
        print("[1-2] Colonnes Drive sur table equipment…")
        for sql, label in [
            ("ALTER TABLE equipment ADD COLUMN IF NOT EXISTS final_drive_folder_id VARCHAR",
             "equipment.final_drive_folder_id"),
            ("ALTER TABLE equipment ADD COLUMN IF NOT EXISTS final_drive_folder_path VARCHAR",
             "equipment.final_drive_folder_path"),
        ]:
            if not _run(conn, sql, label):
                errors += 1

        # ── 3-7. Colonnes sur equipment_media ─────────────────────────────────
        print("\n[3-7] Colonnes média sur table equipment_media…")
        for sql, label in [
            ("ALTER TABLE equipment_media ADD COLUMN IF NOT EXISTS final_drive_folder_id VARCHAR",
             "equipment_media.final_drive_folder_id"),
            ("ALTER TABLE equipment_media ADD COLUMN IF NOT EXISTS filename VARCHAR",
             "equipment_media.filename"),
            ("ALTER TABLE equipment_media ADD COLUMN IF NOT EXISTS mime_type VARCHAR",
             "equipment_media.mime_type"),
            ("ALTER TABLE equipment_media ADD COLUMN IF NOT EXISTS is_primary BOOLEAN DEFAULT FALSE",
             "equipment_media.is_primary"),
            ("ALTER TABLE equipment_media ADD COLUMN IF NOT EXISTS web_view_link VARCHAR",
             "equipment_media.web_view_link"),
        ]:
            if not _run(conn, sql, label):
                errors += 1

    print(f"\n{'='*60}")
    if errors == 0:
        print("  Migration SIGA v4.2 terminée avec succès.")
    else:
        print(f"  Migration terminée avec {errors} erreur(s).")
        sys.exit(1)
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SIGA v4.2 — Migration Complétude Média & Drive")
    parser.add_argument("--db", default=DEFAULT_DB_PATH,
                        help=f"Chemin DuckDB (défaut : {DEFAULT_DB_PATH})")
    args = parser.parse_args()
    migrate(db_path=args.db)
