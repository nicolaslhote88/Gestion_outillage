"""
SIGA — Serveur MCP (Model Context Protocol)
Expose des outils pour qu'un agent IA (Openclaw) puisse :
  - Rechercher des équipements dans le catalogue DuckDB
  - Consulter la disponibilité et les sorties en cours
  - Enregistrer des sorties et retours (individuels, groupés, par kit)
  - Créer, composer et supprimer des kits (caisses à outils / paniers chantier)
  - Préparer un chantier via les kits / paniers
  - Piloter l'affichage de l'écran kiosque atelier (Raspberry Pi 5)

Transport : SSE (Server-Sent Events) — connexion via HTTP/web.
Concurrence DuckDB : utilise le même pattern retry/backoff que app.py
  (5 tentatives, backoff exponentiel à 2 s) pour cohabiter avec n8n.

Lancement :
    python mcp_server.py
ou via la CLI officielle :
    mcp dev mcp_server.py
"""

import json
import time
import uuid
from datetime import datetime
from typing import Optional

import duckdb
import pandas as pd
from mcp.server.fastmcp import FastMCP

# ─── Configuration ────────────────────────────────────────────
DB_PATH = "/files/duckdb/siga_v1.duckdb"

VALID_MOVEMENT_TYPES = {"LOAN", "RENTAL", "MAINTENANCE"}

# ─── Utilitaires DuckDB ───────────────────────────────────────

def _run_query(sql: str, params=None) -> pd.DataFrame:
    """Lecture DuckDB avec connexion ouvre/ferme (pas de lock persistant)."""
    try:
        with duckdb.connect(DB_PATH, read_only=True) as conn:
            if params:
                return conn.execute(sql, params).df()
            return conn.execute(sql).df()
    except Exception as e:
        raise RuntimeError(f"Erreur lecture DuckDB : {e}") from e


def _run_write(sql: str, params=None, _retries: int = 5) -> None:
    """Écriture DuckDB avec retry/backoff exponentiel (gestion lock n8n)."""
    delay = 2
    last_err = None
    for attempt in range(_retries):
        try:
            with duckdb.connect(DB_PATH, read_only=False) as conn:
                if params:
                    conn.execute(sql, params)
                else:
                    conn.execute(sql)
            return
        except duckdb.IOException as e:
            last_err = e
            if attempt < _retries - 1:
                time.sleep(delay)
                delay *= 2
        except Exception as e:
            raise RuntimeError(f"Erreur écriture DuckDB : {e}") from e
    raise RuntimeError(
        f"Base de données inaccessible après {_retries} tentatives "
        f"(verrou n8n ?) : {last_err}"
    )


def _run_write_many(statements: list[tuple], _retries: int = 5) -> None:
    """Exécute plusieurs (sql, params) dans une seule connexion (atomique)."""
    delay = 2
    last_err = None
    for attempt in range(_retries):
        try:
            with duckdb.connect(DB_PATH, read_only=False) as conn:
                for sql, params in statements:
                    conn.execute(sql, params)
            return
        except duckdb.IOException as e:
            last_err = e
            if attempt < _retries - 1:
                time.sleep(delay)
                delay *= 2
        except Exception as e:
            raise RuntimeError(f"Erreur écriture DuckDB : {e}") from e
    raise RuntimeError(
        f"Base de données inaccessible après {_retries} tentatives "
        f"(verrou n8n ?) : {last_err}"
    )


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def _df_to_json(df: pd.DataFrame) -> str:
    for col in df.select_dtypes(include=["datetime64[ns]", "datetime64[us]"]).columns:
        df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M")
    return df.to_json(orient="records", force_ascii=False)


# ─── Serveur MCP ──────────────────────────────────────────────

mcp = FastMCP(
    name="SIGA",
    instructions=(
        "Tu interagis avec le système SIGA de gestion d'outillage industriel. "
        "Tu peux : rechercher des équipements, consulter leur disponibilité, "
        "enregistrer des sorties et retours (individuels ou groupés), "
        "préparer des chantiers via les kits/paniers, "
        "piloter l'affichage de l'écran kiosque atelier, "
        "et gérer les réservations de matériel.\n\n"
        "Types de mouvement valides : LOAN (prêt), RENTAL (location), MAINTENANCE.\n"
        "Les dates doivent être au format YYYY-MM-DD ou YYYY-MM-DDTHH:MM.\n\n"
        "Workflow réservation : quand un utilisateur demande de réserver un outil :\n"
        "1. Identifier l'outil via search_equipment.\n"
        "2. Normaliser les dates (ex: 'mardi prochain' -> date ISO).\n"
        "3. Vérifier les conflits via check_reservation_conflicts.\n"
        "4. Si pas de conflit : appeler create_reservation et confirmer.\n"
        "5. Si conflit : expliquer précisément le problème à l'utilisateur."
    ),
)


# ════════════════════════════════════════════════════════════
# OUTIL 1 — Recherche d'équipements
# ════════════════════════════════════════════════════════════

@mcp.tool()
def search_equipment(query: str) -> str:
    """
    Recherche des équipements dans le catalogue SIGA.

    Effectue une recherche insensible à la casse sur la marque (brand),
    le modèle (model) et le nom de l'équipement (label).

    Args:
        query: Texte de recherche (marque, modèle ou nom de l'outil).

    Returns:
        JSON contenant la liste des équipements correspondants avec leurs
        caractéristiques principales (id, nom, marque, modèle, état, emplacement).
    """
    like_param = f"%{query}%"
    df = _run_query(
        """
        SELECT
            e.equipment_id,
            e.label,
            e.brand,
            e.model,
            e.serial_number,
            e.subtype,
            e.condition_label,
            e.location_hint,
            e.received_at
        FROM equipment e
        WHERE
            LOWER(e.brand) LIKE LOWER(?)
            OR LOWER(e.model) LIKE LOWER(?)
            OR LOWER(e.label) LIKE LOWER(?)
        ORDER BY e.received_at DESC
        LIMIT 20
        """,
        [like_param, like_param, like_param],
    )

    if df.empty:
        return f'{{"results": [], "message": "Aucun équipement trouvé pour : {query}"}}'

    return _df_to_json(df)


# ════════════════════════════════════════════════════════════
# OUTIL 2 — Disponibilité d'un équipement
# ════════════════════════════════════════════════════════════

@mcp.tool()
def get_equipment_status(equipment_id: str) -> str:
    """
    Vérifie si un équipement est disponible ou actuellement sorti.

    Args:
        equipment_id: Identifiant unique de l'équipement (champ equipment_id).

    Returns:
        JSON indiquant la disponibilité, et si sorti : l'emprunteur,
        le type de mouvement et les dates de sortie / retour prévue.
    """
    eq_df = _run_query(
        "SELECT equipment_id, label FROM equipment WHERE equipment_id = ? LIMIT 1",
        [equipment_id],
    )
    if eq_df.empty:
        return json.dumps({"error": "equipment_not_found", "equipment_id": equipment_id})

    label = str(eq_df.iloc[0]["label"] or "")

    mv_df = _run_query(
        """
        SELECT movement_type, borrower_name, borrower_contact,
               out_date, expected_return_date, batch_id, kit_id
        FROM equipment_movements
        WHERE equipment_id = ? AND actual_return_date IS NULL
        ORDER BY out_date DESC LIMIT 1
        """,
        [equipment_id],
    )

    if mv_df.empty:
        return json.dumps({"equipment_id": equipment_id, "label": label, "available": True})

    row = mv_df.iloc[0].to_dict()
    row["equipment_id"] = equipment_id
    row["label"] = label
    row["available"] = False
    # Stringify dates
    for k in ("out_date", "expected_return_date"):
        if pd.notna(row.get(k)):
            row[k] = str(row[k])[:16]
        else:
            row[k] = None

    return json.dumps(row, ensure_ascii=False, default=str)


# ════════════════════════════════════════════════════════════
# OUTIL 3 — Liste des sorties actives
# ════════════════════════════════════════════════════════════

@mcp.tool()
def list_active_movements() -> str:
    """
    Liste tous les équipements actuellement sortis (non retournés).

    Indique pour chaque mouvement : l'équipement, l'emprunteur, le type,
    les dates de sortie et de retour prévue, et si le retour est en retard.

    Returns:
        JSON avec la liste des mouvements actifs et un résumé (total, retards).
    """
    df = _run_query(
        """
        SELECT
            m.movement_id,
            m.equipment_id,
            COALESCE(NULLIF(e.label, ''), e.brand || ' ' || e.model, m.equipment_id) AS label,
            m.borrower_name,
            m.movement_type,
            m.out_date,
            m.expected_return_date,
            m.batch_id,
            m.kit_id,
            k.name AS kit_name
        FROM equipment_movements m
        JOIN equipment e ON e.equipment_id = m.equipment_id
        LEFT JOIN kits k ON k.kit_id = m.kit_id
        WHERE m.actual_return_date IS NULL
        ORDER BY m.out_date DESC
        """
    )

    if df.empty:
        return json.dumps({"total": 0, "late": 0, "movements": []})

    now = datetime.now()
    movements = []
    late_count = 0
    for _, row in df.iterrows():
        exp = row.get("expected_return_date")
        is_late = bool(pd.notna(exp) and pd.Timestamp(exp).to_pydatetime() < now)
        if is_late:
            late_count += 1
        movements.append({
            "movement_id": str(row["movement_id"]),
            "equipment_id": str(row["equipment_id"]),
            "label": str(row["label"] or ""),
            "borrower_name": str(row["borrower_name"] or ""),
            "movement_type": str(row["movement_type"] or ""),
            "out_date": str(row["out_date"])[:16] if pd.notna(row.get("out_date")) else None,
            "expected_return_date": str(row["expected_return_date"])[:16] if pd.notna(row.get("expected_return_date")) else None,
            "is_late": is_late,
            "batch_id": str(row["batch_id"]) if pd.notna(row.get("batch_id")) else None,
            "kit_id": str(row["kit_id"]) if pd.notna(row.get("kit_id")) else None,
            "kit_name": str(row["kit_name"]) if pd.notna(row.get("kit_name")) else None,
        })

    return json.dumps({"total": len(movements), "late": late_count, "movements": movements}, ensure_ascii=False)


# ════════════════════════════════════════════════════════════
# OUTIL 4 — Sortie d'équipement (individuelle ou groupée)
# ════════════════════════════════════════════════════════════

@mcp.tool()
def checkout_equipment(
    equipment_ids: list[str],
    borrower_name: str,
    movement_type: str = "LOAN",
    borrower_contact: Optional[str] = None,
    expected_return_date: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """
    Enregistre la sortie d'un ou plusieurs équipements.

    Un seul equipment_id → mouvement individuel.
    Plusieurs equipment_ids → sortie groupée avec un batch_id commun.

    Args:
        equipment_ids: Liste d'identifiants d'équipements à sortir.
        borrower_name: Nom de l'emprunteur / entreprise (obligatoire).
        movement_type: Type de mouvement : LOAN (prêt), RENTAL (location),
                       MAINTENANCE. Par défaut : LOAN.
        borrower_contact: Contact de l'emprunteur (téléphone, email) — optionnel.
        expected_return_date: Date de retour prévue au format YYYY-MM-DD — optionnel.
        notes: Notes libres sur le mouvement — optionnel.

    Returns:
        JSON avec batch_id, liste des movement_ids créés et un message de confirmation.
    """
    if not equipment_ids:
        return json.dumps({"ok": False, "error": "equipment_ids ne peut pas être vide."})

    mv_type = movement_type.upper()
    if mv_type not in VALID_MOVEMENT_TYPES:
        return json.dumps({"ok": False, "error": f"movement_type invalide : {mv_type}. Valeurs : {list(VALID_MOVEMENT_TYPES)}"})

    if not borrower_name or not borrower_name.strip():
        return json.dumps({"ok": False, "error": "borrower_name est obligatoire."})

    exp_dt = _parse_date(expected_return_date)
    if expected_return_date and exp_dt is None:
        return json.dumps({"ok": False, "error": f"expected_return_date invalide : '{expected_return_date}'. Format : YYYY-MM-DD"})

    # Vérifie existence des équipements
    ids_ph = ", ".join(["?"] * len(equipment_ids))
    exists_df = _run_query(
        f"SELECT equipment_id FROM equipment WHERE equipment_id IN ({ids_ph})",
        equipment_ids,
    )
    found_ids = set(exists_df["equipment_id"].tolist())
    missing = [eid for eid in equipment_ids if eid not in found_ids]
    if missing:
        return json.dumps({"ok": False, "error": f"Équipements introuvables : {missing}"})

    batch_id = str(uuid.uuid4()) if len(equipment_ids) > 1 else None
    movement_ids = []
    statements = []

    for eid in equipment_ids:
        mid = str(uuid.uuid4())
        movement_ids.append(mid)
        statements.append((
            """
            INSERT INTO equipment_movements
                (movement_id, equipment_id, movement_type,
                 borrower_name, borrower_contact,
                 out_date, expected_return_date, notes, batch_id)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
            """,
            [mid, eid, mv_type,
             borrower_name.strip(),
             borrower_contact.strip() if borrower_contact else None,
             exp_dt, notes, batch_id],
        ))

    _run_write_many(statements)

    return json.dumps({
        "ok": True,
        "batch_id": batch_id or movement_ids[0],
        "movement_ids": movement_ids,
        "count": len(equipment_ids),
        "message": (
            f"{len(equipment_ids)} équipement(s) sorti(s) pour '{borrower_name.strip()}' "
            f"(type : {mv_type}"
            + (f", retour prévu : {expected_return_date}" if exp_dt else "")
            + ")."
        ),
    }, ensure_ascii=False)


# ════════════════════════════════════════════════════════════
# OUTIL 5 — Entrée d'équipement (retour)
# ════════════════════════════════════════════════════════════

@mcp.tool()
def checkin_equipment(
    movement_ids: Optional[list[str]] = None,
    batch_id: Optional[str] = None,
) -> str:
    """
    Enregistre le retour d'un ou plusieurs équipements.

    Fournir movement_ids pour cibler des mouvements précis.
    Fournir batch_id pour solder tous les équipements d'un lot groupé.
    Les deux paramètres peuvent être combinés.

    Args:
        movement_ids: Liste de movement_id à solder — optionnel.
        batch_id: Identifiant de lot pour retour groupé — optionnel.

    Returns:
        JSON avec le nombre d'équipements effectivement retournés.
    """
    if not movement_ids and not batch_id:
        return json.dumps({"ok": False, "error": "Fournir movement_ids ou batch_id."})

    statements = []

    if movement_ids:
        for mid in movement_ids:
            statements.append((
                "UPDATE equipment_movements SET actual_return_date = CURRENT_TIMESTAMP WHERE movement_id = ? AND actual_return_date IS NULL",
                [mid],
            ))

    if batch_id:
        statements.append((
            "UPDATE equipment_movements SET actual_return_date = CURRENT_TIMESTAMP WHERE batch_id = ? AND actual_return_date IS NULL",
            [batch_id],
        ))

    _run_write_many(statements)

    # Compte les retours effectifs
    if batch_id:
        count_df = _run_query(
            "SELECT COUNT(*) AS n FROM equipment_movements WHERE batch_id = ? AND actual_return_date IS NOT NULL",
            [batch_id],
        )
    else:
        ids_ph = ", ".join(["?"] * len(movement_ids))
        count_df = _run_query(
            f"SELECT COUNT(*) AS n FROM equipment_movements WHERE movement_id IN ({ids_ph}) AND actual_return_date IS NOT NULL",
            movement_ids,
        )
    returned = int(count_df.iloc[0]["n"]) if not count_df.empty else 0

    return json.dumps({
        "ok": True,
        "returned_count": returned,
        "message": f"{returned} équipement(s) enregistré(s) comme rendu(s).",
    })


# ════════════════════════════════════════════════════════════
# OUTIL 6 — Liste des kits disponibles
# ════════════════════════════════════════════════════════════

@mcp.tool()
def list_kits() -> str:
    """
    Liste tous les kits / paniers disponibles dans SIGA.

    Chaque kit est une caisse à outils pré-configurée pour un type de chantier.
    Utilisez get_kit_content pour voir les équipements d'un kit spécifique.

    Returns:
        JSON avec la liste des kits (id, nom, description, nombre d'équipements).
    """
    df = _run_query(
        """
        SELECT k.kit_id, k.name, k.description, COUNT(ki.equipment_id) AS item_count
        FROM kits k
        LEFT JOIN kit_items ki ON ki.kit_id = k.kit_id
        GROUP BY k.kit_id, k.name, k.description
        ORDER BY k.name
        """
    )

    if df.empty:
        return json.dumps({"count": 0, "kits": [], "message": "Aucun kit configuré dans SIGA."})

    kits = [
        {
            "kit_id": str(row["kit_id"]),
            "name": str(row["name"] or ""),
            "description": str(row["description"]) if pd.notna(row.get("description")) else None,
            "item_count": int(row["item_count"]),
        }
        for _, row in df.iterrows()
    ]
    return json.dumps({"count": len(kits), "kits": kits}, ensure_ascii=False)


# ════════════════════════════════════════════════════════════
# OUTIL 7 — Contenu d'un kit
# ════════════════════════════════════════════════════════════

@mcp.tool()
def get_kit_content(kit_id: str) -> str:
    """
    Retourne le contenu détaillé d'un kit (liste des équipements).

    Args:
        kit_id: Identifiant du kit (champ kit_id de list_kits).

    Returns:
        JSON avec les informations du kit et la liste de ses équipements
        (id, nom, marque, modèle, état, emplacement).
    """
    kit_df = _run_query(
        "SELECT kit_id, name, description FROM kits WHERE kit_id = ? LIMIT 1",
        [kit_id],
    )
    if kit_df.empty:
        return json.dumps({"error": "kit_not_found", "kit_id": kit_id})

    kit_row = kit_df.iloc[0]

    items_df = _run_query(
        """
        SELECT e.equipment_id, e.label, e.brand, e.model,
               e.condition_label, e.location_hint
        FROM kit_items ki
        JOIN equipment e ON e.equipment_id = ki.equipment_id
        WHERE ki.kit_id = ?
        ORDER BY e.label
        """,
        [kit_id],
    )

    items = [
        {
            "equipment_id": str(row["equipment_id"]),
            "label": str(row["label"] or ""),
            "brand": str(row["brand"]) if pd.notna(row.get("brand")) else None,
            "model": str(row["model"]) if pd.notna(row.get("model")) else None,
            "condition": str(row["condition_label"]) if pd.notna(row.get("condition_label")) else None,
            "location": str(row["location_hint"]) if pd.notna(row.get("location_hint")) else None,
        }
        for _, row in items_df.iterrows()
    ]

    return json.dumps({
        "kit_id": str(kit_row["kit_id"]),
        "name": str(kit_row["name"] or ""),
        "description": str(kit_row["description"]) if pd.notna(kit_row.get("description")) else None,
        "item_count": len(items),
        "items": items,
    }, ensure_ascii=False)


# ════════════════════════════════════════════════════════════
# OUTIL 8 — Sortie d'un kit complet (préparation chantier)
# ════════════════════════════════════════════════════════════

@mcp.tool()
def checkout_kit(
    kit_id: str,
    borrower_name: str,
    movement_type: str = "LOAN",
    borrower_contact: Optional[str] = None,
    expected_return_date: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """
    Enregistre la sortie de tous les équipements d'un kit pour un chantier.

    Tous les mouvements partagent un batch_id commun et sont liés au kit_id.
    Utilisez checkin_kit avec le batch_id retourné pour enregistrer le retour.

    Args:
        kit_id: Identifiant du kit à sortir.
        borrower_name: Nom de l'emprunteur / chantier (obligatoire).
        movement_type: LOAN, RENTAL ou MAINTENANCE. Par défaut : LOAN.
        borrower_contact: Contact de l'emprunteur — optionnel.
        expected_return_date: Date de retour prévue au format YYYY-MM-DD — optionnel.
        notes: Notes libres (ex. nom du chantier, adresse) — optionnel.

    Returns:
        JSON avec le batch_id du lot, les movement_ids créés et un résumé.
    """
    mv_type = movement_type.upper()
    if mv_type not in VALID_MOVEMENT_TYPES:
        return json.dumps({"ok": False, "error": f"movement_type invalide : {mv_type}"})

    if not borrower_name or not borrower_name.strip():
        return json.dumps({"ok": False, "error": "borrower_name est obligatoire."})

    exp_dt = _parse_date(expected_return_date)
    if expected_return_date and exp_dt is None:
        return json.dumps({"ok": False, "error": f"expected_return_date invalide : '{expected_return_date}'"})

    kit_df = _run_query("SELECT kit_id, name FROM kits WHERE kit_id = ? LIMIT 1", [kit_id])
    if kit_df.empty:
        return json.dumps({"ok": False, "error": "kit_not_found", "kit_id": kit_id})

    kit_name = str(kit_df.iloc[0]["name"] or "")

    items_df = _run_query("SELECT equipment_id FROM kit_items WHERE kit_id = ?", [kit_id])
    if items_df.empty:
        return json.dumps({"ok": False, "error": f"Le kit '{kit_name}' ne contient aucun équipement."})

    equipment_ids = items_df["equipment_id"].tolist()
    batch_id = str(uuid.uuid4())
    movement_ids = []
    statements = []

    for eid in equipment_ids:
        mid = str(uuid.uuid4())
        movement_ids.append(mid)
        statements.append((
            """
            INSERT INTO equipment_movements
                (movement_id, equipment_id, movement_type,
                 borrower_name, borrower_contact,
                 out_date, expected_return_date, notes, batch_id, kit_id)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?)
            """,
            [mid, eid, mv_type,
             borrower_name.strip(),
             borrower_contact.strip() if borrower_contact else None,
             exp_dt, notes, batch_id, kit_id],
        ))

    _run_write_many(statements)

    return json.dumps({
        "ok": True,
        "kit_id": kit_id,
        "kit_name": kit_name,
        "batch_id": batch_id,
        "movement_ids": movement_ids,
        "count": len(equipment_ids),
        "message": (
            f"Kit '{kit_name}' sorti ({len(equipment_ids)} outil(s)) "
            f"pour '{borrower_name.strip()}'"
            + (f" — retour prévu le {expected_return_date}." if exp_dt else ".")
        ),
    }, ensure_ascii=False)


# ════════════════════════════════════════════════════════════
# OUTIL 9 — Retour d'un kit (total ou partiel)
# ════════════════════════════════════════════════════════════

@mcp.tool()
def checkin_kit(
    batch_id: str,
    returned_equipment_ids: Optional[list[str]] = None,
) -> str:
    """
    Enregistre le retour d'un kit sorti par lot.

    Si returned_equipment_ids est fourni, seuls ces équipements sont soldés
    (retour partiel — les autres restent ouverts).
    Si absent, tous les équipements du lot sont soldés (retour total).

    Args:
        batch_id: Identifiant du lot retourné par checkout_kit.
        returned_equipment_ids: Liste d'equipment_id effectivement rendus.
                                None ou liste vide = retour total du lot.

    Returns:
        JSON avec le nombre d'équipements retournés et les éventuels manquants.
    """
    if not batch_id:
        return json.dumps({"ok": False, "error": "batch_id est obligatoire."})

    if returned_equipment_ids:
        ids_ph = ", ".join(["?"] * len(returned_equipment_ids))
        _run_write(
            f"""
            UPDATE equipment_movements
            SET actual_return_date = CURRENT_TIMESTAMP
            WHERE batch_id = ?
              AND equipment_id IN ({ids_ph})
              AND actual_return_date IS NULL
            """,
            [batch_id] + returned_equipment_ids,
        )
    else:
        _run_write(
            "UPDATE equipment_movements SET actual_return_date = CURRENT_TIMESTAMP WHERE batch_id = ? AND actual_return_date IS NULL",
            [batch_id],
        )

    # Bilan du lot
    bilan_df = _run_query(
        """
        SELECT
            m.equipment_id,
            COALESCE(NULLIF(e.label,''), e.brand || ' ' || e.model, m.equipment_id) AS label,
            m.actual_return_date IS NOT NULL AS returned
        FROM equipment_movements m
        JOIN equipment e ON e.equipment_id = m.equipment_id
        WHERE m.batch_id = ?
        ORDER BY label
        """,
        [batch_id],
    )

    returned = bilan_df[bilan_df["returned"] == True].shape[0] if not bilan_df.empty else 0  # noqa: E712
    missing  = bilan_df[bilan_df["returned"] == False].shape[0] if not bilan_df.empty else 0  # noqa: E712
    missing_items = (
        bilan_df[bilan_df["returned"] == False]["label"].tolist()  # noqa: E712
        if not bilan_df.empty else []
    )

    return json.dumps({
        "ok": True,
        "batch_id": batch_id,
        "returned_count": returned,
        "missing_count": missing,
        "missing_items": missing_items,
        "message": (
            f"{returned} outil(s) retourné(s)"
            + (f", {missing} manquant(s) : {missing_items}" if missing else ".")
        ),
    }, ensure_ascii=False)


# ════════════════════════════════════════════════════════════
# OUTIL 10 — Affichage kiosque atelier
# ════════════════════════════════════════════════════════════

@mcp.tool()
def display_on_screen(equipment_id: str) -> str:
    """
    Affiche la fiche d'un équipement sur l'écran kiosque de l'atelier.

    Insère une commande dans la table ui_commands ; l'écran kiosque
    (Raspberry Pi 5, mode Streamlit ?kiosk=true) la détecte en moins de 2 s
    et bascule automatiquement sur la fiche de l'équipement demandé.

    Args:
        equipment_id: Identifiant unique de l'équipement à afficher.

    Returns:
        Message de confirmation.
    """
    command_id = str(uuid.uuid4())

    _run_write(
        """
        INSERT INTO ui_commands
            (command_id, target_ui, command_type, payload, created_at, executed)
        VALUES
            (?, 'atelier_pi_5', 'SHOW_EQUIPMENT', ?, CURRENT_TIMESTAMP, FALSE)
        """,
        [command_id, equipment_id],
    )

    return (
        f"Commande envoyée à l'écran de l'atelier. "
        f"L'équipement '{equipment_id}' sera affiché dans les 2 secondes "
        f"(command_id: {command_id})."
    )


# ════════════════════════════════════════════════════════════
# OUTILS 11-15 — Création & administration des kits
# ════════════════════════════════════════════════════════════

@mcp.tool()
def create_kit(
    name: str,
    description: Optional[str] = None,
    equipment_ids: Optional[list[str]] = None,
) -> str:
    """
    Crée un nouveau kit (caisse à outils / panier chantier).

    Un kit est un ensemble pré-configuré d'équipements destiné à un type de
    chantier ou d'intervention. Il peut être sorti entièrement d'un coup via
    checkout_kit().

    Args:
        name: Nom du kit (ex : "Caisse Plomberie Urgence"). Obligatoire.
        description: Description libre du kit — optionnel.
        equipment_ids: Liste d'equipment_id à inclure dès la création.
                       Si absent, le kit est créé vide et on peut le peupler
                       ensuite via add_equipment_to_kit() ou set_kit_content().

    Returns:
        JSON avec le kit_id généré et un message de confirmation.
    """
    if not name or not name.strip():
        return json.dumps({"ok": False, "error": "Le nom du kit est obligatoire."})

    kit_id = str(uuid.uuid4())

    _run_write(
        "INSERT INTO kits (kit_id, name, description) VALUES (?, ?, ?)",
        [kit_id, name.strip(), description.strip() if description else None],
    )

    added = 0
    if equipment_ids:
        # Vérifie que les équipements existent
        ids_ph = ", ".join(["?"] * len(equipment_ids))
        exists_df = _run_query(
            f"SELECT equipment_id FROM equipment WHERE equipment_id IN ({ids_ph})",
            equipment_ids,
        )
        found_ids = set(exists_df["equipment_id"].tolist())
        missing = [eid for eid in equipment_ids if eid not in found_ids]

        valid_ids = [eid for eid in equipment_ids if eid in found_ids]
        if valid_ids:
            stmts = [
                ("INSERT OR IGNORE INTO kit_items (kit_id, equipment_id) VALUES (?, ?)", [kit_id, eid])
                for eid in valid_ids
            ]
            _run_write_many(stmts)
            added = len(valid_ids)

        if missing:
            return json.dumps({
                "ok": True,
                "kit_id": kit_id,
                "added_count": added,
                "warning": f"Kit créé mais équipements introuvables ignorés : {missing}",
                "message": f"Kit '{name.strip()}' créé avec {added} équipement(s). {len(missing)} ID(s) ignoré(s).",
            }, ensure_ascii=False)

    return json.dumps({
        "ok": True,
        "kit_id": kit_id,
        "added_count": added,
        "message": f"Kit '{name.strip()}' créé" + (f" avec {added} équipement(s)." if added else " (vide)."),
    }, ensure_ascii=False)


@mcp.tool()
def add_equipment_to_kit(
    kit_id: str,
    equipment_ids: list[str],
) -> str:
    """
    Ajoute un ou plusieurs équipements à un kit existant.

    Les doublons sont ignorés silencieusement : si un équipement est déjà
    dans le kit, il n'est pas ajouté en double.

    Args:
        kit_id: Identifiant du kit cible.
        equipment_ids: Liste d'equipment_id à ajouter au kit.

    Returns:
        JSON avec le nombre d'équipements ajoutés.
    """
    if not equipment_ids:
        return json.dumps({"ok": False, "error": "equipment_ids ne peut pas être vide."})

    kit_df = _run_query("SELECT name FROM kits WHERE kit_id = ? LIMIT 1", [kit_id])
    if kit_df.empty:
        return json.dumps({"ok": False, "error": "kit_not_found", "kit_id": kit_id})

    ids_ph = ", ".join(["?"] * len(equipment_ids))
    exists_df = _run_query(
        f"SELECT equipment_id FROM equipment WHERE equipment_id IN ({ids_ph})",
        equipment_ids,
    )
    found_ids = set(exists_df["equipment_id"].tolist())
    missing = [eid for eid in equipment_ids if eid not in found_ids]

    valid_ids = [eid for eid in equipment_ids if eid in found_ids]
    if valid_ids:
        stmts = [
            ("INSERT OR IGNORE INTO kit_items (kit_id, equipment_id) VALUES (?, ?)", [kit_id, eid])
            for eid in valid_ids
        ]
        _run_write_many(stmts)

    result = {
        "ok": True,
        "kit_id": kit_id,
        "added_count": len(valid_ids),
        "message": f"{len(valid_ids)} équipement(s) ajouté(s) au kit.",
    }
    if missing:
        result["warning"] = f"IDs introuvables ignorés : {missing}"

    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def remove_equipment_from_kit(
    kit_id: str,
    equipment_ids: list[str],
) -> str:
    """
    Retire un ou plusieurs équipements d'un kit sans les supprimer du catalogue.

    Args:
        kit_id: Identifiant du kit.
        equipment_ids: Liste d'equipment_id à retirer du kit.

    Returns:
        JSON de confirmation.
    """
    if not equipment_ids:
        return json.dumps({"ok": False, "error": "equipment_ids ne peut pas être vide."})

    kit_df = _run_query("SELECT name FROM kits WHERE kit_id = ? LIMIT 1", [kit_id])
    if kit_df.empty:
        return json.dumps({"ok": False, "error": "kit_not_found", "kit_id": kit_id})

    ids_ph = ", ".join(["?"] * len(equipment_ids))
    _run_write(
        f"DELETE FROM kit_items WHERE kit_id = ? AND equipment_id IN ({ids_ph})",
        [kit_id] + equipment_ids,
    )

    return json.dumps({
        "ok": True,
        "kit_id": kit_id,
        "message": f"{len(equipment_ids)} équipement(s) retiré(s) du kit.",
    })


@mcp.tool()
def set_kit_content(
    kit_id: str,
    equipment_ids: list[str],
) -> str:
    """
    Remplace entièrement le contenu d'un kit.

    Opération atomique : vide d'abord le kit puis insère les nouveaux équipements.
    Utiliser cette fonction pour redéfinir complètement la composition d'un kit
    (ex. après recherche préalable via search_equipment).
    Passer une liste vide pour vider le kit sans le supprimer.

    Args:
        kit_id: Identifiant du kit à reconfigurer.
        equipment_ids: Nouvelle liste complète d'equipment_id pour ce kit.

    Returns:
        JSON avec le bilan de la mise à jour.
    """
    kit_df = _run_query("SELECT name FROM kits WHERE kit_id = ? LIMIT 1", [kit_id])
    if kit_df.empty:
        return json.dumps({"ok": False, "error": "kit_not_found", "kit_id": kit_id})

    kit_name = str(kit_df.iloc[0]["name"] or kit_id)

    missing = []
    valid_ids = equipment_ids[:]
    if equipment_ids:
        ids_ph = ", ".join(["?"] * len(equipment_ids))
        exists_df = _run_query(
            f"SELECT equipment_id FROM equipment WHERE equipment_id IN ({ids_ph})",
            equipment_ids,
        )
        found_ids = set(exists_df["equipment_id"].tolist())
        missing = [eid for eid in equipment_ids if eid not in found_ids]
        valid_ids = [eid for eid in equipment_ids if eid in found_ids]

    stmts: list[tuple] = [("DELETE FROM kit_items WHERE kit_id = ?", [kit_id])]
    stmts += [
        ("INSERT INTO kit_items (kit_id, equipment_id) VALUES (?, ?)", [kit_id, eid])
        for eid in valid_ids
    ]
    _run_write_many(stmts)

    result = {
        "ok": True,
        "kit_id": kit_id,
        "kit_name": kit_name,
        "item_count": len(valid_ids),
        "message": f"Kit '{kit_name}' reconfiguré avec {len(valid_ids)} équipement(s).",
    }
    if missing:
        result["warning"] = f"IDs introuvables ignorés : {missing}"

    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def delete_kit(kit_id: str) -> str:
    """
    Supprime un kit et toutes ses lignes dans kit_items.

    Les mouvements historiques référençant ce kit sont conservés.
    Cette action est irréversible : demander confirmation à l'utilisateur avant.

    Args:
        kit_id: Identifiant du kit à supprimer.

    Returns:
        JSON de confirmation ou d'erreur.
    """
    kit_df = _run_query("SELECT name FROM kits WHERE kit_id = ? LIMIT 1", [kit_id])
    if kit_df.empty:
        return json.dumps({"ok": False, "error": "kit_not_found", "kit_id": kit_id})

    name = str(kit_df.iloc[0]["name"] or kit_id)

    _run_write_many([
        ("DELETE FROM kit_items WHERE kit_id = ?", [kit_id]),
        ("DELETE FROM kits WHERE kit_id = ?", [kit_id]),
    ])

    return json.dumps({
        "ok": True,
        "kit_id": kit_id,
        "message": f"Kit '{name}' supprimé définitivement.",
    }, ensure_ascii=False)


# ════════════════════════════════════════════════════════════
# OUTILS RÉSERVATIONS & PLANNING
# ════════════════════════════════════════════════════════════

def _parse_date_mcp(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


@mcp.tool()
def check_reservation_conflicts(equipment_id: str, start_date: str, end_date: str) -> str:
    """
    Vérifie si un équipement est disponible pour une plage horaire donnée.

    Contrôle deux types de blocages :
    - Chevauchement avec une réservation existante (PENDING ou ACTIVE).
    - Équipement actuellement en MAINTENANCE (mouvement actif).

    Appeler cet outil AVANT de proposer une réservation à l'utilisateur.

    Args:
        equipment_id: Identifiant UUID de l'équipement.
        start_date:   Début de la plage souhaitée (format : YYYY-MM-DD ou YYYY-MM-DDTHH:MM).
        end_date:     Fin de la plage souhaitée  (format : YYYY-MM-DD ou YYYY-MM-DDTHH:MM).

    Returns:
        JSON indiquant has_conflict (bool) et la liste des conflits détectés.
    """
    start_dt = _parse_date_mcp(start_date)
    end_dt   = _parse_date_mcp(end_date)
    if not start_dt or not end_dt:
        return json.dumps({"ok": False, "error": "Dates invalides. Utiliser YYYY-MM-DD ou YYYY-MM-DDTHH:MM."})
    if end_dt <= start_dt:
        return json.dumps({"ok": False, "error": "end_date doit être postérieure à start_date."})

    conflicts = []

    # Chevauchements de réservations
    res_df = _run_query(
        """
        SELECT user_name, start_date, end_date, status
        FROM reservations
        WHERE equipment_id = ?
          AND status IN ('PENDING', 'ACTIVE')
          AND start_date < ?
          AND end_date   > ?
        """,
        [equipment_id, end_dt, start_dt],
    )
    for _, r in res_df.iterrows():
        conflicts.append({
            "type":       "reservation",
            "user_name":  str(r.get("user_name") or ""),
            "start_date": str(r["start_date"]) if r.get("start_date") is not None else None,
            "end_date":   str(r["end_date"])   if r.get("end_date")   is not None else None,
        })

    # Maintenance active
    maint_df = _run_query(
        """
        SELECT borrower_name, out_date, expected_return_date
        FROM equipment_movements
        WHERE equipment_id  = ?
          AND movement_type = 'MAINTENANCE'
          AND actual_return_date IS NULL
        LIMIT 1
        """,
        [equipment_id],
    )
    for _, r in maint_df.iterrows():
        conflicts.append({
            "type":            "maintenance",
            "user_name":       str(r.get("borrower_name") or ""),
            "start_date":      str(r["out_date"]) if r.get("out_date") is not None else None,
            "expected_return": str(r["expected_return_date"]) if r.get("expected_return_date") is not None else None,
        })

    result = {
        "equipment_id": equipment_id,
        "has_conflict": len(conflicts) > 0,
        "conflicts":    conflicts,
    }
    if not conflicts:
        result["message"] = "Aucun conflit détecté. La réservation est possible."
    else:
        c = conflicts[0]
        if c["type"] == "maintenance":
            result["message"] = f"L'équipement est en maintenance (responsable : {c.get('user_name', '?')})."
        else:
            result["message"] = (
                f"Déjà réservé par {c.get('user_name', '?')} "
                f"de {c.get('start_date', '?')} à {c.get('end_date', '?')}."
            )
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
def create_reservation(
    equipment_id: str,
    user_name: str,
    start_date: str,
    end_date: str,
) -> str:
    """
    Crée une réservation sur un équipement après vérification des conflits.

    Workflow conseillé : appeler check_reservation_conflicts d'abord, puis
    cette fonction seulement si has_conflict == False.

    Args:
        equipment_id: Identifiant UUID de l'équipement à réserver.
        user_name:    Prénom/nom de la personne qui réserve.
        start_date:   Début de la réservation (YYYY-MM-DD ou YYYY-MM-DDTHH:MM).
        end_date:     Fin de la réservation   (YYYY-MM-DD ou YYYY-MM-DDTHH:MM).

    Returns:
        JSON avec res_id si succès, ou message d'erreur/conflit.
    """
    start_dt = _parse_date_mcp(start_date)
    end_dt   = _parse_date_mcp(end_date)
    if not start_dt or not end_dt:
        return json.dumps({"ok": False, "error": "Dates invalides."})
    if end_dt <= start_dt:
        return json.dumps({"ok": False, "error": "end_date doit être postérieure à start_date."})

    # Vérification de l'équipement
    eq_df = _run_query("SELECT label FROM equipment WHERE equipment_id = ? LIMIT 1", [equipment_id])
    if eq_df.empty:
        return json.dumps({"ok": False, "error": "equipment_not_found", "equipment_id": equipment_id})

    # Vérification des conflits
    conflict_check = json.loads(
        check_reservation_conflicts(equipment_id, start_date, end_date)
    )
    if conflict_check.get("has_conflict"):
        return json.dumps({
            "ok": False,
            "error": "conflict",
            "message": conflict_check.get("message", "Conflit détecté."),
            "conflicts": conflict_check.get("conflicts", []),
        }, ensure_ascii=False)

    res_id = str(uuid.uuid4())
    _run_write(
        """
        INSERT INTO reservations
            (res_id, equipment_id, user_name, start_date, end_date, status, created_at)
        VALUES
            (?, ?, ?, ?, ?, 'PENDING', CURRENT_TIMESTAMP)
        """,
        [res_id, equipment_id, user_name, start_dt, end_dt],
    )

    eq_label = str(eq_df.iloc[0]["label"] or equipment_id)
    return json.dumps({
        "ok":      True,
        "res_id":  res_id,
        "message": f"C'est noté, '{eq_label}' est bloqué pour {user_name} du {start_date} au {end_date} !",
    }, ensure_ascii=False)


@mcp.tool()
def list_reservations(
    equipment_id: Optional[str] = None,
    user_name: Optional[str] = None,
) -> str:
    """
    Liste les réservations à venir ou en cours.

    Args:
        equipment_id: (optionnel) Filtrer par équipement.
        user_name:    (optionnel) Filtrer par nom d'utilisateur.

    Returns:
        JSON avec la liste des réservations actives/en attente.
    """
    conditions = ["r.status IN ('PENDING', 'ACTIVE')", "r.end_date >= CURRENT_TIMESTAMP"]
    params = []
    if equipment_id:
        conditions.append("r.equipment_id = ?")
        params.append(equipment_id)
    if user_name:
        conditions.append("LOWER(r.user_name) = LOWER(?)")
        params.append(user_name)

    where = " AND ".join(conditions)
    df = _run_query(
        f"""
        SELECT r.res_id, r.equipment_id, r.user_name, r.start_date, r.end_date, r.status,
               e.label AS equipment_label
        FROM reservations r
        JOIN equipment e ON e.equipment_id = r.equipment_id
        WHERE {where}
        ORDER BY r.start_date ASC
        """,
        params or None,
    )
    if df.empty:
        return json.dumps({"count": 0, "reservations": [], "message": "Aucune réservation à venir."})
    return _df_to_json(df)


@mcp.tool()
def cancel_reservation(res_id: str) -> str:
    """
    Annule une réservation existante.

    Args:
        res_id: Identifiant UUID de la réservation à annuler.

    Returns:
        JSON de confirmation ou d'erreur.
    """
    res_df = _run_query(
        "SELECT res_id, status FROM reservations WHERE res_id = ? LIMIT 1",
        [res_id],
    )
    if res_df.empty:
        return json.dumps({"ok": False, "error": "reservation_not_found", "res_id": res_id})

    current_status = str(res_df.iloc[0]["status"] or "")
    if current_status == "CANCELLED":
        return json.dumps({"ok": True, "res_id": res_id, "message": "Réservation déjà annulée."})

    _run_write("UPDATE reservations SET status = 'CANCELLED' WHERE res_id = ?", [res_id])
    return json.dumps({"ok": True, "res_id": res_id, "message": "Réservation annulée avec succès."}, ensure_ascii=False)


# ─── Lancement ────────────────────────────────────────────────

if __name__ == "__main__":
    # Transport SSE : Openclaw se connecte via http://<host>:8000/sse
    mcp.run(transport="sse")
