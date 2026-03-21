"""
SIGA — API HTTP Métier
Interface JSON simple pour le skill OpenClaw (chat principal + WhatsApp).

Deux opérations :
  GET  /api/equipment/search?q=<texte>  → recherche fuzzy avec score
  POST /api/display/show                → envoie une commande au kiosque atelier

Auth : Bearer token statique (header Authorization: Bearer <token>)
       Défini par la variable d'environnement SIGA_API_TOKEN.
       Par défaut : "siga-secret-token-change-me" (à surcharger en prod).

Lancement :
    uvicorn api_server:app --host 0.0.0.0 --port 8001
ou
    python api_server.py
"""

import os
import time
import uuid
from difflib import SequenceMatcher
from typing import List, Optional

import duckdb
import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

# ─── Configuration ────────────────────────────────────────────
DB_PATH    = "/files/duckdb/siga_v1.duckdb"
API_TOKEN  = os.getenv("SIGA_API_TOKEN", "siga-secret-token-change-me")
API_PORT   = int(os.getenv("SIGA_API_PORT", "8001"))

SCORE_MIN  = 0.25   # Score minimum pour inclure un résultat
MAX_RESULTS = 20

# ─── Auth ─────────────────────────────────────────────────────
security = HTTPBearer(auto_error=True)


def _require_token(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> None:
    if credentials.credentials != API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide",
        )


# ─── DuckDB helpers (même pattern retry que app.py) ───────────

def _run_query(sql: str, params=None) -> pd.DataFrame:
    try:
        with duckdb.connect(DB_PATH, read_only=True) as conn:
            if params:
                return conn.execute(sql, params).df()
            return conn.execute(sql).df()
    except Exception as e:
        raise RuntimeError(f"Lecture DuckDB : {e}") from e


def _run_write(sql: str, params=None, _retries: int = 5) -> None:
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
            raise RuntimeError(f"Écriture DuckDB : {e}") from e
    raise RuntimeError(
        f"Base inaccessible après {_retries} tentatives (lock n8n ?) : {last_err}"
    )


# ─── Scoring fuzzy ────────────────────────────────────────────

def _score(query: str, row: dict) -> float:
    """
    Calcule un score de pertinence [0, 1] entre la requête et une ligne equipment.

    Stratégie par priorité décroissante :
      1. Égalité exacte (insensible à la casse)        → 1.00
      2. Sous-chaîne exacte dans le champ               → 0.85 × poids
      3. Tous les mots de la requête présents            → 0.75 × poids
      4. SequenceMatcher ratio (correspondance partielle)→ ratio × poids
    """
    q = query.strip().lower()
    words = q.split()

    # Champs à scorer, avec leur poids
    fields = [
        (str(row.get("label",    "") or ""), 1.0),
        (str(row.get("brand",    "") or ""), 0.9),
        (str(row.get("model",    "") or ""), 0.9),
        (str(row.get("subtype",  "") or ""), 0.75),
    ]

    best = 0.0
    for text, weight in fields:
        t = text.lower().strip()
        if not t:
            continue

        if q == t:
            s = 1.0
        elif q in t or t in q:
            s = 0.85
        elif all(w in t for w in words):
            s = 0.75
        else:
            s = SequenceMatcher(None, q, t).ratio()

        best = max(best, s * weight)

    return round(min(best, 1.0), 3)


# ─── Schémas Pydantic ─────────────────────────────────────────

class EquipmentResult(BaseModel):
    equipment_id: str
    label: str
    brand: Optional[str] = None
    model: Optional[str] = None
    category: Optional[str] = None
    condition: Optional[str] = None
    location: Optional[str] = None
    score: float


class SearchResponse(BaseModel):
    query: str
    count: int
    results: List[EquipmentResult]


class DisplayRequest(BaseModel):
    equipment_id: str


class DisplayResponse(BaseModel):
    ok: bool
    equipment_id: str
    display_status: str
    screen: str
    message: str


class ErrorResponse(BaseModel):
    ok: bool
    error: str
    detail: Optional[str] = None
    equipment_id: Optional[str] = None


# ─── App FastAPI ──────────────────────────────────────────────

app = FastAPI(
    title="SIGA API",
    description="API HTTP métier pour le pilotage de SIGA par OpenClaw.",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
)


@app.get(
    "/api/equipment/search",
    response_model=SearchResponse,
    summary="Recherche d'équipement par texte libre",
    tags=["Équipements"],
)
def search_equipment(
    q: str,
    _: None = Security(_require_token),
) -> SearchResponse:
    """
    Recherche fuzzy dans le catalogue.

    - Cherche dans : **label**, **brand**, **model**, **subtype**
    - Tri par score de pertinence décroissant
    - Renvoie une liste vide si aucun résultat (pas d'erreur HTTP)
    """
    if not q or not q.strip():
        return SearchResponse(query=q, count=0, results=[])

    like = f"%{q.strip()}%"

    try:
        df = _run_query(
            """
            SELECT
                equipment_id,
                label,
                brand,
                model,
                subtype,
                condition_label,
                location_hint
            FROM equipment
            WHERE
                LOWER(label)   LIKE LOWER(?)
                OR LOWER(brand)  LIKE LOWER(?)
                OR LOWER(model)  LIKE LOWER(?)
                OR LOWER(subtype) LIKE LOWER(?)
            ORDER BY received_at DESC
            LIMIT 100
            """,
            [like, like, like, like],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    if df.empty:
        # Aucun résultat LIKE → on retourne vide (pas d'erreur)
        return SearchResponse(query=q, count=0, results=[])

    # Score chaque ligne et filtre
    results: List[EquipmentResult] = []
    for _, row in df.iterrows():
        sc = _score(q, row.to_dict())
        if sc < SCORE_MIN:
            continue
        results.append(
            EquipmentResult(
                equipment_id=str(row["equipment_id"]),
                label=str(row["label"] or ""),
                brand=str(row["brand"]) if pd.notna(row.get("brand")) else None,
                model=str(row["model"]) if pd.notna(row.get("model")) else None,
                category=str(row["subtype"]) if pd.notna(row.get("subtype")) else None,
                condition=str(row["condition_label"]) if pd.notna(row.get("condition_label")) else None,
                location=str(row["location_hint"]) if pd.notna(row.get("location_hint")) else None,
                score=sc,
            )
        )

    results.sort(key=lambda r: r.score, reverse=True)
    results = results[:MAX_RESULTS]

    return SearchResponse(query=q, count=len(results), results=results)


@app.post(
    "/api/display/show",
    response_model=DisplayResponse,
    summary="Affiche un équipement sur l'écran kiosque atelier",
    tags=["Kiosque"],
    responses={
        404: {"model": ErrorResponse, "description": "Équipement inconnu"},
        503: {"model": ErrorResponse, "description": "Écran ou base indisponible"},
    },
)
def display_equipment(
    body: DisplayRequest,
    _: None = Security(_require_token),
) -> DisplayResponse:
    """
    Envoie une commande `SHOW_EQUIPMENT` au kiosque Raspberry Pi 5.

    L'écran kiosque (Streamlit `?kiosk=true`) poll la table `ui_commands`
    toutes les 2 secondes et bascule automatiquement sur la fiche.
    """
    # Vérifie que l'équipement existe
    try:
        exists_df = _run_query(
            "SELECT equipment_id FROM equipment WHERE equipment_id = ? LIMIT 1",
            [body.equipment_id],
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                ok=False,
                error="screen_unavailable",
                detail=str(e),
            ).model_dump(),
        ) from e

    if exists_df.empty:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                ok=False,
                error="equipment_not_found",
                equipment_id=body.equipment_id,
            ).model_dump(),
        )

    # Insère la commande dans la boîte aux lettres
    command_id = str(uuid.uuid4())
    try:
        _run_write(
            """
            INSERT INTO ui_commands
                (command_id, target_ui, command_type, payload, created_at, executed)
            VALUES
                (?, 'atelier_pi_5', 'SHOW_EQUIPMENT', ?, CURRENT_TIMESTAMP, FALSE)
            """,
            [command_id, body.equipment_id],
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                ok=False,
                error="screen_unavailable",
                detail=str(e),
            ).model_dump(),
        ) from e

    return DisplayResponse(
        ok=True,
        equipment_id=body.equipment_id,
        display_status="sent",
        screen="atelier-main",
        message=(
            f"Commande d'affichage transmise à l'écran atelier. "
            f"L'équipement '{body.equipment_id}' sera visible dans ≤ 2 s."
        ),
    )


# ─── Health check (sans auth) ─────────────────────────────────

@app.get("/api/health", tags=["Système"], summary="État du serveur")
def health() -> dict:
    """Vérifie que l'API est vivante et que la base est accessible."""
    try:
        _run_query("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok", "db": "reachable" if db_ok else "unreachable"}


# ─── Lancement direct ─────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=API_PORT,
        log_level="info",
    )
