"""
SIGA — API HTTP Métier
Interface JSON simple pour le skill OpenClaw (chat principal + WhatsApp).

Opérations :
  GET  /api/equipment/search?q=<texte>       → recherche fuzzy avec score
  GET  /api/equipment/{id}/status            → disponibilité d'un équipement
  POST /api/display/show                     → envoie une commande au kiosque atelier
  POST /api/movements/checkout               → sortie individuelle ou groupée
  POST /api/movements/checkin                → entrée individuelle, groupée ou par batch
  GET  /api/movements/active                 → liste des sorties en cours
  GET  /api/kits                             → liste des kits disponibles
  GET  /api/kits/{kit_id}                    → détail + contenu d'un kit
  POST /api/kits/{kit_id}/checkout           → sortie d'un kit complet
  POST /api/kits/{kit_id}/checkin            → retour d'un kit (par batch_id)
  POST /api/kits                             → créer un kit (+ peupler en une passe)
  PUT  /api/kits/{kit_id}                    → renommer / modifier la description
  DELETE /api/kits/{kit_id}                  → supprimer un kit
  POST /api/kits/{kit_id}/items              → ajouter des équipements à un kit
  DELETE /api/kits/{kit_id}/items            → retirer des équipements d'un kit
  PUT  /api/kits/{kit_id}/content            → remplacer entièrement le contenu

Auth : Bearer token statique (header Authorization: Bearer <token>)
       Défini par la variable d'environnement SIGA_API_TOKEN.
       Par défaut : "siga-secret-token-change-me" (à surcharger en prod).

Lancement :
    uvicorn api_server:app --host 0.0.0.0 --port 8001
ou
    python api_server.py
"""

import json
import os
import time
import uuid
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

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

SCORE_MIN  = 0.25
MAX_RESULTS = 20

VALID_MOVEMENT_TYPES = {"LOAN", "RENTAL", "MAINTENANCE"}

# Fichier JSON partagé avec le kiosque Streamlit — élimine tout polling DuckDB
KIOSK_STATE_FILE = Path(DB_PATH).parent / "kiosk_state.json"

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


# ─── DuckDB helpers ───────────────────────────────────────────

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
            raise RuntimeError(f"Écriture DuckDB : {e}") from e
    raise RuntimeError(
        f"Base inaccessible après {_retries} tentatives (lock n8n ?) : {last_err}"
    )


def _s(v) -> str:
    """Convertit n'importe quelle valeur DuckDB/pandas en str sans lever d'exception.

    Problème : DuckDB récent retourne pd.NA (pandas NA) pour les VARCHAR nuls.
    bool(pd.NA) lève TypeError, donc les patterns 'v or ""' explosent silencieusement.
    Cette fonction évite toute évaluation booléenne : on passe direct à str().
    """
    if v is None:
        return ""
    s = str(v)
    return "" if s in ("nan", "NaT", "<NA>", "None", "nat") else s


# ─── Transport kiosque (JSON file) ───────────────────────────

def _write_kiosk_state(command_type: str, data: Dict[str, Any]) -> None:
    """Écrit l'état courant du kiosque dans un fichier JSON (écriture atomique).

    Le kiosque Streamlit lit ce fichier toutes les 2 s via le système de
    fichiers — sans jamais ouvrir de connexion DuckDB — ce qui supprime
    le verrou continu observé avec le polling sur ui_commands.
    """
    state = {
        "command_type": command_type,
        "updated_at": datetime.utcnow().isoformat(),
        "data": data,
    }
    tmp = KIOSK_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    tmp.replace(KIOSK_STATE_FILE)


# ─── Scoring fuzzy ────────────────────────────────────────────

def _score(query: str, row: dict) -> float:
    q = query.strip().lower()
    words = q.split()
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


class DisplayGenericResponse(BaseModel):
    ok: bool
    command_type: str
    display_status: str
    screen: str
    message: str


class DisplayKitRequest(BaseModel):
    kit_id: str


class DisplayConfirmationRequest(BaseModel):
    title: str
    subtitle: str
    details: List[str] = []
    batch_id: Optional[str] = None
    color: str = "green"  # "green" | "red" | "blue"


class ErrorResponse(BaseModel):
    ok: bool
    error: str
    detail: Optional[str] = None
    equipment_id: Optional[str] = None


# — Mouvements ────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    equipment_ids: List[str]
    borrower_name: str
    movement_type: str = "LOAN"          # LOAN | RENTAL | MAINTENANCE
    borrower_contact: Optional[str] = None
    expected_return_date: Optional[str] = None   # ISO 8601 : "2025-06-30"
    notes: Optional[str] = None


class CheckoutResponse(BaseModel):
    ok: bool
    batch_id: str
    movement_ids: List[str]
    count: int
    message: str


class CheckinRequest(BaseModel):
    movement_ids: Optional[List[str]] = None   # entrée individuelle ou liste
    batch_id: Optional[str] = None             # retour groupé par lot


class CheckinResponse(BaseModel):
    ok: bool
    returned_count: int
    message: str


class ActiveMovementItem(BaseModel):
    movement_id: str
    equipment_id: str
    label: str
    borrower_name: str
    movement_type: str
    out_date: Optional[str] = None
    expected_return_date: Optional[str] = None
    batch_id: Optional[str] = None
    kit_id: Optional[str] = None
    kit_name: Optional[str] = None
    is_late: bool


class ActiveMovementsResponse(BaseModel):
    count: int
    items: List[ActiveMovementItem]


class EquipmentStatusResponse(BaseModel):
    equipment_id: str
    label: str
    available: bool
    movement_type: Optional[str] = None
    borrower_name: Optional[str] = None
    out_date: Optional[str] = None
    expected_return_date: Optional[str] = None


# — Kits ──────────────────────────────────────────────────────

class KitCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    equipment_ids: Optional[List[str]] = None   # optionnel : peupler le kit à la création


class KitUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class KitAddItemsRequest(BaseModel):
    equipment_ids: List[str]


class KitSetContentRequest(BaseModel):
    equipment_ids: List[str]   # remplace entièrement le contenu existant


class KitMutationResponse(BaseModel):
    ok: bool
    kit_id: str
    message: str


class KitSummary(BaseModel):
    kit_id: str
    name: str
    description: Optional[str] = None
    item_count: int


class KitListResponse(BaseModel):
    count: int
    kits: List[KitSummary]


class KitItem(BaseModel):
    equipment_id: str
    label: str
    brand: Optional[str] = None
    model: Optional[str] = None
    condition: Optional[str] = None
    location: Optional[str] = None


class KitDetailResponse(BaseModel):
    kit_id: str
    name: str
    description: Optional[str] = None
    item_count: int
    items: List[KitItem]


class KitCheckoutRequest(BaseModel):
    borrower_name: str
    movement_type: str = "LOAN"
    borrower_contact: Optional[str] = None
    expected_return_date: Optional[str] = None
    notes: Optional[str] = None


class KitCheckinRequest(BaseModel):
    batch_id: str
    returned_equipment_ids: Optional[List[str]] = None   # None = tout renvoyer


# ─── App FastAPI ──────────────────────────────────────────────

app = FastAPI(
    title="SIGA API",
    description="API HTTP métier pour le pilotage de SIGA par OpenClaw.",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url=None,
)


# ════════════════════════════════════════════════════════════
# ÉQUIPEMENTS — Recherche & Statut
# ════════════════════════════════════════════════════════════

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
    """Recherche fuzzy dans le catalogue (label, brand, model, subtype)."""
    if not q or not q.strip():
        return SearchResponse(query=q, count=0, results=[])

    like = f"%{q.strip()}%"
    try:
        df = _run_query(
            """
            SELECT
                equipment_id, label, brand, model,
                subtype, condition_label, location_hint
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
        return SearchResponse(query=q, count=0, results=[])

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
    return SearchResponse(query=q, count=len(results), results=results[:MAX_RESULTS])


@app.get(
    "/api/equipment/{equipment_id}/status",
    response_model=EquipmentStatusResponse,
    summary="Disponibilité d'un équipement",
    tags=["Équipements"],
    responses={404: {"model": ErrorResponse}},
)
def equipment_status(
    equipment_id: str,
    _: None = Security(_require_token),
) -> EquipmentStatusResponse:
    """Indique si un équipement est disponible ou actuellement sorti."""
    try:
        eq_df = _run_query(
            "SELECT equipment_id, label FROM equipment WHERE equipment_id = ? LIMIT 1",
            [equipment_id],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    if eq_df.empty:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(ok=False, error="equipment_not_found", equipment_id=equipment_id).model_dump(),
        )

    label = str(eq_df.iloc[0]["label"] or "")

    try:
        mv_df = _run_query(
            """
            SELECT movement_type, borrower_name, out_date, expected_return_date
            FROM equipment_movements
            WHERE equipment_id = ? AND actual_return_date IS NULL
            ORDER BY out_date DESC LIMIT 1
            """,
            [equipment_id],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    if mv_df.empty:
        return EquipmentStatusResponse(equipment_id=equipment_id, label=label, available=True)

    row = mv_df.iloc[0]
    return EquipmentStatusResponse(
        equipment_id=equipment_id,
        label=label,
        available=False,
        movement_type=str(row["movement_type"]) if pd.notna(row.get("movement_type")) else None,
        borrower_name=str(row["borrower_name"]) if pd.notna(row.get("borrower_name")) else None,
        out_date=str(row["out_date"])[:16] if pd.notna(row.get("out_date")) else None,
        expected_return_date=str(row["expected_return_date"])[:16] if pd.notna(row.get("expected_return_date")) else None,
    )


# ════════════════════════════════════════════════════════════
# MOUVEMENTS — Sorties & Entrées
# ════════════════════════════════════════════════════════════

@app.post(
    "/api/movements/checkout",
    response_model=CheckoutResponse,
    summary="Sortie d'équipement (individuelle ou groupée)",
    tags=["Mouvements"],
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def checkout_equipment(
    body: CheckoutRequest,
    _: None = Security(_require_token),
) -> CheckoutResponse:
    """
    Enregistre la sortie d'un ou plusieurs équipements.

    - Un seul `equipment_id` → mouvement individuel (pas de batch_id).
    - Plusieurs `equipment_ids` → sortie groupée, tous partagent un `batch_id`.
    - `movement_type` : `LOAN` (prêt), `RENTAL` (location), `MAINTENANCE`.
    - `expected_return_date` au format ISO 8601 (`YYYY-MM-DD` ou `YYYY-MM-DDTHH:MM`).
    """
    if not body.equipment_ids:
        raise HTTPException(status_code=400, detail="equipment_ids ne peut pas être vide.")

    mv_type = body.movement_type.upper()
    if mv_type not in VALID_MOVEMENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"movement_type invalide : {mv_type}. Valeurs : {VALID_MOVEMENT_TYPES}",
        )

    if not body.borrower_name or not body.borrower_name.strip():
        raise HTTPException(status_code=400, detail="borrower_name est obligatoire.")

    # Parse expected_return_date
    exp_dt: Optional[datetime] = None
    if body.expected_return_date:
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                exp_dt = datetime.strptime(body.expected_return_date, fmt)
                break
            except ValueError:
                continue
        if exp_dt is None:
            raise HTTPException(
                status_code=400,
                detail=f"expected_return_date invalide : '{body.expected_return_date}'. Format attendu : YYYY-MM-DD",
            )

    # Vérifie que tous les equipment_ids existent
    try:
        ids_placeholder = ", ".join(["?"] * len(body.equipment_ids))
        exists_df = _run_query(
            f"SELECT equipment_id FROM equipment WHERE equipment_id IN ({ids_placeholder})",
            body.equipment_ids,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    found_ids = set(exists_df["equipment_id"].tolist())
    missing = [eid for eid in body.equipment_ids if eid not in found_ids]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Équipements introuvables : {missing}",
        )

    # Génère batch_id si sortie groupée
    batch_id = str(uuid.uuid4()) if len(body.equipment_ids) > 1 else None
    movement_ids: List[str] = []
    statements = []

    for eid in body.equipment_ids:
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
             body.borrower_name.strip(),
             body.borrower_contact.strip() if body.borrower_contact else None,
             exp_dt, body.notes, batch_id],
        ))

    try:
        _run_write_many(statements)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    count = len(body.equipment_ids)
    msg = (
        f"{count} équipement(s) sorti(s) pour '{body.borrower_name.strip()}' "
        f"(type : {mv_type}"
        + (f", retour prévu : {body.expected_return_date}" if exp_dt else "")
        + ")."
    )
    return CheckoutResponse(
        ok=True,
        batch_id=batch_id or movement_ids[0],
        movement_ids=movement_ids,
        count=count,
        message=msg,
    )


@app.post(
    "/api/movements/checkin",
    response_model=CheckinResponse,
    summary="Entrée d'équipement (individuelle, groupée ou par batch)",
    tags=["Mouvements"],
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def checkin_equipment(
    body: CheckinRequest,
    _: None = Security(_require_token),
) -> CheckinResponse:
    """
    Enregistre le retour d'un ou plusieurs équipements.

    - Fournir `movement_ids` pour un retour ciblé (liste de 1..N).
    - Fournir `batch_id` pour solder tous les équipements d'un lot.
    - Les deux peuvent être combinés.
    """
    if not body.movement_ids and not body.batch_id:
        raise HTTPException(status_code=400, detail="Fournir movement_ids ou batch_id.")

    statements = []
    now_str = datetime.now().isoformat(sep=" ", timespec="seconds")

    if body.movement_ids:
        for mid in body.movement_ids:
            statements.append((
                "UPDATE equipment_movements SET actual_return_date = CURRENT_TIMESTAMP WHERE movement_id = ? AND actual_return_date IS NULL",
                [mid],
            ))

    if body.batch_id:
        statements.append((
            "UPDATE equipment_movements SET actual_return_date = CURRENT_TIMESTAMP WHERE batch_id = ? AND actual_return_date IS NULL",
            [body.batch_id],
        ))

    try:
        _run_write_many(statements)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    # Compte les lignes effectivement mises à jour
    try:
        if body.batch_id:
            count_df = _run_query(
                "SELECT COUNT(*) AS n FROM equipment_movements WHERE batch_id = ? AND actual_return_date IS NOT NULL",
                [body.batch_id],
            )
        else:
            ids_ph = ", ".join(["?"] * len(body.movement_ids))
            count_df = _run_query(
                f"SELECT COUNT(*) AS n FROM equipment_movements WHERE movement_id IN ({ids_ph}) AND actual_return_date IS NOT NULL",
                body.movement_ids,
            )
        returned = int(count_df.iloc[0]["n"]) if not count_df.empty else 0
    except RuntimeError:
        returned = len(body.movement_ids or [])

    return CheckinResponse(
        ok=True,
        returned_count=returned,
        message=f"{returned} équipement(s) enregistré(s) comme rendu(s).",
    )


@app.get(
    "/api/movements/active",
    response_model=ActiveMovementsResponse,
    summary="Liste des équipements actuellement sortis",
    tags=["Mouvements"],
)
def active_movements(
    _: None = Security(_require_token),
) -> ActiveMovementsResponse:
    """
    Retourne tous les mouvements non soldés (actual_return_date IS NULL),
    triés par date de sortie décroissante.
    Indique si le retour est en retard (`is_late`).
    """
    try:
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
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    now = datetime.now()
    items: List[ActiveMovementItem] = []
    for _, row in df.iterrows():
        exp = row.get("expected_return_date")
        is_late = bool(pd.notna(exp) and pd.Timestamp(exp).to_pydatetime() < now)
        items.append(ActiveMovementItem(
            movement_id=str(row["movement_id"]),
            equipment_id=str(row["equipment_id"]),
            label=str(row["label"] or ""),
            borrower_name=str(row["borrower_name"] or ""),
            movement_type=str(row["movement_type"] or ""),
            out_date=str(row["out_date"])[:16] if pd.notna(row.get("out_date")) else None,
            expected_return_date=str(row["expected_return_date"])[:16] if pd.notna(row.get("expected_return_date")) else None,
            batch_id=str(row["batch_id"]) if pd.notna(row.get("batch_id")) else None,
            kit_id=str(row["kit_id"]) if pd.notna(row.get("kit_id")) else None,
            kit_name=str(row["kit_name"]) if pd.notna(row.get("kit_name")) else None,
            is_late=is_late,
        ))

    return ActiveMovementsResponse(count=len(items), items=items)


# ════════════════════════════════════════════════════════════
# KITS — Gestion des paniers / caisses à outils
# ════════════════════════════════════════════════════════════

@app.get(
    "/api/kits",
    response_model=KitListResponse,
    summary="Liste de tous les kits disponibles",
    tags=["Kits"],
)
def list_kits(
    _: None = Security(_require_token),
) -> KitListResponse:
    """Retourne tous les kits avec leur nombre d'équipements."""
    try:
        df = _run_query(
            """
            SELECT k.kit_id, k.name, k.description, COUNT(ki.equipment_id) AS item_count
            FROM kits k
            LEFT JOIN kit_items ki ON ki.kit_id = k.kit_id
            GROUP BY k.kit_id, k.name, k.description
            ORDER BY k.name
            """
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    kits = [
        KitSummary(
            kit_id=str(row["kit_id"]),
            name=str(row["name"] or ""),
            description=str(row["description"]) if pd.notna(row.get("description")) else None,
            item_count=int(row["item_count"]),
        )
        for _, row in df.iterrows()
    ]
    return KitListResponse(count=len(kits), kits=kits)


@app.get(
    "/api/kits/{kit_id}",
    response_model=KitDetailResponse,
    summary="Détail d'un kit avec son contenu",
    tags=["Kits"],
    responses={404: {"model": ErrorResponse}},
)
def get_kit(
    kit_id: str,
    _: None = Security(_require_token),
) -> KitDetailResponse:
    """Retourne les informations du kit et la liste détaillée de ses équipements."""
    try:
        kit_df = _run_query(
            "SELECT kit_id, name, description FROM kits WHERE kit_id = ? LIMIT 1",
            [kit_id],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    if kit_df.empty:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(ok=False, error="kit_not_found").model_dump(),
        )

    kit_row = kit_df.iloc[0]

    try:
        items_df = _run_query(
            """
            SELECT e.equipment_id, e.label, e.brand, e.model, e.condition_label, e.location_hint
            FROM kit_items ki
            JOIN equipment e ON e.equipment_id = ki.equipment_id
            WHERE ki.kit_id = ?
            ORDER BY e.label
            """,
            [kit_id],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    items = [
        KitItem(
            equipment_id=str(row["equipment_id"]),
            label=str(row["label"] or ""),
            brand=str(row["brand"]) if pd.notna(row.get("brand")) else None,
            model=str(row["model"]) if pd.notna(row.get("model")) else None,
            condition=str(row["condition_label"]) if pd.notna(row.get("condition_label")) else None,
            location=str(row["location_hint"]) if pd.notna(row.get("location_hint")) else None,
        )
        for _, row in items_df.iterrows()
    ]

    return KitDetailResponse(
        kit_id=str(kit_row["kit_id"]),
        name=str(kit_row["name"] or ""),
        description=str(kit_row["description"]) if pd.notna(kit_row.get("description")) else None,
        item_count=len(items),
        items=items,
    )


@app.post(
    "/api/kits/{kit_id}/checkout",
    response_model=CheckoutResponse,
    summary="Sortie d'un kit complet pour un chantier",
    tags=["Kits"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def checkout_kit(
    kit_id: str,
    body: KitCheckoutRequest,
    _: None = Security(_require_token),
) -> CheckoutResponse:
    """
    Enregistre la sortie de tous les équipements d'un kit pour un chantier.

    Tous les mouvements partagent un `batch_id` commun et sont liés au `kit_id`.
    Permet le retour groupé via `/api/kits/{kit_id}/checkin`.
    """
    mv_type = body.movement_type.upper()
    if mv_type not in VALID_MOVEMENT_TYPES:
        raise HTTPException(status_code=400, detail=f"movement_type invalide : {mv_type}")

    if not body.borrower_name or not body.borrower_name.strip():
        raise HTTPException(status_code=400, detail="borrower_name est obligatoire.")

    exp_dt: Optional[datetime] = None
    if body.expected_return_date:
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                exp_dt = datetime.strptime(body.expected_return_date, fmt)
                break
            except ValueError:
                continue
        if exp_dt is None:
            raise HTTPException(
                status_code=400,
                detail=f"expected_return_date invalide : '{body.expected_return_date}'",
            )

    # Vérifie que le kit existe
    try:
        kit_df = _run_query("SELECT kit_id, name FROM kits WHERE kit_id = ? LIMIT 1", [kit_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    if kit_df.empty:
        raise HTTPException(status_code=404, detail=ErrorResponse(ok=False, error="kit_not_found").model_dump())

    kit_name = str(kit_df.iloc[0]["name"] or "")

    # Récupère les équipements du kit
    try:
        items_df = _run_query("SELECT equipment_id FROM kit_items WHERE kit_id = ?", [kit_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    if items_df.empty:
        raise HTTPException(
            status_code=400,
            detail=f"Le kit '{kit_name}' ne contient aucun équipement.",
        )

    equipment_ids = items_df["equipment_id"].tolist()
    batch_id = str(uuid.uuid4())
    movement_ids: List[str] = []
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
             body.borrower_name.strip(),
             body.borrower_contact.strip() if body.borrower_contact else None,
             exp_dt, body.notes, batch_id, kit_id],
        ))

    try:
        _run_write_many(statements)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    count = len(equipment_ids)
    msg = (
        f"Kit '{kit_name}' sorti ({count} outil(s)) pour '{body.borrower_name.strip()}' "
        + (f"— retour prévu le {body.expected_return_date}." if exp_dt else ".")
    )
    return CheckoutResponse(ok=True, batch_id=batch_id, movement_ids=movement_ids, count=count, message=msg)


@app.post(
    "/api/kits/{kit_id}/checkin",
    response_model=CheckinResponse,
    summary="Retour d'un kit (total ou partiel via batch_id)",
    tags=["Kits"],
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def checkin_kit(
    kit_id: str,
    body: KitCheckinRequest,
    _: None = Security(_require_token),
) -> CheckinResponse:
    """
    Enregistre le retour d'un kit sorti par lot.

    - `batch_id` : identifiant du lot de sortie (retourné par `/checkout`).
    - `returned_equipment_ids` : si fourni, seuls ces équipements sont soldés
      (retour partiel — les autres restent ouverts).
    - Si `returned_equipment_ids` est absent ou vide → retour total du lot.
    """
    if not body.batch_id:
        raise HTTPException(status_code=400, detail="batch_id est obligatoire.")

    try:
        if body.returned_equipment_ids:
            # Retour partiel : on filtre par equipment_id ET batch_id
            ids_ph = ", ".join(["?"] * len(body.returned_equipment_ids))
            _run_write(
                f"""
                UPDATE equipment_movements
                SET actual_return_date = CURRENT_TIMESTAMP
                WHERE batch_id = ?
                  AND equipment_id IN ({ids_ph})
                  AND actual_return_date IS NULL
                """,
                [body.batch_id] + body.returned_equipment_ids,
            )
        else:
            # Retour total du lot
            _run_write(
                "UPDATE equipment_movements SET actual_return_date = CURRENT_TIMESTAMP WHERE batch_id = ? AND actual_return_date IS NULL",
                [body.batch_id],
            )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    # Compte les retours effectifs
    try:
        count_df = _run_query(
            "SELECT COUNT(*) AS n FROM equipment_movements WHERE batch_id = ? AND actual_return_date IS NOT NULL",
            [body.batch_id],
        )
        returned = int(count_df.iloc[0]["n"]) if not count_df.empty else 0
    except RuntimeError:
        returned = len(body.returned_equipment_ids or [])

    return CheckinResponse(
        ok=True,
        returned_count=returned,
        message=f"{returned} outil(s) du kit enregistré(s) comme rendu(s).",
    )


# ════════════════════════════════════════════════════════════
# KIOSQUE
# ════════════════════════════════════════════════════════════

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
    """Envoie une commande `SHOW_EQUIPMENT` au kiosque Raspberry Pi 5.

    Embarque les données équipement + prêts actifs dans le fichier JSON partagé
    afin que le kiosque n'ait pas à interroger DuckDB lui-même.
    """
    try:
        eq_df = _run_query(
            """
            SELECT
                e.equipment_id, e.label, e.brand, e.model, e.serial_number,
                e.subtype, e.condition_label, e.location_hint, e.notes,
                e.technical_specs_json
            FROM equipment e
            WHERE e.equipment_id = ?
            """,
            [body.equipment_id],
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(ok=False, error="screen_unavailable", detail=str(e)).model_dump(),
        ) from e

    if eq_df.empty:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(ok=False, error="equipment_not_found", equipment_id=body.equipment_id).model_dump(),
        )

    # Toutes les photos (triées : overview > nameplate > autres)
    try:
        media_df = _run_query(
            """
            SELECT final_drive_file_id, image_role
            FROM equipment_media
            WHERE equipment_id = ?
            ORDER BY
                CASE image_role
                    WHEN 'overview'  THEN 1
                    WHEN 'nameplate' THEN 2
                    ELSE 3
                END
            """,
            [body.equipment_id],
        )
        # pd.notna() évite bool(pd.NA) → TypeError sur les VARCHAR nuls DuckDB
        media_files = [
            {"file_id": _s(r["final_drive_file_id"]), "role": _s(r["image_role"])}
            for _, r in media_df.iterrows()
            if pd.notna(r["final_drive_file_id"]) and _s(r["final_drive_file_id"]) not in ("", "None", "nan")
        ]
    except Exception:
        media_files = []

    # Prêts actifs
    try:
        loans_df = _run_query(
            """
            SELECT borrower_name, movement_type, out_date, expected_return_date
            FROM equipment_movements
            WHERE equipment_id = ? AND actual_return_date IS NULL
            ORDER BY out_date DESC LIMIT 5
            """,
            [body.equipment_id],
        )
        loans = loans_df.to_dict("records")
    except Exception:
        loans = []

    row = eq_df.iloc[0]
    specs_raw = _s(row.get("technical_specs_json"))
    try:
        specs = json.loads(specs_raw) if specs_raw else {}
    except Exception:
        specs = {}

    eq_data = {
        "equipment_id":    _s(row.get("equipment_id")),
        "label":           _s(row.get("label")),
        "brand":           _s(row.get("brand")),
        "model":           _s(row.get("model")),
        "serial_number":   _s(row.get("serial_number")),
        "subtype":         _s(row.get("subtype")),
        "condition_label": _s(row.get("condition_label")),
        "location_hint":   _s(row.get("location_hint")),
        "notes":           _s(row.get("notes")),
        "technical_specs": specs,
        "media_files":     media_files,
        "loans":           loans,
    }

    # Écrit dans le fichier JSON (transport sans DuckDB pour le kiosque)
    try:
        _write_kiosk_state("SHOW_EQUIPMENT", eq_data)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(ok=False, error="screen_unavailable", detail=str(e)).model_dump(),
        ) from e

    # Audit trail DuckDB (optionnel — n'alimente plus le kiosque)
    command_id = str(uuid.uuid4())
    try:
        _run_write(
            """
            INSERT INTO ui_commands
                (command_id, target_ui, command_type, payload, created_at, executed)
            VALUES
                (?, 'atelier_pi_5', 'SHOW_EQUIPMENT', ?, CURRENT_TIMESTAMP, TRUE)
            """,
            [command_id, body.equipment_id],
        )
    except RuntimeError:
        pass  # L'audit est best-effort ; le kiosque a déjà reçu la commande

    return DisplayResponse(
        ok=True,
        equipment_id=body.equipment_id,
        display_status="sent",
        screen="atelier-main",
        message=(
            f"Commande d'affichage transmise à l'écran atelier. "
            f"'{eq_data['label']}' sera visible dans ≤ 2 s."
        ),
    )


@app.post(
    "/api/display/show-kit",
    response_model=DisplayGenericResponse,
    summary="Affiche le détail d'un kit sur l'écran kiosque",
    tags=["Kiosque"],
    responses={
        404: {"model": ErrorResponse, "description": "Kit inconnu"},
        503: {"model": ErrorResponse, "description": "Écran indisponible"},
    },
)
def display_kit(
    body: DisplayKitRequest,
    _: None = Security(_require_token),
) -> DisplayGenericResponse:
    """Envoie une commande `SHOW_KIT` au kiosque : fiche kit + liste des outils."""
    try:
        kit_df = _run_query(
            "SELECT kit_id, name, description FROM kits WHERE kit_id = ? LIMIT 1",
            [body.kit_id],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503,
            detail=ErrorResponse(ok=False, error="screen_unavailable", detail=str(e)).model_dump()) from e

    if kit_df.empty:
        raise HTTPException(status_code=404,
            detail=ErrorResponse(ok=False, error="kit_not_found", kit_id=body.kit_id).model_dump())

    try:
        items_df = _run_query(
            """
            SELECT e.equipment_id, e.label, e.brand, e.model,
                   e.condition_label, e.location_hint,
                   mf.final_drive_file_id AS main_file_id
            FROM kit_items ki
            JOIN equipment e ON e.equipment_id = ki.equipment_id
            LEFT JOIN (
                SELECT equipment_id, final_drive_file_id
                FROM (
                    SELECT equipment_id, final_drive_file_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY equipment_id
                               ORDER BY CASE image_role
                                   WHEN 'overview'  THEN 1
                                   WHEN 'nameplate' THEN 2
                                   ELSE 3
                               END
                           ) AS rn
                    FROM equipment_media
                ) t WHERE rn = 1
            ) mf ON mf.equipment_id = e.equipment_id
            WHERE ki.kit_id = ?
            ORDER BY e.label
            """,
            [body.kit_id],
        )
        items = items_df.to_dict("records")
    except RuntimeError:
        items = []

    kit_row = kit_df.iloc[0]
    kit_data = {
        "kit_id":      _s(kit_row.get("kit_id")),
        "name":        _s(kit_row.get("name")),
        "description": _s(kit_row.get("description")),
        "item_count":  len(items),
        "items":       items,
    }

    try:
        _write_kiosk_state("SHOW_KIT", kit_data)
    except Exception as e:
        raise HTTPException(status_code=503,
            detail=ErrorResponse(ok=False, error="screen_unavailable", detail=str(e)).model_dump()) from e

    return DisplayGenericResponse(
        ok=True,
        command_type="SHOW_KIT",
        display_status="sent",
        screen="atelier-main",
        message=f"Kit '{kit_data['name']}' ({len(items)} outil(s)) affiché sur l'écran atelier.",
    )


@app.post(
    "/api/display/show-movements",
    response_model=DisplayGenericResponse,
    summary="Affiche le tableau des sorties en cours sur l'écran kiosque",
    tags=["Kiosque"],
    responses={503: {"model": ErrorResponse}},
)
def display_movements(
    _: None = Security(_require_token),
) -> DisplayGenericResponse:
    """Envoie une commande `SHOW_MOVEMENTS_ACTIVE` au kiosque."""
    try:
        mv_df = _run_query(
            """
            SELECT
                em.movement_id, em.equipment_id, e.label,
                em.borrower_name, em.movement_type,
                em.out_date, em.expected_return_date,
                em.batch_id, em.kit_id,
                k.name AS kit_name,
                (em.expected_return_date IS NOT NULL
                 AND em.expected_return_date < CURRENT_DATE) AS is_late,
                mf.final_drive_file_id AS main_file_id
            FROM equipment_movements em
            JOIN equipment e ON e.equipment_id = em.equipment_id
            LEFT JOIN kits k ON k.kit_id = em.kit_id
            LEFT JOIN (
                SELECT equipment_id, final_drive_file_id
                FROM (
                    SELECT equipment_id, final_drive_file_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY equipment_id
                               ORDER BY CASE image_role
                                   WHEN 'overview'  THEN 1
                                   WHEN 'nameplate' THEN 2
                                   ELSE 3
                               END
                           ) AS rn
                    FROM equipment_media
                ) t WHERE rn = 1
            ) mf ON mf.equipment_id = em.equipment_id
            WHERE em.actual_return_date IS NULL
            ORDER BY em.out_date DESC
            """
        )
        items = mv_df.to_dict("records")
    except RuntimeError as e:
        raise HTTPException(status_code=503,
            detail=ErrorResponse(ok=False, error="screen_unavailable", detail=str(e)).model_dump()) from e

    mv_data = {"count": len(items), "items": items}

    try:
        _write_kiosk_state("SHOW_MOVEMENTS_ACTIVE", mv_data)
    except Exception as e:
        raise HTTPException(status_code=503,
            detail=ErrorResponse(ok=False, error="screen_unavailable", detail=str(e)).model_dump()) from e

    late = sum(1 for i in items if i.get("is_late"))
    return DisplayGenericResponse(
        ok=True,
        command_type="SHOW_MOVEMENTS_ACTIVE",
        display_status="sent",
        screen="atelier-main",
        message=f"{len(items)} sortie(s) en cours affichée(s) ({late} en retard).",
    )


@app.post(
    "/api/display/show-confirmation",
    response_model=DisplayGenericResponse,
    summary="Affiche un écran de confirmation d'action sur le kiosque",
    tags=["Kiosque"],
    responses={503: {"model": ErrorResponse}},
)
def display_confirmation(
    body: DisplayConfirmationRequest,
    _: None = Security(_require_token),
) -> DisplayGenericResponse:
    """Affiche un écran de confirmation après une action OpenClaw (sortie, retour, création kit…)."""
    data = {
        "title":    body.title,
        "subtitle": body.subtitle,
        "details":  body.details,
        "batch_id": body.batch_id,
        "color":    body.color,
    }
    try:
        _write_kiosk_state("SHOW_CONFIRMATION", data)
    except Exception as e:
        raise HTTPException(status_code=503,
            detail=ErrorResponse(ok=False, error="screen_unavailable", detail=str(e)).model_dump()) from e

    return DisplayGenericResponse(
        ok=True,
        command_type="SHOW_CONFIRMATION",
        display_status="sent",
        screen="atelier-main",
        message=f"Confirmation '{body.title}' affichée sur l'écran atelier.",
    )


@app.post(
    "/api/display/clear",
    response_model=DisplayGenericResponse,
    summary="Repasse le kiosque en mode veille",
    tags=["Kiosque"],
    responses={503: {"model": ErrorResponse}},
)
def display_clear(
    _: None = Security(_require_token),
) -> DisplayGenericResponse:
    """Efface l'affichage et repasse le kiosque sur l'écran de veille SIGA."""
    try:
        _write_kiosk_state("CLEAR_SCREEN", {})
    except Exception as e:
        raise HTTPException(status_code=503,
            detail=ErrorResponse(ok=False, error="screen_unavailable", detail=str(e)).model_dump()) from e

    return DisplayGenericResponse(
        ok=True,
        command_type="CLEAR_SCREEN",
        display_status="sent",
        screen="atelier-main",
        message="Kiosque repassé en mode veille.",
    )


# ════════════════════════════════════════════════════════════
# KITS — Création & Administration
# ════════════════════════════════════════════════════════════

@app.post(
    "/api/kits",
    response_model=KitMutationResponse,
    status_code=201,
    summary="Crée un nouveau kit",
    tags=["Kits"],
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def create_kit(
    body: KitCreateRequest,
    _: None = Security(_require_token),
) -> KitMutationResponse:
    """
    Crée un kit (caisse à outils / panier chantier).

    - `name` : nom du kit (obligatoire).
    - `description` : description libre — optionnel.
    - `equipment_ids` : liste d'équipements à intégrer immédiatement — optionnel.
      Le kit peut aussi être peuplé ultérieurement via `POST /api/kits/{id}/items`.
    """
    if not body.name or not body.name.strip():
        raise HTTPException(status_code=400, detail="Le nom du kit est obligatoire.")

    kit_id = str(uuid.uuid4())

    try:
        _run_write(
            "INSERT INTO kits (kit_id, name, description) VALUES (?, ?, ?)",
            [kit_id, body.name.strip(), body.description.strip() if body.description else None],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    # Peupler si equipment_ids fournis
    if body.equipment_ids:
        # Vérifie existence
        ids_ph = ", ".join(["?"] * len(body.equipment_ids))
        try:
            exists_df = _run_query(
                f"SELECT equipment_id FROM equipment WHERE equipment_id IN ({ids_ph})",
                body.equipment_ids,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

        found_ids = set(exists_df["equipment_id"].tolist())
        missing = [eid for eid in body.equipment_ids if eid not in found_ids]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Kit créé (id={kit_id}) mais équipements introuvables : {missing}",
            )

        stmts = [
            ("INSERT OR IGNORE INTO kit_items (kit_id, equipment_id) VALUES (?, ?)", [kit_id, eid])
            for eid in body.equipment_ids
        ]
        try:
            _run_write_many(stmts)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    count = len(body.equipment_ids) if body.equipment_ids else 0
    return KitMutationResponse(
        ok=True,
        kit_id=kit_id,
        message=f"Kit '{body.name.strip()}' créé" + (f" avec {count} équipement(s)." if count else "."),
    )


@app.put(
    "/api/kits/{kit_id}",
    response_model=KitMutationResponse,
    summary="Renomme ou modifie la description d'un kit",
    tags=["Kits"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def update_kit(
    kit_id: str,
    body: KitUpdateRequest,
    _: None = Security(_require_token),
) -> KitMutationResponse:
    """Met à jour le `name` et/ou la `description` d'un kit existant."""
    if not body.name and body.description is None:
        raise HTTPException(status_code=400, detail="Fournir name ou description à modifier.")

    try:
        kit_df = _run_query("SELECT kit_id FROM kits WHERE kit_id = ? LIMIT 1", [kit_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    if kit_df.empty:
        raise HTTPException(status_code=404, detail=ErrorResponse(ok=False, error="kit_not_found").model_dump())

    if body.name:
        try:
            _run_write("UPDATE kits SET name = ? WHERE kit_id = ?", [body.name.strip(), kit_id])
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    if body.description is not None:
        try:
            _run_write(
                "UPDATE kits SET description = ? WHERE kit_id = ?",
                [body.description.strip() or None, kit_id],
            )
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    return KitMutationResponse(ok=True, kit_id=kit_id, message="Kit mis à jour.")


@app.delete(
    "/api/kits/{kit_id}",
    response_model=KitMutationResponse,
    summary="Supprime un kit et son contenu",
    tags=["Kits"],
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def delete_kit(
    kit_id: str,
    _: None = Security(_require_token),
) -> KitMutationResponse:
    """
    Supprime le kit et toutes ses entrées dans `kit_items`.
    Les mouvements passés référençant ce kit ne sont pas supprimés (historique conservé).
    """
    try:
        kit_df = _run_query("SELECT name FROM kits WHERE kit_id = ? LIMIT 1", [kit_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    if kit_df.empty:
        raise HTTPException(status_code=404, detail=ErrorResponse(ok=False, error="kit_not_found").model_dump())

    name = str(kit_df.iloc[0]["name"] or kit_id)

    try:
        _run_write_many([
            ("DELETE FROM kit_items WHERE kit_id = ?", [kit_id]),
            ("DELETE FROM kits WHERE kit_id = ?", [kit_id]),
        ])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return KitMutationResponse(ok=True, kit_id=kit_id, message=f"Kit '{name}' supprimé.")


@app.post(
    "/api/kits/{kit_id}/items",
    response_model=KitMutationResponse,
    status_code=201,
    summary="Ajoute des équipements à un kit existant",
    tags=["Kits"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def add_kit_items(
    kit_id: str,
    body: KitAddItemsRequest,
    _: None = Security(_require_token),
) -> KitMutationResponse:
    """
    Ajoute un ou plusieurs équipements à un kit.
    Les doublons sont ignorés silencieusement (INSERT OR IGNORE).
    """
    if not body.equipment_ids:
        raise HTTPException(status_code=400, detail="equipment_ids ne peut pas être vide.")

    try:
        kit_df = _run_query("SELECT name FROM kits WHERE kit_id = ? LIMIT 1", [kit_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    if kit_df.empty:
        raise HTTPException(status_code=404, detail=ErrorResponse(ok=False, error="kit_not_found").model_dump())

    # Vérifie existence des équipements
    ids_ph = ", ".join(["?"] * len(body.equipment_ids))
    try:
        exists_df = _run_query(
            f"SELECT equipment_id FROM equipment WHERE equipment_id IN ({ids_ph})",
            body.equipment_ids,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    found_ids = set(exists_df["equipment_id"].tolist())
    missing = [eid for eid in body.equipment_ids if eid not in found_ids]
    if missing:
        raise HTTPException(status_code=400, detail=f"Équipements introuvables : {missing}")

    stmts = [
        ("INSERT OR IGNORE INTO kit_items (kit_id, equipment_id) VALUES (?, ?)", [kit_id, eid])
        for eid in body.equipment_ids
    ]
    try:
        _run_write_many(stmts)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return KitMutationResponse(
        ok=True,
        kit_id=kit_id,
        message=f"{len(body.equipment_ids)} équipement(s) ajouté(s) au kit.",
    )


@app.delete(
    "/api/kits/{kit_id}/items",
    response_model=KitMutationResponse,
    summary="Retire des équipements d'un kit",
    tags=["Kits"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def remove_kit_items(
    kit_id: str,
    body: KitAddItemsRequest,
    _: None = Security(_require_token),
) -> KitMutationResponse:
    """Retire un ou plusieurs équipements d'un kit (sans les supprimer du catalogue)."""
    if not body.equipment_ids:
        raise HTTPException(status_code=400, detail="equipment_ids ne peut pas être vide.")

    try:
        kit_df = _run_query("SELECT name FROM kits WHERE kit_id = ? LIMIT 1", [kit_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    if kit_df.empty:
        raise HTTPException(status_code=404, detail=ErrorResponse(ok=False, error="kit_not_found").model_dump())

    ids_ph = ", ".join(["?"] * len(body.equipment_ids))
    try:
        _run_write(
            f"DELETE FROM kit_items WHERE kit_id = ? AND equipment_id IN ({ids_ph})",
            [kit_id] + body.equipment_ids,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return KitMutationResponse(
        ok=True,
        kit_id=kit_id,
        message=f"{len(body.equipment_ids)} équipement(s) retiré(s) du kit.",
    )


@app.put(
    "/api/kits/{kit_id}/content",
    response_model=KitMutationResponse,
    summary="Remplace entièrement le contenu d'un kit",
    tags=["Kits"],
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def set_kit_content(
    kit_id: str,
    body: KitSetContentRequest,
    _: None = Security(_require_token),
) -> KitMutationResponse:
    """
    Remplace atomiquement la liste complète des équipements d'un kit.
    Équivalent à : vider le kit puis ajouter `equipment_ids`.
    Passer une liste vide pour vider le kit sans le supprimer.
    """
    try:
        kit_df = _run_query("SELECT name FROM kits WHERE kit_id = ? LIMIT 1", [kit_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    if kit_df.empty:
        raise HTTPException(status_code=404, detail=ErrorResponse(ok=False, error="kit_not_found").model_dump())

    if body.equipment_ids:
        ids_ph = ", ".join(["?"] * len(body.equipment_ids))
        try:
            exists_df = _run_query(
                f"SELECT equipment_id FROM equipment WHERE equipment_id IN ({ids_ph})",
                body.equipment_ids,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

        found_ids = set(exists_df["equipment_id"].tolist())
        missing = [eid for eid in body.equipment_ids if eid not in found_ids]
        if missing:
            raise HTTPException(status_code=400, detail=f"Équipements introuvables : {missing}")

    stmts: list[tuple] = [("DELETE FROM kit_items WHERE kit_id = ?", [kit_id])]
    stmts += [
        ("INSERT INTO kit_items (kit_id, equipment_id) VALUES (?, ?)", [kit_id, eid])
        for eid in body.equipment_ids
    ]
    try:
        _run_write_many(stmts)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    name = str(kit_df.iloc[0]["name"] or kit_id)
    return KitMutationResponse(
        ok=True,
        kit_id=kit_id,
        message=f"Contenu du kit '{name}' mis à jour : {len(body.equipment_ids)} équipement(s).",
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
