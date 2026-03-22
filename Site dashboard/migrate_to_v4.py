"""
SIGA v4.0 — Script de migration base de données relationnelle

Ce script fait évoluer SIGA d'un inventaire plat vers un modèle relationnel
capable de gérer les dépendances entre machines, accessoires et consommables.

Opérations effectuées (toutes idempotentes) :
  1. Ajout colonne ai_metadata (JSON) dans equipment
  2. Création table accessories
  3. Création table consumables
  4. Création table links_compatibility (équipements ↔ accessoires, Many-to-Many)
  5. Création table links_consumables  (équipements ↔ consommables,  Many-to-Many)

Usage :
    python migrate_to_v4.py
    python migrate_to_v4.py --db /chemin/vers/siga.duckdb

Ce script peut être relancé sans risque — il n'écrase jamais de données.
"""

import sys
import argparse
from pathlib import Path

try:
    import duckdb
except ImportError:
    print("Erreur : duckdb n'est pas installé. Exécutez : pip install duckdb", file=sys.stderr)
    sys.exit(1)

DEFAULT_DB_PATH = "/files/duckdb/siga_v1.duckdb"


def _run(conn: "duckdb.DuckDBPyConnection", sql: str, label: str = "") -> bool:
    """Exécute un statement DDL et affiche le résultat."""
    try:
        conn.execute(sql)
        print(f"  ✓  {label}" if label else f"  ✓  OK")
        return True
    except Exception as e:
        print(f"  ✗  {label or sql[:80]} → {e}", file=sys.stderr)
        return False


def migrate(db_path: str = DEFAULT_DB_PATH) -> None:
    print(f"\n{'='*60}")
    print("  SIGA v4.0 — Migration Base Relationnelle")
    print(f"{'='*60}")
    print(f"  Base : {db_path}\n")

    db_file = Path(db_path)
    if not db_file.parent.exists():
        db_file.parent.mkdir(parents=True, exist_ok=True)
        print(f"  ℹ  Répertoire créé : {db_file.parent}")

    errors = 0

    with duckdb.connect(db_path, read_only=False) as conn:

        # ── 1. Colonne ai_metadata dans equipment ─────────────────────────────
        print("[1/5] Ajout colonne ai_metadata sur la table equipment…")
        ok = _run(
            conn,
            "ALTER TABLE equipment ADD COLUMN IF NOT EXISTS ai_metadata VARCHAR",
            "equipment.ai_metadata (JSON — capacités sémantiques / domaines d'usage)",
        )
        if not ok:
            errors += 1

        # ── 2. Table accessories ──────────────────────────────────────────────
        print("\n[2/5] Création table accessories…")
        ok = _run(
            conn,
            """
            CREATE TABLE IF NOT EXISTS accessories (
                accessory_id    VARCHAR PRIMARY KEY,
                label           VARCHAR NOT NULL,
                brand           VARCHAR,
                model           VARCHAR,
                category        VARCHAR,
                description     VARCHAR,
                stock_qty       INTEGER DEFAULT 0,
                location_hint   VARCHAR,
                drive_file_id   VARCHAR,
                notes           VARCHAR,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "table accessories (batteries, adaptateurs, chargeurs, lames…)",
        )
        if not ok:
            errors += 1

        # ── 3. Table consumables ──────────────────────────────────────────────
        print("\n[3/5] Création table consumables…")
        ok = _run(
            conn,
            """
            CREATE TABLE IF NOT EXISTS consumables (
                consumable_id   VARCHAR PRIMARY KEY,
                label           VARCHAR NOT NULL,
                brand           VARCHAR,
                reference       VARCHAR,
                category        VARCHAR,
                description     VARCHAR,
                unit            VARCHAR DEFAULT 'pcs',
                stock_qty       DOUBLE DEFAULT 0,
                stock_min_alert DOUBLE DEFAULT 0,
                location_hint   VARCHAR,
                drive_file_id   VARCHAR,
                notes           VARCHAR,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "table consumables (forets, lames, abrasifs, visserie, filtres…)",
        )
        if not ok:
            errors += 1

        # ── 4. Table links_compatibility ──────────────────────────────────────
        print("\n[4/5] Création table links_compatibility (équipements ↔ accessoires)…")
        ok = _run(
            conn,
            """
            CREATE TABLE IF NOT EXISTS links_compatibility (
                link_id         VARCHAR PRIMARY KEY,
                equipment_id    VARCHAR NOT NULL,
                accessory_id    VARCHAR NOT NULL,
                note            VARCHAR,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (equipment_id, accessory_id)
            )
            """,
            "table links_compatibility (M:M — ex: batterie 18V ↔ perforateur + visseuse)",
        )
        if not ok:
            errors += 1

        # ── 5. Table links_consumables ────────────────────────────────────────
        print("\n[5/5] Création table links_consumables (équipements ↔ consommables)…")
        ok = _run(
            conn,
            """
            CREATE TABLE IF NOT EXISTS links_consumables (
                link_id         VARCHAR PRIMARY KEY,
                equipment_id    VARCHAR NOT NULL,
                consumable_id   VARCHAR NOT NULL,
                qty_per_use     DOUBLE DEFAULT 1,
                note            VARCHAR,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (equipment_id, consumable_id)
            )
            """,
            "table links_consumables (M:M — ex: foret SDS-Plus ↔ perforateur)",
        )
        if not ok:
            errors += 1

    # ── Résumé ────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    if errors == 0:
        print("  Migration SIGA v4.0 terminée avec succès.")
        print("  Démarrez maintenant api_server.py et app.py.")
    else:
        print(f"  Migration terminée avec {errors} erreur(s) — vérifiez les messages ci-dessus.")
        sys.exit(1)
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SIGA v4.0 — Migration base de données relationnelle"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Chemin vers le fichier DuckDB (défaut : {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()
    migrate(db_path=args.db)
