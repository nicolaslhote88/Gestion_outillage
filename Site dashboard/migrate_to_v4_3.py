"""
SIGA v4.3 — Script de migration : Photos orphelines & ingestion_id nullable

Problème résolu :
  L'endpoint PUT /api/equipment/{id}/photos échouait avec une erreur
  "Constraint Error: NOT NULL" sur equipment_media.ingestion_id.
  Ce champ avait été créé NOT NULL par le workflow n8n d'ingestion d'origine,
  mais les endpoints API (hors n8n) ne le renseignent pas.

Opérations effectuées (toutes idempotentes) :
  1. equipment_media.ingestion_id → rendre nullable (ALTER COLUMN … DROP NOT NULL)
  2. Ajout colonne equipment_media.attached_by  — qui a créé la liaison (api / n8n / openclaw)
  3. Ajout colonne equipment_media.attached_at  — horodatage de la liaison
  4. Ajout colonne equipment_media.source_drive_folder_id — dossier Drive source (traçabilité)

Usage :
    python migrate_to_v4_3.py
    python migrate_to_v4_3.py --db /chemin/vers/siga.duckdb
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
        msg = str(e)
        # DuckDB : si la colonne n'est pas NOT NULL, ALTER DROP NOT NULL est sans effet
        if "does not have a NOT NULL constraint" in msg or "already nullable" in msg.lower():
            print(f"  ℹ  {label} — déjà nullable, ignoré")
            return True
        print(f"  ✗  {label or sql[:80]} → {e}", file=sys.stderr)
        return False


def migrate(db_path: str = DEFAULT_DB_PATH) -> None:
    print(f"\n{'='*60}")
    print("  SIGA v4.3 — Migration Photos Orphelines")
    print(f"{'='*60}")
    print(f"  Base : {db_path}\n")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    errors = 0

    with duckdb.connect(db_path, read_only=False) as conn:

        # ── 1. Rendre ingestion_id nullable ──────────────────────────────────
        print("[1] Rendre equipment_media.ingestion_id nullable…")
        # DuckDB ne supporte pas ALTER COLUMN DROP NOT NULL directement sur les
        # colonnes héritées — on recrée la colonne avec ALTER TABLE … ALTER COLUMN
        ok = _run(
            conn,
            "ALTER TABLE equipment_media ALTER COLUMN ingestion_id DROP NOT NULL",
            "equipment_media.ingestion_id → nullable",
        )
        if not ok:
            errors += 1

        # ── 2-4. Colonnes de traçabilité ──────────────────────────────────────
        print("\n[2-4] Colonnes de traçabilité sur equipment_media…")
        for sql, label in [
            (
                "ALTER TABLE equipment_media ADD COLUMN IF NOT EXISTS attached_by VARCHAR DEFAULT 'n8n'",
                "equipment_media.attached_by (api / n8n / openclaw)",
            ),
            (
                "ALTER TABLE equipment_media ADD COLUMN IF NOT EXISTS attached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                "equipment_media.attached_at",
            ),
            (
                "ALTER TABLE equipment_media ADD COLUMN IF NOT EXISTS source_drive_folder_id VARCHAR",
                "equipment_media.source_drive_folder_id (dossier Drive d'origine, traçabilité)",
            ),
        ]:
            if not _run(conn, sql, label):
                errors += 1

        # ── Vérification finale ───────────────────────────────────────────────
        print("\n[check] Vérification structure equipment_media…")
        try:
            cols = conn.execute("PRAGMA table_info('equipment_media')").fetchdf()
            col_names = cols["name"].tolist()
            expected = ["media_id", "equipment_id", "final_drive_file_id",
                        "attached_by", "attached_at", "source_drive_folder_id"]
            missing = [c for c in expected if c not in col_names]
            if missing:
                print(f"  ✗  Colonnes manquantes : {missing}", file=sys.stderr)
                errors += 1
            else:
                print(f"  ✓  {len(col_names)} colonnes présentes")
        except Exception as e:
            print(f"  ✗  Vérification : {e}", file=sys.stderr)
            errors += 1

    print(f"\n{'='*60}")
    if errors == 0:
        print("  Migration SIGA v4.3 terminée avec succès.")
        print("  Relancer l'API pour activer les nouveaux endpoints.")
    else:
        print(f"  Migration terminée avec {errors} erreur(s).")
        sys.exit(1)
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SIGA v4.3 — Migration Photos Orphelines")
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Chemin DuckDB (défaut : {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()
    migrate(db_path=args.db)
