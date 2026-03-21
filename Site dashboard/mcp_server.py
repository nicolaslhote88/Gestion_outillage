"""
SIGA — Serveur MCP (Model Context Protocol)
Expose des outils pour qu'un agent IA (Openclaw) puisse :
  - Rechercher des équipements dans le catalogue DuckDB
  - Piloter l'affichage de l'écran kiosque atelier (Raspberry Pi 5)

Transport : SSE (Server-Sent Events) — connexion via HTTP/web.
Concurrence DuckDB : utilise le même pattern retry/backoff que app.py
  (5 tentatives, backoff exponentiel à 2 s) pour cohabiter avec n8n.

Lancement :
    python mcp_server.py
ou via la CLI officielle :
    mcp dev mcp_server.py
"""

import time
import uuid

import duckdb
import pandas as pd
from mcp.server.fastmcp import FastMCP

# ─── Configuration ────────────────────────────────────────────
DB_PATH = "/files/duckdb/siga_v1.duckdb"

# ─── Utilitaires DuckDB (même pattern que app.py) ─────────────

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


# ─── Serveur MCP ──────────────────────────────────────────────

mcp = FastMCP(
    name="SIGA",
    instructions=(
        "Tu interagis avec le système SIGA de gestion d'outillage industriel. "
        "Tu peux rechercher des équipements dans le catalogue et piloter "
        "l'affichage de l'écran kiosque de l'atelier."
    ),
)


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

    # Sérialise les timestamps en chaînes lisibles
    for col in df.select_dtypes(include=["datetime64[ns]", "datetime64[us]"]).columns:
        df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M")

    return df.to_json(orient="records", force_ascii=False)


@mcp.tool()
def display_on_screen(equipment_id: str) -> str:
    """
    Affiche la fiche d'un équipement sur l'écran kiosque de l'atelier.

    Insère une commande dans la table ui_commands ; l'écran kiosque
    (Raspberry Pi 5, mode Streamlit ?kiosk=true) la détecte en moins de 2 s
    et bascule automatiquement sur la fiche de l'équipement demandé.

    Args:
        equipment_id: Identifiant unique de l'équipement à afficher
                      (champ equipment_id du catalogue SIGA).

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


# ─── Lancement ────────────────────────────────────────────────

if __name__ == "__main__":
    # Transport SSE : Openclaw se connecte via http://<host>:8000/sse
    mcp.run(transport="sse")
