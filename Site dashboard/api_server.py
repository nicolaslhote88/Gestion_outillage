"""
SIGA — API HTTP Métier
Interface JSON simple pour le skill OpenClaw (chat principal + WhatsApp).

Opérations :
  GET  /api/equipment/search?q=<texte>       → recherche fuzzy avec score
  GET  /api/equipment/{id}/status            → disponibilité d'un équipement
  GET  /api/equipment/{id}/family            → accessoires + consommables liés (v4.0)
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
  POST /api/reservations                     → créer une réservation (vérifie conflits)
  GET  /api/reservations/conflicts           → vérifier la disponibilité d'un équipement
  GET  /api/reservations/active              → lister les réservations à venir
  DELETE /api/reservations/{res_id}          → annuler une réservation

  — v4.0 Relationnel ——————————————————————————————————————————
  GET  /api/accessories                      → catalogue accessoires
  POST /api/accessories                      → créer un accessoire
  GET  /api/consumables                      → catalogue consommables
  POST /api/consumables                      → créer un consommable
  POST /api/links/compatibility              → lier un accessoire à un équipement
  DELETE /api/links/compatibility/{link_id}  → supprimer une liaison accessoire
  POST /api/links/consumables                → lier un consommable à un équipement
  DELETE /api/links/consumables/{link_id}    → supprimer une liaison consommable

  — v4.1 Migration & gouvernance ——————————————————————————————
  GET  /api/equipment                        → listing avec filtres + pagination
  GET  /api/equipment/{id}                   → fiche complète (photos, gouvernance)
  PATCH /api/equipment/{id}                  → mise à jour partielle
  POST /api/equipment/{id}/archive           → soft-delete
  POST /api/equipment/{id}/unarchive         → restaurer
  GET  /api/accessories/{id}                 → fiche complète accessoire
  PATCH /api/accessories/{id}               → mise à jour accessoire
  DELETE /api/accessories/{id}              → archiver (hard=true pour supprimer)
  GET  /api/consumables/{id}                → fiche complète consommable
  PATCH /api/consumables/{id}              → mise à jour consommable
  DELETE /api/consumables/{id}             → archiver (hard=true pour supprimer)
  GET  /api/equipment/{id}/photos           → liste des photos
  PUT  /api/equipment/{id}/photos           → remplacer les photos
  POST /api/equipment/{id}/photos/attach    → attacher une photo orpheline (v4.3)
  GET  /api/accessories/{id}/photos         → liste des photos accessoire (v4.4)
  PUT  /api/accessories/{id}/photos         → remplacer les photos accessoire (v4.4)
  POST /api/accessories/{id}/photos/attach  → attacher une photo orpheline à un accessoire (v4.4)
  GET  /api/consumables/{id}/photos         → liste des photos consommable (v4.4)
  PUT  /api/consumables/{id}/photos         → remplacer les photos consommable (v4.4)
  POST /api/consumables/{id}/photos/attach  → attacher une photo orpheline à un consommable (v4.4)
  GET  /api/drive/orphan-photos             → photos Drive non référencées dans toutes les tables (v4.4)
  GET  /api/drive/folder/{folder_id}        → lister un dossier Drive
  GET  /api/drive/files/{file_id}           → métadonnées fichier Drive
  POST /api/drive/folder                    → créer un dossier Drive
  POST /api/drive/files/{file_id}/move      → déplacer un fichier Drive
  POST /api/drive/files/{file_id}/copy      → copier un fichier Drive
  PATCH /api/drive/files/{file_id}/rename   → renommer un fichier Drive
  POST /api/media/reassign                  → réassigner une photo entre toutes entités (v4.4)
  POST /api/admin/migrations/reclassify     → migration atomique (dry_run supporté)
  GET  /api/admin/migrations/logs           → journal d'audit
  GET  /api/admin/migrations/legacy-mappings/{id} → traçabilité legacy → canonical
  GET  /api/admin/export                    → export bulk inventaire
  GET  /api/admin/duplicates                → détection doublons

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
import threading
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

# Drive (optionnel — dégradé gracieux si credentials absents)
try:
    from google.oauth2 import service_account as _sa
    from googleapiclient.discovery import build as _gdrive_build
    from googleapiclient.errors import HttpError as _DriveHttpError
    _DRIVE_AVAILABLE = True
except ImportError:
    _DRIVE_AVAILABLE = False

# Chemin service account partagé avec app.py
GDRIVE_SA_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/secrets/service_account.json")

# ─── Configuration ────────────────────────────────────────────
DB_PATH    = "/files/duckdb/siga_v1.duckdb"
API_TOKEN  = os.getenv("SIGA_API_TOKEN", "siga-secret-token-change-me")
API_PORT   = int(os.getenv("SIGA_API_PORT", "8001"))

SCORE_MIN  = 0.25
MAX_RESULTS = 20

VALID_MOVEMENT_TYPES = {"LOAN", "RENTAL", "MAINTENANCE"}

# Verrou global pour sérialiser les écritures DuckDB dans le même processus.
# DuckDB n'accepte qu'une seule connexion d'écriture à la fois ; sans ce verrou
# des requêtes concurrentes peuvent provoquer des conflits de handle de fichier
# ou des erreurs CHECKPOINT.
_db_write_lock = threading.Lock()

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


def _rows(sql: str, params=None) -> List[dict]:
    """Retourne une liste de dicts Python natifs (pas de numpy/pandas types, NULL → None).

    DuckDB récent retourne pd.NA pour les VARCHAR nuls (pas None ni numpy.nan).
    pd.isna() est le seul test fiable qui couvre : pd.NA, pd.NaT, numpy.nan,
    numpy.float64(nan), float('nan') — sans lever d'exception.
    """
    df = _run_query(sql, params)
    if df.empty:
        return []

    def _native(v: Any) -> Any:
        if v is None:
            return None
        # pd.isna couvre : pd.NA, pd.NaT, numpy.nan, float('nan'), numpy.float64(nan)
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        # numpy scalar (int64, float64, bool_, …) → type Python natif
        if hasattr(v, "item"):
            return v.item()
        # Timestamp pandas/datetime → ISO 8601 string
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return v

    return [{k: _native(val) for k, val in row.items()} for row in df.to_dict(orient="records")]


def _run_write(sql: str, params=None, _retries: int = 5) -> None:
    delay = 2
    last_err = None
    for attempt in range(_retries):
        try:
            with _db_write_lock:
                with duckdb.connect(DB_PATH, read_only=False) as conn:
                    if params:
                        conn.execute(sql, params)
                    else:
                        conn.execute(sql)
                    conn.commit()
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
            with _db_write_lock:
                with duckdb.connect(DB_PATH, read_only=False) as conn:
                    for sql, params in statements:
                        conn.execute(sql, params)
                    conn.commit()
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


# ─── Drive helpers (v4.1) ────────────────────────────────────

def _gdrive_service(write: bool = False):
    """Retourne un service Google Drive, ou None si credentials absents."""
    if not _DRIVE_AVAILABLE:
        return None
    sa_path = Path(GDRIVE_SA_PATH)
    if not sa_path.exists():
        return None
    try:
        scopes = (["https://www.googleapis.com/auth/drive"] if write
                  else ["https://www.googleapis.com/auth/drive.readonly"])
        creds = _sa.Credentials.from_service_account_file(str(sa_path), scopes=scopes)
        return _gdrive_build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception:
        return None


def _drive_list_folder(folder_id: str) -> List[Dict]:
    """Liste les fichiers d'un dossier Drive. Retourne [] si Drive indisponible."""
    svc = _gdrive_service(write=False)
    if svc is None:
        return []
    try:
        results = []
        page_token = None
        while True:
            kwargs: Dict[str, Any] = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken, files(id, name, mimeType, size, createdTime, modifiedTime, parents, webViewLink)",
                "pageSize": 200,
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            resp = svc.files().list(**kwargs).execute()
            results.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return results
    except Exception:
        return []


def _map_drive_file(f: Dict) -> "DriveFileInfo":
    """Convertit un dict brut de l'API Drive en DriveFileInfo Pydantic."""
    size_raw = f.get("size")
    return DriveFileInfo(
        file_id=f.get("id", ""),
        name=f.get("name", ""),
        mime_type=f.get("mimeType", ""),
        size=int(size_raw) if size_raw is not None else None,
        created_time=f.get("createdTime"),
        modified_time=f.get("modifiedTime"),
        parents=f.get("parents") or [],
        web_view_link=f.get("webViewLink"),
        is_folder=f.get("mimeType") == "application/vnd.google-apps.folder",
    )


def _drive_get_file_meta(file_id: str) -> Optional[Dict]:
    """Récupère les métadonnées d'un fichier Drive."""
    svc = _gdrive_service(write=False)
    if svc is None:
        return None
    try:
        return svc.files().get(
            fileId=file_id,
            fields="id, name, mimeType, size, createdTime, modifiedTime, parents, webViewLink",
            supportsAllDrives=True,
        ).execute()
    except Exception:
        return None


def _drive_create_folder(name: str, parent_id: Optional[str] = None) -> Optional[str]:
    """Crée un dossier Drive. Retourne le folder_id créé, ou None."""
    svc = _gdrive_service(write=True)
    if svc is None:
        return None
    try:
        meta: Dict[str, Any] = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            meta["parents"] = [parent_id]
        f = svc.files().create(body=meta, fields="id", supportsAllDrives=True).execute()
        return f.get("id")
    except Exception:
        return None


def _drive_move_file(file_id: str, new_parent_id: str) -> bool:
    """Déplace un fichier Drive vers un nouveau dossier."""
    svc = _gdrive_service(write=True)
    if svc is None:
        return False
    try:
        meta = _gdrive_service(write=False).files().get(
            fileId=file_id, fields="parents", supportsAllDrives=True
        ).execute()
        old_parents = ",".join(meta.get("parents", []))
        svc.files().update(
            fileId=file_id,
            addParents=new_parent_id,
            removeParents=old_parents,
            fields="id, parents",
            supportsAllDrives=True,
        ).execute()
        return True
    except Exception:
        return False


def _drive_copy_file(file_id: str, new_parent_id: str, new_name: Optional[str] = None) -> Optional[str]:
    """Copie un fichier Drive. Retourne le nouvel ID, ou None."""
    svc = _gdrive_service(write=True)
    if svc is None:
        return None
    try:
        body: Dict[str, Any] = {}
        if new_parent_id:
            body["parents"] = [new_parent_id]
        if new_name:
            body["name"] = new_name
        result = svc.files().copy(
            fileId=file_id, body=body, fields="id", supportsAllDrives=True
        ).execute()
        return result.get("id")
    except Exception:
        return None


def _drive_rename_file(file_id: str, new_name: str) -> bool:
    """Renomme un fichier Drive."""
    svc = _gdrive_service(write=True)
    if svc is None:
        return False
    try:
        svc.files().update(
            fileId=file_id, body={"name": new_name},
            fields="id, name", supportsAllDrives=True
        ).execute()
        return True
    except Exception:
        return False


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


# — Réservations ──────────────────────────────────────────────

class ReservationCreateRequest(BaseModel):
    equipment_id: str
    user_name: str
    start_date: str   # ISO 8601 : "2025-06-30T08:00" ou "2025-06-30"
    end_date: str


class ReservationCreateResponse(BaseModel):
    ok: bool
    res_id: str
    message: str


class ConflictItem(BaseModel):
    type: str          # "reservation" | "maintenance"
    user_name: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    movement_type: Optional[str] = None


class ConflictCheckResponse(BaseModel):
    equipment_id: str
    has_conflict: bool
    conflicts: List[ConflictItem]


class ReservationItem(BaseModel):
    res_id: str
    equipment_id: str
    equipment_label: str
    user_name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: str


class ActiveReservationsResponse(BaseModel):
    count: int
    reservations: List[ReservationItem]


# — v4.0 Accessoires & Consommables ──────────────────────────

class AccessoryCreateRequest(BaseModel):
    label: str
    brand: Optional[str] = None
    model: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    stock_qty: int = 0
    location_hint: Optional[str] = None
    notes: Optional[str] = None


class ConsumableCreateRequest(BaseModel):
    label: str
    brand: Optional[str] = None
    reference: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    unit: str = "pcs"
    stock_qty: float = 0
    stock_min_alert: float = 0
    location_hint: Optional[str] = None
    notes: Optional[str] = None


class AccessoryItem(BaseModel):
    accessory_id: str
    label: str
    brand: Optional[str] = None
    model: Optional[str] = None
    category: Optional[str] = None
    stock_qty: int = 0
    location_hint: Optional[str] = None
    link_id: Optional[str] = None
    note: Optional[str] = None


class ConsumableItem(BaseModel):
    consumable_id: str
    label: str
    brand: Optional[str] = None
    reference: Optional[str] = None
    category: Optional[str] = None
    unit: str = "pcs"
    stock_qty: float = 0
    stock_min_alert: float = 0
    location_hint: Optional[str] = None
    link_id: Optional[str] = None
    qty_per_use: float = 1
    note: Optional[str] = None
    stock_ok: bool = True   # True si stock_qty >= stock_min_alert


class EquipmentFamilyResponse(BaseModel):
    equipment_id: str
    label: str
    accessories: List[AccessoryItem]
    consumables: List[ConsumableItem]


class LinkCompatibilityRequest(BaseModel):
    equipment_id: str
    accessory_id: str
    note: Optional[str] = None


class LinkConsumableRequest(BaseModel):
    equipment_id: str
    consumable_id: str
    qty_per_use: float = 1
    note: Optional[str] = None


class LinkCreateResponse(BaseModel):
    ok: bool
    link_id: str
    message: str


class AccessoryListResponse(BaseModel):
    count: int
    accessories: List[AccessoryItem]


class ConsumableListResponse(BaseModel):
    count: int
    consumables: List[ConsumableItem]


# — v4.1 Equipment listing & full detail ──────────────────────

class EquipmentPhotoRef(BaseModel):
    photo_id: str                    # equipment_media primary key (media_id)
    file_id: Optional[str] = None   # final_drive_file_id
    folder_id: Optional[str] = None # final_drive_folder_id
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    role: Optional[str] = None       # overview | nameplate | detail | …
    sort_order: int = 0
    is_primary: bool = False
    web_view_link: Optional[str] = None


class EquipmentFullResponse(BaseModel):
    equipment_id: str
    label: str
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    category: Optional[str] = None
    subtype: Optional[str] = None
    condition_label: Optional[str] = None
    location_hint: Optional[str] = None
    ownership_mode: Optional[str] = None
    purchase_price: Optional[float] = None
    purchase_currency: Optional[str] = None
    notes: Optional[str] = None
    technical_specs: Optional[Dict] = None
    business_context: Optional[Dict] = None
    ai_metadata: Optional[Dict] = None
    status: Optional[str] = None
    review_required: bool = False
    review_reasons: Optional[List] = None
    archived: bool = False
    migration_status: str = "NOT_REVIEWED"
    legacy_source_id: Optional[str] = None
    migrated_at: Optional[str] = None
    migrated_by: Optional[str] = None
    classification_confidence: Optional[float] = None
    photo_count: int = 0
    photos: List[EquipmentPhotoRef] = []
    drive_folder_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class EquipmentSummary(BaseModel):
    equipment_id: str
    label: str
    brand: Optional[str] = None
    model: Optional[str] = None
    category: Optional[str] = None
    subtype: Optional[str] = None
    condition_label: Optional[str] = None
    location_hint: Optional[str] = None
    status: Optional[str] = None
    archived: bool = False
    migration_status: str = "NOT_REVIEWED"
    photo_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class EquipmentListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    has_more: bool
    items: List[EquipmentSummary]


class EquipmentUpdateRequest(BaseModel):
    label: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    category: Optional[str] = None
    subtype: Optional[str] = None
    condition_label: Optional[str] = None
    location_hint: Optional[str] = None
    ownership_mode: Optional[str] = None
    purchase_price: Optional[float] = None
    purchase_currency: Optional[str] = None
    notes: Optional[str] = None
    technical_specs_json: Optional[str] = None   # JSON string
    business_context_json: Optional[str] = None  # JSON string
    ai_metadata: Optional[str] = None            # JSON string
    status: Optional[str] = None
    review_required: Optional[bool] = None
    archived: Optional[bool] = None
    migration_status: Optional[str] = None
    legacy_source_id: Optional[str] = None
    migrated_by: Optional[str] = None
    classification_confidence: Optional[float] = None


class ArchiveResponse(BaseModel):
    ok: bool
    equipment_id: str
    message: str


# — v4.1 Photo management ─────────────────────────────────────

class PhotoRefInput(BaseModel):
    file_id: str
    folder_id: Optional[str] = None
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    role: str = "overview"
    sort_order: int = 0
    is_primary: bool = False


class PhotoUpdateRequest(BaseModel):
    photos: List[PhotoRefInput]   # remplace entièrement la liste des photos


class PhotoAttachRequest(BaseModel):
    """Attache une photo Drive orpheline à une entité SIGA (v4.3)."""
    file_id: str                           # Drive file_id (obligatoire)
    role: str = "overview"                 # overview | nameplate | detail
    folder_id: Optional[str] = None        # Drive folder_id parent (optionnel)
    filename: Optional[str] = None         # nom du fichier (optionnel)
    mime_type: Optional[str] = None        # type MIME (optionnel)
    is_primary: bool = False
    attached_by: str = "openclaw"          # traçabilité : qui a attaché la photo


class PhotoListResponse(BaseModel):
    equipment_id: str
    count: int
    photos: List[EquipmentPhotoRef]


# — v4.1 Accessories / Consumables CRUD complet ───────────────

class AccessoryFullResponse(BaseModel):
    accessory_id: str
    label: str
    brand: Optional[str] = None
    model: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    stock_qty: int = 0
    location_hint: Optional[str] = None
    drive_file_id: Optional[str] = None   # conservé pour rétrocompatibilité
    photos: List[EquipmentPhotoRef] = []   # multi-photos via accessory_media (v4.4)
    photo_count: int = 0
    notes: Optional[str] = None
    ai_metadata: Optional[Dict] = None
    archived: bool = False
    migration_status: str = "NOT_REVIEWED"
    legacy_source_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AccessoryUpdateRequest(BaseModel):
    label: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    stock_qty: Optional[int] = None
    location_hint: Optional[str] = None
    drive_file_id: Optional[str] = None
    notes: Optional[str] = None
    ai_metadata: Optional[str] = None   # JSON string
    archived: Optional[bool] = None
    migration_status: Optional[str] = None
    legacy_source_id: Optional[str] = None


class ConsumableFullResponse(BaseModel):
    consumable_id: str
    label: str
    brand: Optional[str] = None
    reference: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    unit: str = "pcs"
    stock_qty: float = 0
    stock_min_alert: float = 0
    location_hint: Optional[str] = None
    drive_file_id: Optional[str] = None   # conservé pour rétrocompatibilité
    photos: List[EquipmentPhotoRef] = []   # multi-photos via consumable_media (v4.4)
    photo_count: int = 0
    notes: Optional[str] = None
    ai_metadata: Optional[Dict] = None
    archived: bool = False
    migration_status: str = "NOT_REVIEWED"
    legacy_source_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ConsumableUpdateRequest(BaseModel):
    label: Optional[str] = None
    brand: Optional[str] = None
    reference: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    unit: Optional[str] = None
    stock_qty: Optional[float] = None
    stock_min_alert: Optional[float] = None
    location_hint: Optional[str] = None
    drive_file_id: Optional[str] = None
    notes: Optional[str] = None
    ai_metadata: Optional[str] = None   # JSON string
    archived: Optional[bool] = None
    migration_status: Optional[str] = None
    legacy_source_id: Optional[str] = None


# — v4.1 Drive bridge ─────────────────────────────────────────

class DriveFileInfo(BaseModel):
    file_id: str
    name: str
    mime_type: str
    size: Optional[int] = None
    created_time: Optional[str] = None
    modified_time: Optional[str] = None
    parents: List[str] = []
    web_view_link: Optional[str] = None
    is_folder: bool = False


class DriveFolderContents(BaseModel):
    folder_id: str
    count: int
    files: List[DriveFileInfo]


class DriveCreateFolderRequest(BaseModel):
    name: str
    parent_id: Optional[str] = None


class DriveCreateFolderResponse(BaseModel):
    ok: bool
    folder_id: Optional[str]
    message: str


class DriveMoveRequest(BaseModel):
    new_parent_id: str


class DriveCopyRequest(BaseModel):
    new_parent_id: str
    new_name: Optional[str] = None


class DriveFileOpResponse(BaseModel):
    ok: bool
    file_id: Optional[str] = None
    message: str


class DriveRenameRequest(BaseModel):
    new_name: str


# — v4.1 Réassignation photo ──────────────────────────────────

class MediaReassignRequest(BaseModel):
    source_entity_type: str = "equipment"
    source_entity_id: str
    target_entity_type: str          # "equipment" | "accessory" | "consumable"
    target_entity_id: str
    photo_id: str                    # media_id dans equipment_media
    mode: str = "move"               # "move" | "copy"


class MediaReassignResponse(BaseModel):
    ok: bool
    photo_id: str
    new_file_id: Optional[str] = None
    message: str


# — v4.1 Migration orchestrée ─────────────────────────────────

class ReclassifyTargetEquipment(BaseModel):
    label: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    category: Optional[str] = None
    subtype: Optional[str] = None
    notes: Optional[str] = None
    ai_metadata: Optional[str] = None


class ReclassifyNewAccessory(BaseModel):
    label: str
    brand: Optional[str] = None
    model: Optional[str] = None
    category: Optional[str] = None
    stock_qty: int = 0
    notes: Optional[str] = None
    drive_file_id: Optional[str] = None   # photo à associer


class ReclassifyNewConsumable(BaseModel):
    label: str
    brand: Optional[str] = None
    reference: Optional[str] = None
    category: Optional[str] = None
    unit: str = "pcs"
    stock_qty: float = 0
    stock_min_alert: float = 0
    notes: Optional[str] = None
    drive_file_id: Optional[str] = None


class LinkTarget(BaseModel):
    accessory_id: str
    note: Optional[str] = None


class ConsumableLinkTarget(BaseModel):
    consumable_id: str
    qty_per_use: float = 1
    note: Optional[str] = None


class PhotoMapping(BaseModel):
    photo_id: str                    # media_id de la source
    target_entity_type: str          # "equipment" | "accessory" | "consumable"
    target_index: int = 0            # index dans la liste d'accessories/consumables créés
    mode: str = "keep"               # "keep" | "move" | "copy"


class ReclassifyRequest(BaseModel):
    source_equipment_id: str
    action: str = "split_record"     # "split_record" | "reclassify_as_accessory" | "reclassify_as_consumable"
    target_equipment: Optional[ReclassifyTargetEquipment] = None
    new_accessories: List[ReclassifyNewAccessory] = []
    new_consumables: List[ReclassifyNewConsumable] = []
    link_existing_accessories: List[LinkTarget] = []
    link_existing_consumables: List[ConsumableLinkTarget] = []
    photo_mapping: List[PhotoMapping] = []
    source_record_policy: str = "archive"   # "archive" | "keep"
    operator: str = "openclaw"
    notes: Optional[str] = None


class ReclassifyPlan(BaseModel):
    source_equipment_id: str
    action: str
    equipment_updates: Dict = {}
    accessories_to_create: int = 0
    consumables_to_create: int = 0
    links_to_create: int = 0
    photos_to_process: int = 0
    source_will_be_archived: bool = True


class ReclassifyResult(BaseModel):
    ok: bool
    dry_run: bool
    log_id: Optional[str] = None
    plan: Optional[ReclassifyPlan] = None
    created_accessory_ids: List[str] = []
    created_consumable_ids: List[str] = []
    links_created: int = 0
    source_archived: bool = False
    legacy_mapping_id: Optional[str] = None
    message: str


# — v4.1 Migration logs & Admin ───────────────────────────────

class MigrationLogEntry(BaseModel):
    log_id: str
    operation: str
    operator: str
    source_entity_type: Optional[str] = None
    source_entity_id: Optional[str] = None
    target_entities: Optional[Dict] = None
    details: Optional[Dict] = None
    dry_run: bool = False
    status: str = "COMPLETED"
    error_message: Optional[str] = None
    created_at: Optional[str] = None


class MigrationLogsResponse(BaseModel):
    count: int
    logs: List[MigrationLogEntry]


class DuplicateGroup(BaseModel):
    entity_type: str
    ids: List[str]
    labels: List[str]
    similarity_score: float
    reason: str


class DuplicatesResponse(BaseModel):
    count: int
    groups: List[DuplicateGroup]


class LegacyMappingResponse(BaseModel):
    mapping_id: str
    legacy_equipment_id: str
    canonical_equipment_id: Optional[str] = None
    derived_accessory_ids: List[str] = []
    derived_consumable_ids: List[str] = []
    notes: Optional[str] = None
    created_at: Optional[str] = None


# — v4.2 Catalogue unifié ─────────────────────────────────────

class CatalogItem(BaseModel):
    entity_type: str                        # "equipment" | "accessory" | "consumable"
    entity_id: str
    label: str
    brand: Optional[str] = None
    model: Optional[str] = None             # equipment / accessory
    reference: Optional[str] = None         # consumable
    category: Optional[str] = None
    subtype: Optional[str] = None           # equipment
    condition_label: Optional[str] = None   # equipment
    location_hint: Optional[str] = None
    availability_status: Optional[str] = None   # equipment
    stock_qty: Optional[float] = None           # accessory / consumable
    stock_min_alert: Optional[float] = None     # consumable
    stock_ok: Optional[bool] = None             # consumable
    archived: bool = False
    migration_status: str = "NOT_REVIEWED"
    primary_photo_file_id: Optional[str] = None
    photo_count: int = 0


class CatalogListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    has_more: bool
    entity_types: List[str]   # types présents dans les résultats
    items: List[CatalogItem]


# ─── App FastAPI ──────────────────────────────────────────────

app = FastAPI(
    title="SIGA API",
    description="API HTTP métier pour le pilotage de SIGA par OpenClaw.",
    version="4.0.0",
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
                e.subtype, e.category, e.condition_label, e.location_hint, e.notes,
                e.ownership_mode, e.purchase_price, e.purchase_currency,
                e.technical_specs_json, e.business_context_json
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

    biz_raw = _s(row.get("business_context_json"))
    try:
        biz = json.loads(biz_raw) if biz_raw else {}
    except Exception:
        biz = {}

    purchase_price = row.get("purchase_price")
    try:
        purchase_price_str = (
            f"{float(purchase_price):.2f} {_s(row.get('purchase_currency')) or '€'}"
            if purchase_price is not None and pd.notna(purchase_price)
            else ""
        )
    except Exception:
        purchase_price_str = ""

    # Prochaine réservation (si existante)
    try:
        next_res_df = _run_query(
            """
            SELECT user_name, start_date, end_date
            FROM reservations
            WHERE equipment_id = ?
              AND status IN ('PENDING', 'ACTIVE')
              AND end_date >= CURRENT_TIMESTAMP
            ORDER BY start_date ASC
            LIMIT 1
            """,
            [body.equipment_id],
        )
        if not next_res_df.empty:
            nr = next_res_df.iloc[0]
            next_reservation = {
                "user_name":  _s(nr.get("user_name")),
                "start_date": str(nr["start_date"]) if pd.notna(nr.get("start_date")) else None,
                "end_date":   str(nr["end_date"])   if pd.notna(nr.get("end_date"))   else None,
            }
        else:
            next_reservation = None
    except Exception:
        next_reservation = None

    # Accessoires liés (base relationnelle v4.0)
    try:
        acc_rel_df = _run_query(
            """
            SELECT a.accessory_id, a.label, a.brand, a.model,
                   a.stock_qty, a.location_hint, lc.link_id, lc.note
            FROM links_compatibility lc
            JOIN accessories a ON a.accessory_id = lc.accessory_id
            WHERE lc.equipment_id = ?
            ORDER BY a.label
            """,
            [body.equipment_id],
        )
        accessories_rel = acc_rel_df.to_dict("records") if not acc_rel_df.empty else []
    except Exception:
        accessories_rel = []

    # Consommables liés (base relationnelle v4.0)
    try:
        con_rel_df = _run_query(
            """
            SELECT c.consumable_id, c.label, c.brand, c.reference,
                   c.unit, c.stock_qty, c.stock_min_alert, c.location_hint,
                   lcons.link_id, lcons.qty_per_use, lcons.note
            FROM links_consumables lcons
            JOIN consumables c ON c.consumable_id = lcons.consumable_id
            WHERE lcons.equipment_id = ?
            ORDER BY c.label
            """,
            [body.equipment_id],
        )
        consumables_rel = [
            {**r, "stock_ok": float(r.get("stock_qty") or 0) > float(r.get("stock_min_alert") or 0)}
            for r in (con_rel_df.to_dict("records") if not con_rel_df.empty else [])
        ]
    except Exception:
        consumables_rel = []

    eq_data = {
        "equipment_id":    _s(row.get("equipment_id")),
        "label":           _s(row.get("label")),
        "brand":           _s(row.get("brand")),
        "model":           _s(row.get("model")),
        "serial_number":   _s(row.get("serial_number")),
        "subtype":         _s(row.get("subtype")),
        "category":        _s(row.get("category")),
        "condition_label": _s(row.get("condition_label")),
        "location_hint":   _s(row.get("location_hint")),
        "ownership_mode":  _s(row.get("ownership_mode")),
        "purchase_price":  purchase_price_str,
        "notes":           _s(row.get("notes")),
        "technical_specs": specs,
        # Données ingestion (business_context_json) — rétrocompatibilité
        "accessories":     biz.get("accessories") or biz.get("accessoires") or [],
        "consumables":     biz.get("consumables") or biz.get("consommables") or [],
        "associated_items": biz.get("associated_items") or biz.get("elements_associes") or [],
        # Liaisons relationnelles v4.0
        "accessories_rel": accessories_rel,
        "consumables_rel": consumables_rel,
        "media_files":     media_files,
        "loans":           loans,
        "next_reservation": next_reservation,
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


# ════════════════════════════════════════════════════════════
# RÉSERVATIONS & PLANNING
# ════════════════════════════════════════════════════════════

def _parse_iso_date(date_str: str) -> datetime:
    """Parse une date ISO 8601 (avec ou sans heure). Lève ValueError si invalide."""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Format de date invalide : {date_str!r} (attendu YYYY-MM-DD ou YYYY-MM-DDTHH:MM)")


def _check_reservation_conflicts(equipment_id: str, start: datetime, end: datetime) -> List[ConflictItem]:
    """Vérifie les chevauchements de réservations et la maintenance active."""
    conflicts: List[ConflictItem] = []

    # Chevauchement avec réservations existantes
    try:
        res_df = _run_query(
            """
            SELECT user_name, start_date, end_date
            FROM reservations
            WHERE equipment_id = ?
              AND status IN ('PENDING', 'ACTIVE')
              AND start_date < ?
              AND end_date   > ?
            """,
            [equipment_id, end, start],
        )
        for _, r in res_df.iterrows():
            conflicts.append(ConflictItem(
                type="reservation",
                user_name=_s(r.get("user_name")),
                start_date=str(r["start_date"]) if pd.notna(r.get("start_date")) else None,
                end_date=str(r["end_date"])   if pd.notna(r.get("end_date"))   else None,
            ))
    except RuntimeError:
        pass

    # Maintenance active (movement_type = MAINTENANCE, non retourné)
    try:
        maint_df = _run_query(
            """
            SELECT borrower_name, out_date, expected_return_date
            FROM equipment_movements
            WHERE equipment_id   = ?
              AND movement_type  = 'MAINTENANCE'
              AND actual_return_date IS NULL
            LIMIT 1
            """,
            [equipment_id],
        )
        for _, r in maint_df.iterrows():
            conflicts.append(ConflictItem(
                type="maintenance",
                user_name=_s(r.get("borrower_name")),
                start_date=str(r["out_date"]) if pd.notna(r.get("out_date")) else None,
                end_date=str(r["expected_return_date"]) if pd.notna(r.get("expected_return_date")) else None,
                movement_type="MAINTENANCE",
            ))
    except RuntimeError:
        pass

    return conflicts


@app.post(
    "/api/reservations",
    response_model=ReservationCreateResponse,
    summary="Créer une réservation",
    tags=["Réservations"],
    responses={
        400: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def create_reservation(
    body: ReservationCreateRequest,
    _: None = Security(_require_token),
) -> ReservationCreateResponse:
    """Vérifie les conflits, puis insère la réservation si la plage est libre."""
    # Validation des dates
    try:
        start_dt = _parse_iso_date(body.start_date)
        end_dt   = _parse_iso_date(body.end_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="end_date doit être postérieure à start_date.")

    # Vérification que l'équipement existe
    try:
        eq_df = _run_query(
            "SELECT label FROM equipment WHERE equipment_id = ? LIMIT 1",
            [body.equipment_id],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if eq_df.empty:
        raise HTTPException(status_code=404, detail=f"Équipement inconnu : {body.equipment_id}")

    # Vérification des conflits
    conflicts = _check_reservation_conflicts(body.equipment_id, start_dt, end_dt)
    if conflicts:
        first = conflicts[0]
        if first.type == "maintenance":
            msg = f"Impossible : l'équipement est en maintenance"
            if first.user_name:
                msg += f" (responsable : {first.user_name})"
        else:
            msg = f"Impossible : déjà réservé par {first.user_name or '?'} de {first.start_date} à {first.end_date}"
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(ok=False, error="conflict", detail=msg).model_dump(),
        )

    res_id = str(uuid.uuid4())
    try:
        _run_write(
            """
            INSERT INTO reservations
                (res_id, equipment_id, user_name, start_date, end_date, status, created_at)
            VALUES
                (?, ?, ?, ?, ?, 'PENDING', CURRENT_TIMESTAMP)
            """,
            [res_id, body.equipment_id, body.user_name, start_dt, end_dt],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    eq_label = _s(eq_df.iloc[0]["label"])
    return ReservationCreateResponse(
        ok=True,
        res_id=res_id,
        message=f"C'est noté, '{eq_label}' est bloqué pour {body.user_name} du {body.start_date} au {body.end_date} !",
    )


@app.get(
    "/api/reservations/conflicts",
    response_model=ConflictCheckResponse,
    summary="Vérifier les conflits avant réservation",
    tags=["Réservations"],
)
def check_conflicts(
    equipment_id: str,
    start: str,
    end: str,
    _: None = Security(_require_token),
) -> ConflictCheckResponse:
    """Permet à OpenClaw de vérifier la faisabilité avant de proposer la réservation à l'utilisateur."""
    try:
        start_dt = _parse_iso_date(start)
        end_dt   = _parse_iso_date(end)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    conflicts = _check_reservation_conflicts(equipment_id, start_dt, end_dt)
    return ConflictCheckResponse(
        equipment_id=equipment_id,
        has_conflict=len(conflicts) > 0,
        conflicts=conflicts,
    )


@app.get(
    "/api/reservations/active",
    response_model=ActiveReservationsResponse,
    summary="Lister les réservations à venir",
    tags=["Réservations"],
)
def list_active_reservations(
    equipment_id: Optional[str] = None,
    user_name: Optional[str] = None,
    _: None = Security(_require_token),
) -> ActiveReservationsResponse:
    """Liste toutes les réservations en cours ou à venir, avec filtres optionnels."""
    conditions = ["r.status IN ('PENDING', 'ACTIVE')", "r.end_date >= CURRENT_TIMESTAMP"]
    params: List[Any] = []

    if equipment_id:
        conditions.append("r.equipment_id = ?")
        params.append(equipment_id)
    if user_name:
        conditions.append("LOWER(r.user_name) = LOWER(?)")
        params.append(user_name)

    where_clause = " AND ".join(conditions)
    try:
        df = _run_query(
            f"""
            SELECT
                r.res_id, r.equipment_id, r.user_name,
                r.start_date, r.end_date, r.status,
                e.label AS equipment_label
            FROM reservations r
            JOIN equipment e ON e.equipment_id = r.equipment_id
            WHERE {where_clause}
            ORDER BY r.start_date ASC
            """,
            params or None,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    items: List[ReservationItem] = []
    for _, row in df.iterrows():
        items.append(ReservationItem(
            res_id=_s(row["res_id"]),
            equipment_id=_s(row["equipment_id"]),
            equipment_label=_s(row.get("equipment_label")),
            user_name=_s(row["user_name"]),
            start_date=str(row["start_date"]) if pd.notna(row.get("start_date")) else None,
            end_date=str(row["end_date"])   if pd.notna(row.get("end_date"))   else None,
            status=_s(row["status"]),
        ))
    return ActiveReservationsResponse(count=len(items), reservations=items)


@app.delete(
    "/api/reservations/{res_id}",
    summary="Annuler une réservation",
    tags=["Réservations"],
    responses={404: {"model": ErrorResponse}},
)
def cancel_reservation(
    res_id: str,
    _: None = Security(_require_token),
) -> dict:
    """Annule une réservation en passant son statut à CANCELLED."""
    try:
        res_df = _run_query(
            "SELECT res_id, status FROM reservations WHERE res_id = ? LIMIT 1",
            [res_id],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    if res_df.empty:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(ok=False, error="reservation_not_found").model_dump(),
        )
    current_status = _s(res_df.iloc[0]["status"])
    if current_status == "CANCELLED":
        return {"ok": True, "res_id": res_id, "message": "Réservation déjà annulée."}

    try:
        _run_write(
            "UPDATE reservations SET status = 'CANCELLED' WHERE res_id = ?",
            [res_id],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {"ok": True, "res_id": res_id, "message": "Réservation annulée avec succès."}


# ════════════════════════════════════════════════════════════
# v4.0 — ACCESSOIRES & CONSOMMABLES
# ════════════════════════════════════════════════════════════

@app.get(
    "/api/accessories",
    response_model=AccessoryListResponse,
    summary="Catalogue complet des accessoires",
    tags=["Relationnel v4"],
)
def list_accessories(
    q: Optional[str] = None,
    archived: Optional[bool] = None,
    _: None = Security(_require_token),
) -> AccessoryListResponse:
    """Retourne tous les accessoires (batteries, adaptateurs, lames…), avec filtre texte optionnel.
    Par défaut (archived non spécifié), seuls les accessoires non-archivés sont retournés.
    Passer archived=true pour n'obtenir que les archivés, archived=false pour forcer les actifs."""
    conditions: List[str] = []
    params: List[Any] = []

    # Filtre archived : par défaut on exclut les archivés
    if archived is None:
        conditions.append("(archived IS NULL OR archived = FALSE)")
    else:
        conditions.append("archived = ?")
        params.append(archived)

    if q and q.strip():
        like = f"%{q.strip()}%"
        conditions.append("(LOWER(label) LIKE LOWER(?) OR LOWER(brand) LIKE LOWER(?) OR LOWER(model) LIKE LOWER(?))")
        params += [like, like, like]

    where = " WHERE " + " AND ".join(conditions)
    sql = f"SELECT accessory_id, label, brand, model, category, stock_qty, location_hint FROM accessories{where} ORDER BY label"
    try:
        df = _run_query(sql, params)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    items = [
        AccessoryItem(
            accessory_id=_s(row["accessory_id"]),
            label=_s(row["label"]),
            brand=_s(row.get("brand")) or None,
            model=_s(row.get("model")) or None,
            category=_s(row.get("category")) or None,
            stock_qty=int(row.get("stock_qty") or 0),
            location_hint=_s(row.get("location_hint")) or None,
        )
        for _, row in df.iterrows()
    ]
    return AccessoryListResponse(count=len(items), accessories=items)


@app.post(
    "/api/accessories",
    response_model=LinkCreateResponse,
    status_code=201,
    summary="Créer un accessoire dans le catalogue",
    tags=["Relationnel v4"],
    responses={503: {"model": ErrorResponse}},
)
def create_accessory(
    body: AccessoryCreateRequest,
    force_create: bool = False,
    _: None = Security(_require_token),
) -> LinkCreateResponse:
    """Ajoute un accessoire (batterie, adaptateur, chargeur…) au catalogue SIGA.
    Si un accessoire non-archivé avec le même label+brand+model existe déjà,
    retourne l'existant (pas de doublon). Utilisez force_create=true pour forcer."""
    if not body.label or not body.label.strip():
        raise HTTPException(status_code=400, detail="label est obligatoire.")

    # Déduplication : éviter les doublons à label+brand+model identiques
    if not force_create:
        existing = _rows("""
            SELECT accessory_id FROM accessories
            WHERE LOWER(TRIM(label)) = LOWER(?)
              AND COALESCE(LOWER(TRIM(brand)), '') = COALESCE(LOWER(?), '')
              AND COALESCE(LOWER(TRIM(model)), '') = COALESCE(LOWER(?), '')
              AND (archived IS NULL OR archived = FALSE)
            LIMIT 1
        """, [body.label.strip(), body.brand or "", body.model or ""])
        if existing:
            return LinkCreateResponse(
                ok=True,
                link_id=existing[0]["accessory_id"],
                message=f"Accessoire existant retourné (doublon évité) : {body.label.strip()}",
            )

    acc_id = str(uuid.uuid4())
    try:
        _run_write(
            """
            INSERT INTO accessories
                (accessory_id, label, brand, model, category, description,
                 stock_qty, location_hint, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [acc_id, body.label.strip(), body.brand, body.model,
             body.category, body.description, body.stock_qty,
             body.location_hint, body.notes],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return LinkCreateResponse(ok=True, link_id=acc_id, message=f"Accessoire '{body.label.strip()}' créé (id={acc_id}).")


@app.get(
    "/api/consumables",
    response_model=ConsumableListResponse,
    summary="Catalogue complet des consommables",
    tags=["Relationnel v4"],
)
def list_consumables(
    q: Optional[str] = None,
    low_stock: bool = False,
    archived: Optional[bool] = None,
    _: None = Security(_require_token),
) -> ConsumableListResponse:
    """Retourne tous les consommables (forets, abrasifs, visserie…).
    Par défaut (archived non spécifié), seuls les consommables non-archivés sont retournés.
    Paramètre `low_stock=true` pour n'afficher que ceux en alerte de stock."""
    conditions: List[str] = []
    params: List[Any] = []

    # Filtre archived : par défaut on exclut les archivés
    if archived is None:
        conditions.append("(archived IS NULL OR archived = FALSE)")
    else:
        conditions.append("archived = ?")
        params.append(archived)

    if q and q.strip():
        like = f"%{q.strip()}%"
        conditions.append("(LOWER(label) LIKE LOWER(?) OR LOWER(brand) LIKE LOWER(?) OR LOWER(reference) LIKE LOWER(?))")
        params += [like, like, like]
    if low_stock:
        conditions.append("stock_qty <= stock_min_alert")

    sql = """
        SELECT consumable_id, label, brand, reference, category, unit,
               stock_qty, stock_min_alert, location_hint
        FROM consumables
        WHERE """ + " AND ".join(conditions) + " ORDER BY label"
    try:
        df = _run_query(sql, params or None)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    items = [
        ConsumableItem(
            consumable_id=_s(row["consumable_id"]),
            label=_s(row["label"]),
            brand=_s(row.get("brand")) or None,
            reference=_s(row.get("reference")) or None,
            category=_s(row.get("category")) or None,
            unit=_s(row.get("unit")) or "pcs",
            stock_qty=float(row.get("stock_qty") or 0),
            stock_min_alert=float(row.get("stock_min_alert") or 0),
            location_hint=_s(row.get("location_hint")) or None,
            stock_ok=float(row.get("stock_qty") or 0) > float(row.get("stock_min_alert") or 0),
        )
        for _, row in df.iterrows()
    ]
    return ConsumableListResponse(count=len(items), consumables=items)


@app.post(
    "/api/consumables",
    response_model=LinkCreateResponse,
    status_code=201,
    summary="Créer un consommable dans le catalogue",
    tags=["Relationnel v4"],
    responses={503: {"model": ErrorResponse}},
)
def create_consumable(
    body: ConsumableCreateRequest,
    force_create: bool = False,
    _: None = Security(_require_token),
) -> LinkCreateResponse:
    """Ajoute un consommable (foret, lame de scie, abrasif…) au catalogue SIGA.
    Si un consommable non-archivé avec le même label+brand+reference existe déjà,
    retourne l'existant (pas de doublon). Utilisez force_create=true pour forcer."""
    if not body.label or not body.label.strip():
        raise HTTPException(status_code=400, detail="label est obligatoire.")

    # Déduplication : éviter les doublons à label+brand+reference identiques
    if not force_create:
        existing = _rows("""
            SELECT consumable_id FROM consumables
            WHERE LOWER(TRIM(label)) = LOWER(?)
              AND COALESCE(LOWER(TRIM(brand)), '') = COALESCE(LOWER(?), '')
              AND COALESCE(LOWER(TRIM(reference)), '') = COALESCE(LOWER(?), '')
              AND (archived IS NULL OR archived = FALSE)
            LIMIT 1
        """, [body.label.strip(), body.brand or "", body.reference or ""])
        if existing:
            return LinkCreateResponse(
                ok=True,
                link_id=existing[0]["consumable_id"],
                message=f"Consommable existant retourné (doublon évité) : {body.label.strip()}",
            )

    con_id = str(uuid.uuid4())
    try:
        _run_write(
            """
            INSERT INTO consumables
                (consumable_id, label, brand, reference, category, description,
                 unit, stock_qty, stock_min_alert, location_hint, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [con_id, body.label.strip(), body.brand, body.reference,
             body.category, body.description, body.unit,
             body.stock_qty, body.stock_min_alert, body.location_hint, body.notes],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return LinkCreateResponse(ok=True, link_id=con_id, message=f"Consommable '{body.label.strip()}' créé (id={con_id}).")


# ════════════════════════════════════════════════════════════
# v4.0 — LIAISONS (LINKS)
# ════════════════════════════════════════════════════════════

@app.get(
    "/api/equipment/{equipment_id}/family",
    response_model=EquipmentFamilyResponse,
    summary="Famille d'un équipement (accessoires + consommables liés)",
    tags=["Relationnel v4"],
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def get_equipment_family(
    equipment_id: str,
    _: None = Security(_require_token),
) -> EquipmentFamilyResponse:
    """
    Retourne l'écosystème complet d'un équipement :
    - Accessoires compatibles (batteries, adaptateurs…)
    - Consommables à prévoir (forets, abrasifs…) avec état du stock
    """
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
    label = _s(eq_df.iloc[0]["label"])

    try:
        acc_df = _run_query(
            """
            SELECT a.accessory_id, a.label, a.brand, a.model, a.category,
                   a.stock_qty, a.location_hint,
                   lc.link_id, lc.note
            FROM links_compatibility lc
            JOIN accessories a ON a.accessory_id = lc.accessory_id
            WHERE lc.equipment_id = ?
            ORDER BY a.label
            """,
            [equipment_id],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    accessories = [
        AccessoryItem(
            accessory_id=_s(row["accessory_id"]),
            label=_s(row["label"]),
            brand=_s(row.get("brand")) or None,
            model=_s(row.get("model")) or None,
            category=_s(row.get("category")) or None,
            stock_qty=int(row.get("stock_qty") or 0),
            location_hint=_s(row.get("location_hint")) or None,
            link_id=_s(row.get("link_id")) or None,
            note=_s(row.get("note")) or None,
        )
        for _, row in acc_df.iterrows()
    ]

    try:
        con_df = _run_query(
            """
            SELECT c.consumable_id, c.label, c.brand, c.reference, c.category,
                   c.unit, c.stock_qty, c.stock_min_alert, c.location_hint,
                   lcons.link_id, lcons.qty_per_use, lcons.note
            FROM links_consumables lcons
            JOIN consumables c ON c.consumable_id = lcons.consumable_id
            WHERE lcons.equipment_id = ?
            ORDER BY c.label
            """,
            [equipment_id],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    consumables = [
        ConsumableItem(
            consumable_id=_s(row["consumable_id"]),
            label=_s(row["label"]),
            brand=_s(row.get("brand")) or None,
            reference=_s(row.get("reference")) or None,
            category=_s(row.get("category")) or None,
            unit=_s(row.get("unit")) or "pcs",
            stock_qty=float(row.get("stock_qty") or 0),
            stock_min_alert=float(row.get("stock_min_alert") or 0),
            location_hint=_s(row.get("location_hint")) or None,
            link_id=_s(row.get("link_id")) or None,
            qty_per_use=float(row.get("qty_per_use") or 1),
            note=_s(row.get("note")) or None,
            stock_ok=float(row.get("stock_qty") or 0) > float(row.get("stock_min_alert") or 0),
        )
        for _, row in con_df.iterrows()
    ]

    return EquipmentFamilyResponse(
        equipment_id=equipment_id,
        label=label,
        accessories=accessories,
        consumables=consumables,
    )


@app.post(
    "/api/links/compatibility",
    response_model=LinkCreateResponse,
    status_code=201,
    summary="Lier un accessoire à un équipement",
    tags=["Relationnel v4"],
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def link_compatibility(
    body: LinkCompatibilityRequest,
    _: None = Security(_require_token),
) -> LinkCreateResponse:
    """
    Crée une liaison Many-to-Many entre un équipement et un accessoire.

    Exemple : lier une batterie 18V à un perforateur ET à une visseuse (sans duplication).
    La liaison est ignorée silencieusement si elle existe déjà (UNIQUE constraint).
    """
    try:
        eq_df = _run_query("SELECT label FROM equipment WHERE equipment_id = ? LIMIT 1", [body.equipment_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if eq_df.empty:
        raise HTTPException(status_code=404, detail=f"Équipement introuvable : {body.equipment_id}")

    try:
        acc_df = _run_query("SELECT label FROM accessories WHERE accessory_id = ? LIMIT 1", [body.accessory_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if acc_df.empty:
        raise HTTPException(status_code=404, detail=f"Accessoire introuvable : {body.accessory_id}")

    link_id = str(uuid.uuid4())
    try:
        _run_write(
            """
            INSERT INTO links_compatibility (link_id, equipment_id, accessory_id, note)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (equipment_id, accessory_id) DO NOTHING
            """,
            [link_id, body.equipment_id, body.accessory_id, body.note],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    eq_label  = _s(eq_df.iloc[0]["label"])
    acc_label = _s(acc_df.iloc[0]["label"])
    return LinkCreateResponse(
        ok=True,
        link_id=link_id,
        message=f"'{acc_label}' lié à '{eq_label}' comme accessoire compatible.",
    )


@app.delete(
    "/api/links/compatibility/{link_id}",
    summary="Supprimer une liaison accessoire ↔ équipement",
    tags=["Relationnel v4"],
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def delete_link_compatibility(
    link_id: str,
    _: None = Security(_require_token),
) -> dict:
    """Supprime une liaison de compatibilité. L'accessoire et l'équipement ne sont pas supprimés."""
    try:
        df = _run_query("SELECT link_id FROM links_compatibility WHERE link_id = ? LIMIT 1", [link_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if df.empty:
        raise HTTPException(status_code=404, detail=ErrorResponse(ok=False, error="link_not_found").model_dump())
    try:
        _run_write("DELETE FROM links_compatibility WHERE link_id = ?", [link_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"ok": True, "link_id": link_id, "message": "Liaison supprimée."}


@app.post(
    "/api/links/consumables",
    response_model=LinkCreateResponse,
    status_code=201,
    summary="Lier un consommable à un équipement",
    tags=["Relationnel v4"],
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def link_consumable(
    body: LinkConsumableRequest,
    _: None = Security(_require_token),
) -> LinkCreateResponse:
    """
    Crée une liaison Many-to-Many entre un équipement et un consommable.

    Exemple : lier des forets SDS-Plus à un perforateur.
    `qty_per_use` indique la quantité typiquement utilisée par session.
    """
    try:
        eq_df = _run_query("SELECT label FROM equipment WHERE equipment_id = ? LIMIT 1", [body.equipment_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if eq_df.empty:
        raise HTTPException(status_code=404, detail=f"Équipement introuvable : {body.equipment_id}")

    try:
        con_df = _run_query("SELECT label FROM consumables WHERE consumable_id = ? LIMIT 1", [body.consumable_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if con_df.empty:
        raise HTTPException(status_code=404, detail=f"Consommable introuvable : {body.consumable_id}")

    link_id = str(uuid.uuid4())
    try:
        _run_write(
            """
            INSERT INTO links_consumables (link_id, equipment_id, consumable_id, qty_per_use, note)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (equipment_id, consumable_id) DO NOTHING
            """,
            [link_id, body.equipment_id, body.consumable_id, body.qty_per_use, body.note],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    eq_label  = _s(eq_df.iloc[0]["label"])
    con_label = _s(con_df.iloc[0]["label"])
    return LinkCreateResponse(
        ok=True,
        link_id=link_id,
        message=f"'{con_label}' lié à '{eq_label}' comme consommable nécessaire.",
    )


@app.delete(
    "/api/links/consumables/{link_id}",
    summary="Supprimer une liaison consommable ↔ équipement",
    tags=["Relationnel v4"],
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def delete_link_consumable(
    link_id: str,
    _: None = Security(_require_token),
) -> dict:
    """Supprime une liaison de consommable. Le consommable et l'équipement ne sont pas supprimés."""
    try:
        df = _run_query("SELECT link_id FROM links_consumables WHERE link_id = ? LIMIT 1", [link_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if df.empty:
        raise HTTPException(status_code=404, detail=ErrorResponse(ok=False, error="link_not_found").model_dump())
    try:
        _run_write("DELETE FROM links_consumables WHERE link_id = ?", [link_id])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"ok": True, "link_id": link_id, "message": "Liaison consommable supprimée."}


# ─── v4.1 : Listing & détail équipement ──────────────────────

@app.get("/api/equipment", tags=["v4.1 Équipement"], summary="Listing équipements avec filtres")
def list_equipment(
    q: Optional[str] = None,
    category: Optional[str] = None,
    brand: Optional[str] = None,
    status: Optional[str] = None,
    archived: Optional[bool] = None,
    migration_status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    _: None = Security(_require_token),
):
    """Liste tous les équipements avec filtres optionnels et pagination."""
    conditions = []
    params: List[Any] = []

    if q:
        conditions.append("(LOWER(label) LIKE ? OR LOWER(brand) LIKE ? OR LOWER(model) LIKE ?)")
        like = f"%{q.lower()}%"
        params += [like, like, like]
    if category:
        conditions.append("LOWER(category) = ?")
        params.append(category.lower())
    if brand:
        conditions.append("LOWER(brand) = ?")
        params.append(brand.lower())
    if status:
        conditions.append("status = ?")
        params.append(status)
    if archived is not None:
        conditions.append("archived = ?")
        params.append(archived)
    else:
        # Par défaut on masque les archivés
        conditions.append("(archived IS NULL OR archived = FALSE)")
    if migration_status:
        conditions.append("migration_status = ?")
        params.append(migration_status)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * page_size

    count_rows = _rows(f"SELECT COUNT(*) as cnt FROM equipment {where}", params)
    total = int(count_rows[0]["cnt"]) if count_rows else 0

    items = _rows(
        f"SELECT * FROM equipment {where} ORDER BY label LIMIT ? OFFSET ?",
        params + [page_size, offset],
    )
    return EquipmentListResponse(
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + page_size) < total,
        items=[EquipmentSummary(**_coerce_equipment_summary(i)) for i in items],
    )


def _coerce_equipment_summary(row: dict) -> dict:
    """Normalise un dict DB → EquipmentSummary (champs optionnels manquants)."""
    return {
        "equipment_id": row.get("equipment_id", ""),
        "label": row.get("label", ""),
        "brand": row.get("brand"),
        "model": row.get("model"),
        "category": row.get("category"),
        "subtype": row.get("subtype"),
        "condition_label": row.get("condition_label"),
        "location_hint": row.get("location_hint") or row.get("location"),
        "status": row.get("status"),
        "archived": bool(row.get("archived") or False),
        "migration_status": row.get("migration_status") or "NOT_REVIEWED",
        "photo_count": int(row.get("photo_count") or 0),
        "created_at": str(row["created_at"]) if row.get("created_at") else None,
        "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
    }


@app.get("/api/equipment/{equipment_id}", tags=["v4.1 Équipement"], summary="Fiche complète équipement")
def get_equipment_full(
    equipment_id: str,
    _: None = Security(_require_token),
):
    """Retourne la fiche complète d'un équipement : métadonnées, photos, gouvernance."""
    rows = _rows("SELECT * FROM equipment WHERE equipment_id = ?", [equipment_id])
    if not rows:
        raise HTTPException(status_code=404, detail=f"Équipement {equipment_id} introuvable.")
    row = rows[0]

    # Photos
    photos: List[EquipmentPhotoRef] = []
    try:
        photo_rows = _rows(
            "SELECT * FROM equipment_media WHERE equipment_id = ? ORDER BY image_index",
            [equipment_id],
        )
        for p in photo_rows:
            photos.append(EquipmentPhotoRef(
                photo_id=p["media_id"],
                file_id=p.get("final_drive_file_id"),
                folder_id=p.get("final_drive_folder_id"),
                filename=p.get("filename"),
                mime_type=p.get("mime_type"),
                role=p.get("image_role", "overview"),
                sort_order=int(p.get("image_index") or 0),
                is_primary=bool(p.get("is_primary") or False),
                web_view_link=p.get("web_view_link"),
            ))
    except Exception:
        pass  # table may not have all columns yet

    # ai_metadata
    ai_meta = None
    if row.get("ai_metadata"):
        try:
            ai_meta = json.loads(row["ai_metadata"])
        except Exception:
            ai_meta = {"raw": row["ai_metadata"]}

    # technical_specs / business_context — JSON stocké en VARCHAR
    tech_specs = None
    if row.get("technical_specs"):
        try:
            tech_specs = json.loads(row["technical_specs"])
        except Exception:
            tech_specs = {"raw": row["technical_specs"]}

    biz_ctx = None
    if row.get("business_context"):
        try:
            biz_ctx = json.loads(row["business_context"])
        except Exception:
            biz_ctx = {"raw": row["business_context"]}

    review_reasons = None
    if row.get("review_reasons"):
        try:
            review_reasons = json.loads(row["review_reasons"])
        except Exception:
            review_reasons = [row["review_reasons"]]

    return EquipmentFullResponse(
        equipment_id=row["equipment_id"],
        label=row.get("label", ""),
        brand=row.get("brand"),
        model=row.get("model"),
        serial_number=row.get("serial_number"),
        category=row.get("category"),
        subtype=row.get("subtype"),
        condition_label=row.get("condition_label"),
        location_hint=row.get("location_hint") or row.get("location"),
        ownership_mode=row.get("ownership_mode"),
        purchase_price=row.get("purchase_price"),
        purchase_currency=row.get("purchase_currency"),
        status=row.get("status"),
        notes=row.get("notes"),
        technical_specs=tech_specs,
        business_context=biz_ctx,
        ai_metadata=ai_meta,
        review_required=bool(row.get("review_required") or False),
        review_reasons=review_reasons,
        archived=bool(row.get("archived", False)),
        migration_status=row.get("migration_status", "NOT_REVIEWED"),
        legacy_source_id=row.get("legacy_source_id"),
        migrated_at=str(row["migrated_at"]) if row.get("migrated_at") else None,
        migrated_by=row.get("migrated_by"),
        classification_confidence=row.get("classification_confidence"),
        photo_count=len(photos),
        photos=photos,
        drive_folder_id=row.get("final_drive_folder_id") or row.get("drive_folder_id"),
        created_at=str(row["created_at"]) if row.get("created_at") else None,
        updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
    )


@app.patch("/api/equipment/{equipment_id}", tags=["v4.1 Équipement"], summary="Mettre à jour un équipement")
def patch_equipment(
    equipment_id: str,
    body: EquipmentUpdateRequest,
    _: None = Security(_require_token),
):
    """Met à jour les champs fournis (PATCH sémantique — seuls les champs non-null sont modifiés)."""
    if not _rows("SELECT equipment_id FROM equipment WHERE equipment_id = ?", [equipment_id]):
        raise HTTPException(status_code=404, detail=f"Équipement {equipment_id} introuvable.")

    updates: Dict[str, Any] = {}
    for field, value in body.model_dump(exclude_none=True).items():
        if field == "ai_metadata" and isinstance(value, dict):
            updates[field] = json.dumps(value, ensure_ascii=False)
        else:
            updates[field] = value

    if not updates:
        return {"ok": True, "equipment_id": equipment_id, "message": "Aucun champ à modifier."}

    updates["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [equipment_id]
    try:
        _run_write(f"UPDATE equipment SET {set_clause} WHERE equipment_id = ?", params)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"ok": True, "equipment_id": equipment_id, "updated_fields": list(updates.keys())}


@app.post("/api/equipment/{equipment_id}/archive", tags=["v4.1 Équipement"], summary="Archiver un équipement")
def archive_equipment(
    equipment_id: str,
    _: None = Security(_require_token),
):
    """Soft-delete : marque archived=TRUE et migration_status=ARCHIVED."""
    if not _rows("SELECT equipment_id FROM equipment WHERE equipment_id = ?", [equipment_id]):
        raise HTTPException(status_code=404, detail=f"Équipement {equipment_id} introuvable.")
    try:
        _run_write(
            "UPDATE equipment SET archived = TRUE, migration_status = 'ARCHIVED', updated_at = ? WHERE equipment_id = ?",
            [datetime.utcnow().isoformat(), equipment_id],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return ArchiveResponse(ok=True, equipment_id=equipment_id, archived=True, message="Équipement archivé.")


@app.post("/api/equipment/{equipment_id}/unarchive", tags=["v4.1 Équipement"], summary="Désarchiver un équipement")
def unarchive_equipment(
    equipment_id: str,
    _: None = Security(_require_token),
):
    """Restaure un équipement archivé (archived=FALSE, migration_status=REVIEWED)."""
    if not _rows("SELECT equipment_id FROM equipment WHERE equipment_id = ?", [equipment_id]):
        raise HTTPException(status_code=404, detail=f"Équipement {equipment_id} introuvable.")
    try:
        _run_write(
            "UPDATE equipment SET archived = FALSE, migration_status = 'REVIEWED', updated_at = ? WHERE equipment_id = ?",
            [datetime.utcnow().isoformat(), equipment_id],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return ArchiveResponse(ok=True, equipment_id=equipment_id, archived=False, message="Équipement désarchivé.")


# ─── v4.1 : Accessoires CRUD complet ─────────────────────────

@app.get("/api/accessories/{accessory_id}", tags=["v4.1 Accessoires"], summary="Fiche complète accessoire")
def get_accessory_full(
    accessory_id: str,
    _: None = Security(_require_token),
):
    rows = _rows("SELECT * FROM accessories WHERE accessory_id = ?", [accessory_id])
    if not rows:
        raise HTTPException(status_code=404, detail=f"Accessoire {accessory_id} introuvable.")
    row = rows[0]
    ai_meta = None
    if row.get("ai_metadata"):
        try:
            ai_meta = json.loads(row["ai_metadata"])
        except Exception:
            ai_meta = {"raw": row["ai_metadata"]}
    # Photos multi depuis accessory_media (v4.4)
    photos: List[EquipmentPhotoRef] = []
    try:
        photo_rows = _rows(
            "SELECT * FROM accessory_media WHERE accessory_id = ? ORDER BY image_index",
            [accessory_id],
        )
        for p in photo_rows:
            photos.append(EquipmentPhotoRef(
                photo_id=p["media_id"],
                file_id=p.get("final_drive_file_id"),
                folder_id=p.get("final_drive_folder_id"),
                filename=p.get("filename"),
                mime_type=p.get("mime_type"),
                role=p.get("image_role", "overview"),
                sort_order=int(p.get("image_index") or 0),
                is_primary=bool(p.get("is_primary") or False),
                web_view_link=p.get("web_view_link"),
            ))
    except Exception:
        pass
    return AccessoryFullResponse(
        accessory_id=row["accessory_id"],
        label=row.get("label", ""),
        brand=row.get("brand"),
        model=row.get("model"),
        category=row.get("category"),
        description=row.get("description"),
        stock_qty=int(row.get("stock_qty", 0)),
        location_hint=row.get("location_hint"),
        drive_file_id=row.get("drive_file_id"),
        photos=photos,
        photo_count=len(photos),
        notes=row.get("notes"),
        ai_metadata=ai_meta,
        archived=bool(row.get("archived", False)),
        migration_status=row.get("migration_status", "NOT_REVIEWED"),
        legacy_source_id=row.get("legacy_source_id"),
        created_at=str(row["created_at"]) if row.get("created_at") else None,
        updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
    )


@app.patch("/api/accessories/{accessory_id}", tags=["v4.1 Accessoires"], summary="Mettre à jour un accessoire")
def patch_accessory(
    accessory_id: str,
    body: AccessoryUpdateRequest,
    _: None = Security(_require_token),
):
    if not _rows("SELECT accessory_id FROM accessories WHERE accessory_id = ?", [accessory_id]):
        raise HTTPException(status_code=404, detail=f"Accessoire {accessory_id} introuvable.")

    updates: Dict[str, Any] = {}
    for field, value in body.model_dump(exclude_none=True).items():
        if field == "ai_metadata" and isinstance(value, dict):
            updates[field] = json.dumps(value, ensure_ascii=False)
        else:
            updates[field] = value

    if not updates:
        return {"ok": True, "accessory_id": accessory_id, "message": "Aucun champ à modifier."}

    updates["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [accessory_id]
    try:
        _run_write(f"UPDATE accessories SET {set_clause} WHERE accessory_id = ?", params)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"ok": True, "accessory_id": accessory_id, "updated_fields": list(updates.keys())}


@app.delete("/api/accessories/{accessory_id}", tags=["v4.1 Accessoires"], summary="Archiver/supprimer un accessoire")
def delete_accessory(
    accessory_id: str,
    hard: bool = False,
    _: None = Security(_require_token),
):
    """Par défaut : soft-delete (archived=TRUE). hard=true pour suppression physique."""
    if not _rows("SELECT accessory_id FROM accessories WHERE accessory_id = ?", [accessory_id]):
        raise HTTPException(status_code=404, detail=f"Accessoire {accessory_id} introuvable.")
    try:
        if hard:
            _run_write_many([
                ("DELETE FROM links_compatibility WHERE accessory_id = ?", [accessory_id]),
                ("DELETE FROM accessories WHERE accessory_id = ?", [accessory_id]),
            ])
            return {"ok": True, "accessory_id": accessory_id, "message": "Accessoire supprimé définitivement."}
        else:
            _run_write(
                "UPDATE accessories SET archived = TRUE, updated_at = ? WHERE accessory_id = ?",
                [datetime.utcnow().isoformat(), accessory_id],
            )
            return {"ok": True, "accessory_id": accessory_id, "message": "Accessoire archivé."}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


# ─── v4.1 : Consommables CRUD complet ────────────────────────

@app.get("/api/consumables/{consumable_id}", tags=["v4.1 Consommables"], summary="Fiche complète consommable")
def get_consumable_full(
    consumable_id: str,
    _: None = Security(_require_token),
):
    rows = _rows("SELECT * FROM consumables WHERE consumable_id = ?", [consumable_id])
    if not rows:
        raise HTTPException(status_code=404, detail=f"Consommable {consumable_id} introuvable.")
    row = rows[0]
    ai_meta = None
    if row.get("ai_metadata"):
        try:
            ai_meta = json.loads(row["ai_metadata"])
        except Exception:
            ai_meta = {"raw": row["ai_metadata"]}
    # Photos multi depuis consumable_media (v4.4)
    photos: List[EquipmentPhotoRef] = []
    try:
        photo_rows = _rows(
            "SELECT * FROM consumable_media WHERE consumable_id = ? ORDER BY image_index",
            [consumable_id],
        )
        for p in photo_rows:
            photos.append(EquipmentPhotoRef(
                photo_id=p["media_id"],
                file_id=p.get("final_drive_file_id"),
                folder_id=p.get("final_drive_folder_id"),
                filename=p.get("filename"),
                mime_type=p.get("mime_type"),
                role=p.get("image_role", "overview"),
                sort_order=int(p.get("image_index") or 0),
                is_primary=bool(p.get("is_primary") or False),
                web_view_link=p.get("web_view_link"),
            ))
    except Exception:
        pass
    return ConsumableFullResponse(
        consumable_id=row["consumable_id"],
        label=row.get("label", ""),
        brand=row.get("brand"),
        reference=row.get("reference"),
        category=row.get("category"),
        description=row.get("description"),
        unit=row.get("unit", "pcs"),
        stock_qty=float(row.get("stock_qty", 0)),
        stock_min_alert=float(row.get("stock_min_alert", 0)),
        location_hint=row.get("location_hint"),
        drive_file_id=row.get("drive_file_id"),
        photos=photos,
        photo_count=len(photos),
        notes=row.get("notes"),
        ai_metadata=ai_meta,
        archived=bool(row.get("archived", False)),
        migration_status=row.get("migration_status", "NOT_REVIEWED"),
        legacy_source_id=row.get("legacy_source_id"),
        created_at=str(row["created_at"]) if row.get("created_at") else None,
        updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
    )


@app.patch("/api/consumables/{consumable_id}", tags=["v4.1 Consommables"], summary="Mettre à jour un consommable")
def patch_consumable(
    consumable_id: str,
    body: ConsumableUpdateRequest,
    _: None = Security(_require_token),
):
    if not _rows("SELECT consumable_id FROM consumables WHERE consumable_id = ?", [consumable_id]):
        raise HTTPException(status_code=404, detail=f"Consommable {consumable_id} introuvable.")

    updates: Dict[str, Any] = {}
    for field, value in body.model_dump(exclude_none=True).items():
        if field == "ai_metadata" and isinstance(value, dict):
            updates[field] = json.dumps(value, ensure_ascii=False)
        else:
            updates[field] = value

    if not updates:
        return {"ok": True, "consumable_id": consumable_id, "message": "Aucun champ à modifier."}

    updates["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [consumable_id]
    try:
        _run_write(f"UPDATE consumables SET {set_clause} WHERE consumable_id = ?", params)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"ok": True, "consumable_id": consumable_id, "updated_fields": list(updates.keys())}


@app.delete("/api/consumables/{consumable_id}", tags=["v4.1 Consommables"], summary="Archiver/supprimer un consommable")
def delete_consumable(
    consumable_id: str,
    hard: bool = False,
    _: None = Security(_require_token),
):
    """Par défaut : soft-delete (archived=TRUE). hard=true pour suppression physique."""
    if not _rows("SELECT consumable_id FROM consumables WHERE consumable_id = ?", [consumable_id]):
        raise HTTPException(status_code=404, detail=f"Consommable {consumable_id} introuvable.")
    try:
        if hard:
            _run_write_many([
                ("DELETE FROM links_consumables WHERE consumable_id = ?", [consumable_id]),
                ("DELETE FROM consumables WHERE consumable_id = ?", [consumable_id]),
            ])
            return {"ok": True, "consumable_id": consumable_id, "message": "Consommable supprimé définitivement."}
        else:
            _run_write(
                "UPDATE consumables SET archived = TRUE, updated_at = ? WHERE consumable_id = ?",
                [datetime.utcnow().isoformat(), consumable_id],
            )
            return {"ok": True, "consumable_id": consumable_id, "message": "Consommable archivé."}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


# ─── v4.1 : Gestion des photos équipement ────────────────────

@app.get("/api/equipment/{equipment_id}/photos", tags=["v4.1 Photos"], summary="Lister les photos d'un équipement")
def get_equipment_photos(
    equipment_id: str,
    _: None = Security(_require_token),
):
    if not _rows("SELECT equipment_id FROM equipment WHERE equipment_id = ?", [equipment_id]):
        raise HTTPException(status_code=404, detail=f"Équipement {equipment_id} introuvable.")

    photo_rows = _rows(
        "SELECT * FROM equipment_media WHERE equipment_id = ? ORDER BY image_index",
        [equipment_id],
    )
    photos = [
        EquipmentPhotoRef(
            photo_id=p["media_id"],
            file_id=p.get("final_drive_file_id"),
            folder_id=p.get("final_drive_folder_id"),
            filename=p.get("filename"),
            mime_type=p.get("mime_type"),
            role=p.get("image_role", "overview"),
            sort_order=int(p.get("image_index") or 0),
            is_primary=bool(p.get("is_primary") or False),
            web_view_link=p.get("web_view_link"),
        )
        for p in photo_rows
    ]
    return PhotoListResponse(equipment_id=equipment_id, photos=photos, count=len(photos))


@app.put("/api/equipment/{equipment_id}/photos", tags=["v4.1 Photos"], summary="Remplacer les références photo")
def put_equipment_photos(
    equipment_id: str,
    body: PhotoUpdateRequest,
    _: None = Security(_require_token),
):
    """Remplace entièrement la liste de photos d'un équipement."""
    eq_rows = _rows("SELECT equipment_id, ingestion_id FROM equipment WHERE equipment_id = ?", [equipment_id])
    if not eq_rows:
        raise HTTPException(status_code=404, detail=f"Équipement {equipment_id} introuvable.")

    # ingestion_id hérité de la fiche — peut être NULL après migration v4.3
    ingestion_id = eq_rows[0].get("ingestion_id")

    try:
        statements = [("DELETE FROM equipment_media WHERE equipment_id = ?", [equipment_id])]
        for i, photo in enumerate(body.photos):
            media_id = str(uuid.uuid4())
            statements.append((
                """INSERT INTO equipment_media
                   (media_id, equipment_id, ingestion_id,
                    final_drive_file_id, final_drive_folder_id,
                    filename, mime_type, image_role, image_index, is_primary,
                    attached_by, attached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'api', CURRENT_TIMESTAMP)
                   ON CONFLICT (media_id) DO UPDATE SET
                       final_drive_file_id   = EXCLUDED.final_drive_file_id,
                       final_drive_folder_id = EXCLUDED.final_drive_folder_id,
                       filename              = EXCLUDED.filename,
                       mime_type             = EXCLUDED.mime_type,
                       image_role            = EXCLUDED.image_role,
                       image_index           = EXCLUDED.image_index,
                       is_primary            = EXCLUDED.is_primary""",
                [
                    media_id,
                    equipment_id,
                    ingestion_id,
                    photo.file_id,
                    photo.folder_id,
                    photo.filename,
                    photo.mime_type,
                    photo.role or "overview",
                    photo.sort_order if photo.sort_order is not None else i,
                    photo.is_primary,
                ],
            ))
        _run_write_many(statements)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return {"ok": True, "equipment_id": equipment_id, "photos_count": len(body.photos)}


# ─── v4.1 : Bridge Google Drive ──────────────────────────────

# ─── v4.3 : Attach photo orpheline & détection photos non référencées ────────

@app.post(
    "/api/equipment/{equipment_id}/photos/attach",
    tags=["v4.3 Photos orphelines"],
    summary="Attacher une photo Drive orpheline à un équipement",
)
def attach_equipment_photo(
    equipment_id: str,
    body: PhotoAttachRequest,
    _: None = Security(_require_token),
):
    """Crée un nouveau lien equipment_media pour un fichier Drive qui n'était
    pas encore référencé dans la base. Contrairement à PUT /photos qui remplace
    toute la liste, cet endpoint ajoute une seule photo sans toucher aux autres.

    Cas d'usage : rattacher une photo orpheline identifiée via
    GET /api/drive/orphan-photos ou GET /api/drive/folder/{id}.

    Retourne une erreur 409 si le file_id est déjà lié à cet équipement.
    """
    eq_rows = _rows(
        "SELECT equipment_id, ingestion_id FROM equipment WHERE equipment_id = ?",
        [equipment_id],
    )
    if not eq_rows:
        raise HTTPException(status_code=404, detail=f"Équipement {equipment_id} introuvable.")

    ingestion_id = eq_rows[0].get("ingestion_id")

    # Vérifier si ce file_id est déjà lié à cet équipement
    existing = _rows(
        "SELECT media_id FROM equipment_media WHERE equipment_id = ? AND final_drive_file_id = ?",
        [equipment_id, body.file_id],
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Le fichier {body.file_id} est déjà lié à cet équipement (media_id={existing[0]['media_id']}).",
        )

    # Calculer l'index suivant
    idx_rows = _rows(
        "SELECT COALESCE(MAX(image_index), -1) + 1 AS next_idx FROM equipment_media WHERE equipment_id = ?",
        [equipment_id],
    )
    next_idx = int(idx_rows[0]["next_idx"]) if idx_rows else 0

    media_id = str(uuid.uuid4())
    try:
        _run_write(
            """INSERT INTO equipment_media
               (media_id, equipment_id, ingestion_id,
                final_drive_file_id, final_drive_folder_id,
                filename, mime_type, image_role, image_index, is_primary,
                attached_by, attached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            [
                media_id,
                equipment_id,
                ingestion_id,
                body.file_id,
                body.folder_id,
                body.filename,
                body.mime_type,
                body.role or "overview",
                next_idx,
                body.is_primary,
                body.attached_by,
            ],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return {
        "ok": True,
        "media_id": media_id,
        "equipment_id": equipment_id,
        "file_id": body.file_id,
        "role": body.role,
        "image_index": next_idx,
        "message": f"Photo attachée à l'équipement (role={body.role}, index={next_idx}).",
    }


@app.post(
    "/api/accessories/{accessory_id}/photos/attach",
    tags=["v4.4 Multi-photos"],
    summary="Attacher une photo Drive orpheline à un accessoire",
)
def attach_accessory_photo(
    accessory_id: str,
    body: PhotoAttachRequest,
    _: None = Security(_require_token),
):
    """Insère une photo dans accessory_media. Renvoie 409 si le file_id est déjà lié.
    Met aussi à jour accessories.drive_file_id pour rétrocompatibilité si c'est la première photo (is_primary).
    """
    if not _rows("SELECT accessory_id FROM accessories WHERE accessory_id = ?", [accessory_id]):
        raise HTTPException(status_code=404, detail=f"Accessoire {accessory_id} introuvable.")

    # Vérifier doublon
    existing = _rows(
        "SELECT media_id FROM accessory_media WHERE accessory_id = ? AND final_drive_file_id = ?",
        [accessory_id, body.file_id],
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Le fichier {body.file_id} est déjà lié à l'accessoire {accessory_id}.")

    # Calcul image_index
    idx_rows = _rows(
        "SELECT COALESCE(MAX(image_index), -1) + 1 AS next_idx FROM accessory_media WHERE accessory_id = ?",
        [accessory_id],
    )
    next_idx = idx_rows[0]["next_idx"] if idx_rows else 0
    is_primary = next_idx == 0

    media_id = str(uuid.uuid4())
    role = body.role or "overview"
    now = datetime.utcnow().isoformat()
    try:
        statements = [(
            """INSERT INTO accessory_media
               (media_id, accessory_id, final_drive_file_id, image_role, image_index, is_primary,
                attached_by, attached_at)
               VALUES (?, ?, ?, ?, ?, ?, 'api', ?)""",
            [media_id, accessory_id, body.file_id, role, next_idx, is_primary, now],
        )]
        # Rétrocompatibilité : mettre à jour drive_file_id si photo primaire
        if is_primary:
            statements.append((
                "UPDATE accessories SET drive_file_id = ?, updated_at = ? WHERE accessory_id = ?",
                [body.file_id, now, accessory_id],
            ))
        _run_write_many(statements)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return {
        "ok": True,
        "media_id": media_id,
        "accessory_id": accessory_id,
        "file_id": body.file_id,
        "role": role,
        "image_index": next_idx,
        "is_primary": is_primary,
        "message": f"Photo attachée à l'accessoire (role={role}, index={next_idx}).",
    }


@app.post(
    "/api/consumables/{consumable_id}/photos/attach",
    tags=["v4.4 Multi-photos"],
    summary="Attacher une photo Drive orpheline à un consommable",
)
def attach_consumable_photo(
    consumable_id: str,
    body: PhotoAttachRequest,
    _: None = Security(_require_token),
):
    """Insère une photo dans consumable_media. Renvoie 409 si le file_id est déjà lié.
    Met aussi à jour consumables.drive_file_id pour rétrocompatibilité si c'est la première photo (is_primary).
    """
    if not _rows("SELECT consumable_id FROM consumables WHERE consumable_id = ?", [consumable_id]):
        raise HTTPException(status_code=404, detail=f"Consommable {consumable_id} introuvable.")

    # Vérifier doublon
    existing = _rows(
        "SELECT media_id FROM consumable_media WHERE consumable_id = ? AND final_drive_file_id = ?",
        [consumable_id, body.file_id],
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Le fichier {body.file_id} est déjà lié au consommable {consumable_id}.")

    # Calcul image_index
    idx_rows = _rows(
        "SELECT COALESCE(MAX(image_index), -1) + 1 AS next_idx FROM consumable_media WHERE consumable_id = ?",
        [consumable_id],
    )
    next_idx = idx_rows[0]["next_idx"] if idx_rows else 0
    is_primary = next_idx == 0

    media_id = str(uuid.uuid4())
    role = body.role or "overview"
    now = datetime.utcnow().isoformat()
    try:
        statements = [(
            """INSERT INTO consumable_media
               (media_id, consumable_id, final_drive_file_id, image_role, image_index, is_primary,
                attached_by, attached_at)
               VALUES (?, ?, ?, ?, ?, ?, 'api', ?)""",
            [media_id, consumable_id, body.file_id, role, next_idx, is_primary, now],
        )]
        # Rétrocompatibilité : mettre à jour drive_file_id si photo primaire
        if is_primary:
            statements.append((
                "UPDATE consumables SET drive_file_id = ?, updated_at = ? WHERE consumable_id = ?",
                [body.file_id, now, consumable_id],
            ))
        _run_write_many(statements)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return {
        "ok": True,
        "media_id": media_id,
        "consumable_id": consumable_id,
        "file_id": body.file_id,
        "role": role,
        "image_index": next_idx,
        "is_primary": is_primary,
        "message": f"Photo attachée au consommable (role={role}, index={next_idx}).",
    }


@app.get(
    "/api/drive/orphan-photos",
    tags=["v4.4 Multi-photos"],
    summary="Lister les fichiers Drive non référencés dans la base",
)
def drive_orphan_photos(
    equipment_id: Optional[str] = None,
    accessory_id: Optional[str] = None,
    consumable_id: Optional[str] = None,
    folder_id: Optional[str] = None,
    _: None = Security(_require_token),
):
    """Liste les fichiers présents dans un dossier Drive mais absents de toutes les tables
    media (equipment_media, accessory_media, consumable_media) — photos orphelines.

    Paramètres (au moins un requis) :
    - equipment_id  : utilise final_drive_folder_id de l'équipement
    - accessory_id  : utilise final_drive_folder_id de l'accessoire (accessory_media)
    - consumable_id : utilise final_drive_folder_id du consommable (consumable_media)
    - folder_id     : ID Drive direct du dossier à analyser

    Retourne pour chaque fichier orphelin : file_id, name, mimeType, size.
    """
    if not _DRIVE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Google Drive non configuré sur ce serveur.")

    # Résoudre le folder_id si non fourni
    target_folder_id = folder_id
    entity_label = None

    if not target_folder_id and equipment_id:
        eq_rows = _rows(
            "SELECT final_drive_folder_id FROM equipment WHERE equipment_id = ?",
            [equipment_id],
        )
        if not eq_rows:
            raise HTTPException(status_code=404, detail=f"Équipement {equipment_id} introuvable.")
        target_folder_id = eq_rows[0].get("final_drive_folder_id")
        if not target_folder_id:
            raise HTTPException(
                status_code=422,
                detail=f"L'équipement {equipment_id} n'a pas de final_drive_folder_id renseigné. "
                       "Fournir folder_id directement.",
            )
        entity_label = f"equipment/{equipment_id}"

    if not target_folder_id and accessory_id:
        acc_rows = _rows(
            "SELECT final_drive_folder_id FROM accessory_media WHERE accessory_id = ? LIMIT 1",
            [accessory_id],
        )
        if acc_rows and acc_rows[0].get("final_drive_folder_id"):
            target_folder_id = acc_rows[0]["final_drive_folder_id"]
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Aucun final_drive_folder_id trouvé pour l'accessoire {accessory_id}. "
                       "Fournir folder_id directement.",
            )
        entity_label = f"accessory/{accessory_id}"

    if not target_folder_id and consumable_id:
        con_rows = _rows(
            "SELECT final_drive_folder_id FROM consumable_media WHERE consumable_id = ? LIMIT 1",
            [consumable_id],
        )
        if con_rows and con_rows[0].get("final_drive_folder_id"):
            target_folder_id = con_rows[0]["final_drive_folder_id"]
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Aucun final_drive_folder_id trouvé pour le consommable {consumable_id}. "
                       "Fournir folder_id directement.",
            )
        entity_label = f"consumable/{consumable_id}"

    if not target_folder_id:
        raise HTTPException(
            status_code=422,
            detail="Fournir equipment_id, accessory_id, consumable_id, ou folder_id.",
        )

    # Fichiers dans le dossier Drive
    raw_files = _drive_list_folder(target_folder_id)
    drive_file_ids = {f["id"] for f in raw_files}

    if not drive_file_ids:
        return {"folder_id": target_folder_id, "orphan_count": 0, "orphans": []}

    ph = ", ".join(["?"] * len(drive_file_ids))
    ids_list = list(drive_file_ids)

    # File IDs référencés dans toutes les tables media
    known_ids: set = set()
    for media_table, col in [
        ("equipment_media", "final_drive_file_id"),
        ("accessory_media", "final_drive_file_id"),
        ("consumable_media", "final_drive_file_id"),
    ]:
        rows = _rows(
            f"SELECT DISTINCT {col} FROM {media_table} WHERE {col} IN ({ph})",
            ids_list,
        )
        known_ids.update(r[col] for r in rows if r[col])

    orphans = [
        _map_drive_file(f)
        for f in raw_files
        if f["id"] not in known_ids
    ]

    return {
        "folder_id": target_folder_id,
        "entity": entity_label,
        "equipment_id": equipment_id,
        "accessory_id": accessory_id,
        "consumable_id": consumable_id,
        "total_files_in_folder": len(raw_files),
        "already_linked": len(known_ids),
        "orphan_count": len(orphans),
        "orphans": orphans,
    }


# ─── v4.1 : Bridge Google Drive ──────────────────────────────

@app.get("/api/drive/folder/{folder_id}", tags=["v4.1 Drive"], summary="Lister le contenu d'un dossier Drive")
def drive_list_folder(
    folder_id: str,
    _: None = Security(_require_token),
):
    if not _DRIVE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Google Drive non configuré sur ce serveur.")
    raw_files = _drive_list_folder(folder_id)
    files = [_map_drive_file(f) for f in raw_files]
    return DriveFolderContents(folder_id=folder_id, files=files, count=len(files))


@app.get("/api/drive/files/{file_id}", tags=["v4.1 Drive"], summary="Métadonnées d'un fichier Drive")
def drive_get_file(
    file_id: str,
    _: None = Security(_require_token),
):
    if not _DRIVE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Google Drive non configuré sur ce serveur.")
    meta = _drive_get_file_meta(file_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Fichier Drive {file_id} introuvable.")
    return meta


@app.post("/api/drive/folder", tags=["v4.1 Drive"], summary="Créer un dossier Drive")
def drive_create_folder(
    body: DriveCreateFolderRequest,
    _: None = Security(_require_token),
):
    if not _DRIVE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Google Drive non configuré sur ce serveur.")
    folder_id = _drive_create_folder(body.name, body.parent_id)
    if folder_id is None:
        raise HTTPException(status_code=500, detail="Impossible de créer le dossier Drive.")
    return DriveCreateFolderResponse(ok=True, folder_id=folder_id, message=f"Dossier '{body.name}' créé.")


@app.post("/api/drive/files/{file_id}/move", tags=["v4.1 Drive"], summary="Déplacer un fichier Drive")
def drive_move_file(
    file_id: str,
    body: DriveMoveRequest,
    _: None = Security(_require_token),
):
    if not _DRIVE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Google Drive non configuré sur ce serveur.")
    ok = _drive_move_file(file_id, body.new_parent_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Déplacement Drive échoué.")
    return DriveFileOpResponse(ok=True, file_id=file_id, message="Fichier déplacé.")


@app.post("/api/drive/files/{file_id}/copy", tags=["v4.1 Drive"], summary="Copier un fichier Drive")
def drive_copy_file(
    file_id: str,
    body: DriveCopyRequest,
    _: None = Security(_require_token),
):
    if not _DRIVE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Google Drive non configuré sur ce serveur.")
    new_id = _drive_copy_file(file_id, body.new_parent_id, body.new_name)
    if new_id is None:
        raise HTTPException(status_code=500, detail="Copie Drive échouée.")
    return DriveFileOpResponse(ok=True, file_id=new_id, message="Fichier copié.")


@app.patch("/api/drive/files/{file_id}/rename", tags=["v4.1 Drive"], summary="Renommer un fichier Drive")
def drive_rename_file(
    file_id: str,
    body: DriveRenameRequest,
    _: None = Security(_require_token),
):
    if not _DRIVE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Google Drive non configuré sur ce serveur.")
    ok = _drive_rename_file(file_id, body.new_name)
    if not ok:
        raise HTTPException(status_code=500, detail="Renommage Drive échoué.")
    return DriveFileOpResponse(ok=True, file_id=file_id, message=f"Fichier renommé en '{body.new_name}'.")


# ─── v4.1 : Réassignation photo ──────────────────────────────

@app.post("/api/media/reassign", tags=["v4.4 Multi-photos"], summary="Réassigner une photo entre entités")
def media_reassign(
    body: MediaReassignRequest,
    _: None = Security(_require_token),
):
    """Déplace ou copie une photo entre entités.
    Supporte toutes les combinaisons : equipment, accessory, consumable → equipment, accessory, consumable.
    source_entity_type : "equipment" (défaut) | "accessory" | "consumable"
    """

    # ── Résoudre la table source et récupérer la photo ────────────────────────
    src_type = body.source_entity_type  # "equipment" | "accessory" | "consumable"
    if src_type == "equipment":
        src_table = "equipment_media"
        src_id_col = "equipment_id"
    elif src_type == "accessory":
        src_table = "accessory_media"
        src_id_col = "accessory_id"
    elif src_type == "consumable":
        src_table = "consumable_media"
        src_id_col = "consumable_id"
    else:
        raise HTTPException(status_code=400, detail=f"Type d'entité source inconnu : {src_type}")

    src_rows = _rows(
        f"SELECT * FROM {src_table} WHERE media_id = ? AND {src_id_col} = ?",
        [body.photo_id, body.source_entity_id],
    )
    if not src_rows:
        raise HTTPException(status_code=404, detail=f"Photo {body.photo_id} introuvable sur {src_type}/{body.source_entity_id}.")

    src = src_rows[0]
    final_file_id = src.get("final_drive_file_id")
    new_file_id = final_file_id

    # Copie Drive si demandée
    if body.mode == "copy" and _DRIVE_AVAILABLE and final_file_id:
        new_file_id = _drive_copy_file(final_file_id, None)

    now = datetime.utcnow().isoformat()
    dest_file_id = new_file_id or final_file_id
    role = src.get("image_role", "overview")

    try:
        # ── Insérer dans la table cible ───────────────────────────────────────
        if body.target_entity_type == "equipment":
            new_media_id = str(uuid.uuid4())
            _run_write(
                """INSERT INTO equipment_media
                   (media_id, equipment_id, final_drive_file_id, image_role, image_index, attached_by, attached_at)
                   VALUES (?, ?, ?, ?, 0, 'api', ?)""",
                [new_media_id, body.target_entity_id, dest_file_id, role, now],
            )
        elif body.target_entity_type == "accessory":
            # Calcul image_index sur la cible
            idx_rows = _rows(
                "SELECT COALESCE(MAX(image_index), -1) + 1 AS next_idx FROM accessory_media WHERE accessory_id = ?",
                [body.target_entity_id],
            )
            next_idx = idx_rows[0]["next_idx"] if idx_rows else 0
            is_primary = next_idx == 0
            new_media_id = str(uuid.uuid4())
            _run_write(
                """INSERT INTO accessory_media
                   (media_id, accessory_id, final_drive_file_id, image_role, image_index, is_primary,
                    attached_by, attached_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'api', ?)""",
                [new_media_id, body.target_entity_id, dest_file_id, role, next_idx, is_primary, now],
            )
            if is_primary:
                _run_write(
                    "UPDATE accessories SET drive_file_id = ?, updated_at = ? WHERE accessory_id = ?",
                    [dest_file_id, now, body.target_entity_id],
                )
        elif body.target_entity_type == "consumable":
            idx_rows = _rows(
                "SELECT COALESCE(MAX(image_index), -1) + 1 AS next_idx FROM consumable_media WHERE consumable_id = ?",
                [body.target_entity_id],
            )
            next_idx = idx_rows[0]["next_idx"] if idx_rows else 0
            is_primary = next_idx == 0
            new_media_id = str(uuid.uuid4())
            _run_write(
                """INSERT INTO consumable_media
                   (media_id, consumable_id, final_drive_file_id, image_role, image_index, is_primary,
                    attached_by, attached_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'api', ?)""",
                [new_media_id, body.target_entity_id, dest_file_id, role, next_idx, is_primary, now],
            )
            if is_primary:
                _run_write(
                    "UPDATE consumables SET drive_file_id = ?, updated_at = ? WHERE consumable_id = ?",
                    [dest_file_id, now, body.target_entity_id],
                )
        else:
            raise HTTPException(status_code=400, detail=f"Type d'entité cible inconnu : {body.target_entity_type}")

        # Supprimer la source si mode=move
        if body.mode == "move":
            _run_write(f"DELETE FROM {src_table} WHERE media_id = ?", [body.photo_id])

    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return MediaReassignResponse(
        ok=True,
        photo_id=body.photo_id,
        new_file_id=new_file_id,
        message=f"Photo {body.mode}ée de {src_type}/{body.source_entity_id} vers {body.target_entity_type}/{body.target_entity_id}.",
    )


# ─── v4.1 : Migration orchestrée ─────────────────────────────

@app.post("/api/admin/migrations/reclassify", tags=["v4.1 Migration"], summary="Reclassifier / scinder un équipement")
def reclassify_equipment(
    body: ReclassifyRequest,
    dry_run: bool = False,
    _: None = Security(_require_token),
):
    """
    Opération de migration atomique. Avec ?dry_run=true retourne un plan sans modifier la base.
    Actions supportées :
      - split_record : conserve l'équipement et crée accessoires/consommables associés
      - reclassify_as_accessory : transforme l'équipement en accessoire
      - reclassify_as_consumable : transforme l'équipement en consommable
    """

    # Vérifier la source
    src_rows = _rows("SELECT * FROM equipment WHERE equipment_id = ?", [body.source_equipment_id])
    if not src_rows:
        raise HTTPException(status_code=404, detail=f"Équipement source {body.source_equipment_id} introuvable.")
    src = src_rows[0]

    plan = ReclassifyPlan(
        source_equipment_id=body.source_equipment_id,
        action=body.action,
        equipment_updates=body.target_equipment.model_dump(exclude_none=True) if body.target_equipment else {},
        accessories_to_create=len(body.new_accessories),
        consumables_to_create=len(body.new_consumables),
        links_to_create=len(body.link_existing_accessories) + len(body.link_existing_consumables),
        photos_to_process=len(body.photo_mapping),
        source_will_be_archived=body.source_record_policy == "archive",
    )

    if dry_run:
        return ReclassifyResult(
            ok=True,
            dry_run=True,
            plan=plan,
            message="Plan de migration calculé (dry_run=true — aucune modification effectuée).",
        )

    log_id = str(uuid.uuid4())
    created_accessory_ids: List[str] = []
    created_consumable_ids: List[str] = []
    links_created = 0
    now = datetime.utcnow().isoformat()

    try:
        # 1. Mettre à jour l'équipement source si target_equipment fourni
        if body.target_equipment:
            updates = body.target_equipment.model_dump(exclude_none=True)
            if "ai_metadata" in updates and isinstance(updates["ai_metadata"], dict):
                updates["ai_metadata"] = json.dumps(updates["ai_metadata"], ensure_ascii=False)
            updates["updated_at"] = now
            updates["migration_status"] = "MIGRATED"
            updates["migrated_at"] = now
            updates["migrated_by"] = body.operator
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            _run_write(
                f"UPDATE equipment SET {set_clause} WHERE equipment_id = ?",
                list(updates.values()) + [body.source_equipment_id],
            )

        # 2. Créer les nouveaux accessoires (avec déduplication)
        for acc in body.new_accessories:
            # Vérifier si un accessoire identique existe déjà (non-archivé)
            dup = _rows("""
                SELECT accessory_id FROM accessories
                WHERE LOWER(TRIM(label)) = LOWER(?)
                  AND COALESCE(LOWER(TRIM(brand)), '') = COALESCE(LOWER(?), '')
                  AND COALESCE(LOWER(TRIM(model)), '') = COALESCE(LOWER(?), '')
                  AND (archived IS NULL OR archived = FALSE)
                LIMIT 1
            """, [acc.label or "", acc.brand or "", acc.model or ""])
            if dup:
                acc_id = dup[0]["accessory_id"]
            else:
                acc_id = str(uuid.uuid4())
                _run_write(
                    """INSERT INTO accessories (accessory_id, label, brand, model, category, stock_qty, notes,
                       drive_file_id, created_at, updated_at, migration_status, legacy_source_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'MIGRATED', ?)""",
                    [acc_id, acc.label, acc.brand, acc.model, acc.category,
                     acc.stock_qty, acc.notes, acc.drive_file_id, now, now, body.source_equipment_id],
                )
            created_accessory_ids.append(acc_id)
            # Lier automatiquement à l'équipement source si split_record
            if body.action == "split_record":
                link_id = str(uuid.uuid4())
                _run_write(
                    "INSERT INTO links_compatibility (link_id, equipment_id, accessory_id, created_at) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    [link_id, body.source_equipment_id, acc_id, now],
                )
                links_created += 1

        # 3. Créer les nouveaux consommables (avec déduplication)
        for con in body.new_consumables:
            # Vérifier si un consommable identique existe déjà (non-archivé)
            dup = _rows("""
                SELECT consumable_id FROM consumables
                WHERE LOWER(TRIM(label)) = LOWER(?)
                  AND COALESCE(LOWER(TRIM(brand)), '') = COALESCE(LOWER(?), '')
                  AND COALESCE(LOWER(TRIM(reference)), '') = COALESCE(LOWER(?), '')
                  AND (archived IS NULL OR archived = FALSE)
                LIMIT 1
            """, [con.label or "", con.brand or "", con.reference or ""])
            if dup:
                con_id = dup[0]["consumable_id"]
            else:
                con_id = str(uuid.uuid4())
                _run_write(
                    """INSERT INTO consumables (consumable_id, label, brand, reference, category, unit,
                       stock_qty, stock_min_alert, notes, drive_file_id, created_at, updated_at,
                       migration_status, legacy_source_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'MIGRATED', ?)""",
                    [con_id, con.label, con.brand, con.reference, con.category, con.unit,
                     con.stock_qty, con.stock_min_alert, con.notes, con.drive_file_id,
                     now, now, body.source_equipment_id],
                )
            created_consumable_ids.append(con_id)
            if body.action == "split_record":
                link_id = str(uuid.uuid4())
                _run_write(
                    "INSERT INTO links_consumables (link_id, equipment_id, consumable_id, created_at) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    [link_id, body.source_equipment_id, con_id, now],
                )
                links_created += 1

        # 4. Liens vers accessoires/consommables existants
        for lt in body.link_existing_accessories:
            link_id = str(uuid.uuid4())
            _run_write(
                "INSERT INTO links_compatibility (link_id, equipment_id, accessory_id, note, created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                [link_id, body.source_equipment_id, lt.accessory_id, lt.note, now],
            )
            links_created += 1

        for lt in body.link_existing_consumables:
            link_id = str(uuid.uuid4())
            _run_write(
                "INSERT INTO links_consumables (link_id, equipment_id, consumable_id, qty_per_use, note, created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                [link_id, body.source_equipment_id, lt.consumable_id, lt.qty_per_use, lt.note, now],
            )
            links_created += 1

        # 5. Archiver la source si demandé
        source_archived = False
        if body.source_record_policy == "archive":
            _run_write(
                "UPDATE equipment SET archived = TRUE, migration_status = 'ARCHIVED', updated_at = ? WHERE equipment_id = ?",
                [now, body.source_equipment_id],
            )
            source_archived = True

        # 6. Enregistrer dans legacy_mappings
        mapping_id = str(uuid.uuid4())
        _run_write(
            """INSERT INTO legacy_mappings
               (mapping_id, legacy_equipment_id, canonical_equipment_id, derived_accessory_ids,
                derived_consumable_ids, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (legacy_equipment_id) DO NOTHING""",
            [
                mapping_id,
                body.source_equipment_id,
                body.source_equipment_id if body.action == "split_record" else None,
                json.dumps(created_accessory_ids),
                json.dumps(created_consumable_ids),
                body.notes,
                now,
            ],
        )

        # 7. Journal d'audit
        _run_write(
            """INSERT INTO migration_logs
               (log_id, operation, operator, source_entity_type, source_entity_id,
                target_entities, details, dry_run, status, created_at)
               VALUES (?, ?, ?, 'equipment', ?, ?, ?, FALSE, 'COMPLETED', ?)""",
            [
                log_id,
                body.action,
                body.operator,
                body.source_equipment_id,
                json.dumps({
                    "accessory_ids": created_accessory_ids,
                    "consumable_ids": created_consumable_ids,
                    "links_created": links_created,
                }),
                json.dumps({"source_archived": source_archived, "notes": body.notes}),
                now,
            ],
        )

    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return ReclassifyResult(
        ok=True,
        dry_run=False,
        log_id=log_id,
        plan=plan,
        created_accessory_ids=created_accessory_ids,
        created_consumable_ids=created_consumable_ids,
        links_created=links_created,
        source_archived=source_archived,
        legacy_mapping_id=mapping_id,
        message=f"Migration '{body.action}' effectuée avec succès.",
    )


@app.get("/api/admin/migrations/logs", tags=["v4.1 Migration"], summary="Journal d'audit des migrations")
def get_migration_logs(
    operator: Optional[str] = None,
    operation: Optional[str] = None,
    source_entity_id: Optional[str] = None,
    limit: int = 100,
    _: None = Security(_require_token),
):
    conditions = []
    params: List[Any] = []
    if operator:
        conditions.append("operator = ?")
        params.append(operator)
    if operation:
        conditions.append("operation = ?")
        params.append(operation)
    if source_entity_id:
        conditions.append("source_entity_id = ?")
        params.append(source_entity_id)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = _rows(
        f"SELECT * FROM migration_logs {where} ORDER BY created_at DESC LIMIT ?",
        params + [limit],
    )
    logs = []
    for entry in rows:
        try:
            entry["target_entities"] = json.loads(entry["target_entities"]) if entry.get("target_entities") else None
        except Exception:
            pass
        try:
            entry["details"] = json.loads(entry["details"]) if entry.get("details") else None
        except Exception:
            pass
        logs.append(MigrationLogEntry(
            log_id=entry["log_id"],
            operation=entry["operation"],
            operator=entry["operator"],
            source_entity_type=entry.get("source_entity_type"),
            source_entity_id=entry.get("source_entity_id"),
            target_entities=entry.get("target_entities"),
            details=entry.get("details"),
            dry_run=bool(entry.get("dry_run", False)),
            status=entry.get("status", "COMPLETED"),
            error_message=entry.get("error_message"),
            created_at=str(entry["created_at"]) if entry.get("created_at") else None,
        ))
    return MigrationLogsResponse(count=len(logs), logs=logs)


@app.get(
    "/api/admin/migrations/legacy-mappings/{equipment_id}",
    tags=["v4.1 Migration"],
    summary="Traçabilité legacy → canonical pour un équipement",
)
def get_legacy_mapping(
    equipment_id: str,
    _: None = Security(_require_token),
):
    rows = _rows(
        "SELECT * FROM legacy_mappings WHERE legacy_equipment_id = ?",
        [equipment_id],
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Aucun mapping legacy pour {equipment_id}.")
    row = rows[0]
    derived_acc = json.loads(row["derived_accessory_ids"]) if row.get("derived_accessory_ids") else []
    derived_con = json.loads(row["derived_consumable_ids"]) if row.get("derived_consumable_ids") else []
    return LegacyMappingResponse(
        mapping_id=row["mapping_id"],
        legacy_equipment_id=row["legacy_equipment_id"],
        canonical_equipment_id=row.get("canonical_equipment_id"),
        derived_accessory_ids=derived_acc,
        derived_consumable_ids=derived_con,
        notes=row.get("notes"),
        created_at=str(row["created_at"]) if row.get("created_at") else None,
    )


# ─── Admin : doublons & nettoyage ────────────────────────────

@app.get("/api/admin/duplicates", tags=["v4.1 Admin"], summary="Lister les accessoires/consommables en doublon")
def admin_duplicates(
    _: None = Security(_require_token),
):
    """Retourne tous les accessoires et consommables dont le label+brand apparaît
    plus d'une fois parmi les enregistrements non-archivés."""
    acc_dups = _rows("""
        SELECT label, brand, COUNT(*) AS count,
               STRING_AGG(accessory_id, ', ') AS ids
        FROM accessories
        WHERE (archived IS NULL OR archived = FALSE)
        GROUP BY LOWER(TRIM(label)), LOWER(COALESCE(TRIM(brand), ''))
        HAVING COUNT(*) > 1
        ORDER BY count DESC, label
    """)
    con_dups = _rows("""
        SELECT label, brand, COUNT(*) AS count,
               STRING_AGG(consumable_id, ', ') AS ids
        FROM consumables
        WHERE (archived IS NULL OR archived = FALSE)
        GROUP BY LOWER(TRIM(label)), LOWER(COALESCE(TRIM(brand), ''))
        HAVING COUNT(*) > 1
        ORDER BY count DESC, label
    """)
    return {
        "ok": True,
        "accessories_duplicates": acc_dups,
        "consumables_duplicates": con_dups,
        "total_accessory_groups": len(acc_dups),
        "total_consumable_groups": len(con_dups),
    }


@app.post("/api/admin/archive-by-label", tags=["v4.1 Admin"],
          summary="Archiver tous les enregistrements correspondant à un label+type")
def admin_archive_by_label(
    entity_type: str,
    label: str,
    brand: Optional[str] = None,
    _: None = Security(_require_token),
):
    """Archive TOUS les enregistrements (y compris doublons) du type donné
    dont le label correspond (insensible à la casse).
    entity_type : 'accessory' ou 'consumable'."""
    now = datetime.utcnow().isoformat()
    if entity_type == "accessory":
        rows = _rows("""
            SELECT accessory_id FROM accessories
            WHERE LOWER(TRIM(label)) = LOWER(?)
              AND (archived IS NULL OR archived = FALSE)
        """, [label.strip()])
        for r in rows:
            _run_write(
                "UPDATE accessories SET archived = TRUE, updated_at = ? WHERE accessory_id = ?",
                [now, r["accessory_id"]],
            )
        return {"ok": True, "archived_count": len(rows),
                "entity_type": "accessory", "label": label}
    elif entity_type == "consumable":
        rows = _rows("""
            SELECT consumable_id FROM consumables
            WHERE LOWER(TRIM(label)) = LOWER(?)
              AND (archived IS NULL OR archived = FALSE)
        """, [label.strip()])
        for r in rows:
            _run_write(
                "UPDATE consumables SET archived = TRUE, updated_at = ? WHERE consumable_id = ?",
                [now, r["consumable_id"]],
            )
        return {"ok": True, "archived_count": len(rows),
                "entity_type": "consumable", "label": label}
    else:
        raise HTTPException(status_code=400,
                            detail="entity_type doit être 'accessory' ou 'consumable'.")


# ─── v4.1 : Admin export & doublons ──────────────────────────

@app.get("/api/admin/export", tags=["v4.1 Admin"], summary="Export bulk de tout l'inventaire")
def admin_export(
    include_archived: bool = False,
    _: None = Security(_require_token),
):
    """Exporte l'intégralité de l'inventaire (équipements, accessoires, consommables, liens)."""
    arch_filter = "" if include_archived else "WHERE (archived IS NULL OR archived = FALSE)"

    equipment = _rows(f"SELECT * FROM equipment {arch_filter} ORDER BY label")
    accessories = _rows(f"SELECT * FROM accessories {arch_filter} ORDER BY label")
    consumables = _rows(f"SELECT * FROM consumables {arch_filter} ORDER BY label")
    links_compat = _rows("SELECT * FROM links_compatibility ORDER BY created_at")
    links_cons = _rows("SELECT * FROM links_consumables ORDER BY created_at")

    return {
        "exported_at": datetime.utcnow().isoformat(),
        "include_archived": include_archived,
        "counts": {
            "equipment": len(equipment),
            "accessories": len(accessories),
            "consumables": len(consumables),
            "links_compatibility": len(links_compat),
            "links_consumables": len(links_cons),
        },
        "equipment": equipment,
        "accessories": accessories,
        "consumables": consumables,
        "links_compatibility": links_compat,
        "links_consumables": links_cons,
    }


@app.get("/api/admin/duplicates", tags=["v4.1 Admin"], summary="Détection de doublons potentiels")
def admin_duplicates(
    threshold: float = 0.85,
    _: None = Security(_require_token),
):
    """Détecte les doublons potentiels en comparant label+brand+model par type d'entité."""

    def _detect_duplicates(rows: List[dict], entity_type: str, id_col: str) -> List[DuplicateGroup]:
        groups: List[DuplicateGroup] = []
        items = [(r[id_col], f"{r.get('label', '')} {r.get('brand', '')} {r.get('model', '')}".lower().strip()) for r in rows]
        seen: set = set()
        for i in range(len(items)):
            if items[i][0] in seen:
                continue
            cluster_ids = [items[i][0]]
            cluster_labels = [rows[i].get("label", "")]
            max_score = 0.0
            for j in range(i + 1, len(items)):
                if items[j][0] in seen:
                    continue
                score = SequenceMatcher(None, items[i][1], items[j][1]).ratio()
                if score >= threshold:
                    cluster_ids.append(items[j][0])
                    cluster_labels.append(rows[j].get("label", ""))
                    seen.add(items[j][0])
                    max_score = max(max_score, score)
            if len(cluster_ids) > 1:
                seen.add(items[i][0])
                groups.append(DuplicateGroup(
                    entity_type=entity_type,
                    ids=cluster_ids,
                    labels=cluster_labels,
                    similarity_score=round(max_score, 3),
                    reason="label/brand/model similaires",
                ))
        return groups

    eq_rows = _rows(
        "SELECT equipment_id, label, brand, model FROM equipment WHERE (archived IS NULL OR archived = FALSE)"
    )
    acc_rows = _rows(
        "SELECT accessory_id, label, brand, model FROM accessories WHERE (archived IS NULL OR archived = FALSE)"
    )
    con_rows = _rows(
        "SELECT consumable_id, label, brand FROM consumables WHERE (archived IS NULL OR archived = FALSE)"
    )

    groups: List[DuplicateGroup] = []
    groups.extend(_detect_duplicates(eq_rows, "equipment", "equipment_id"))
    groups.extend(_detect_duplicates(acc_rows, "accessory", "accessory_id"))
    groups.extend(_detect_duplicates(con_rows, "consumable", "consumable_id"))

    return DuplicatesResponse(count=len(groups), groups=groups)


# ─── v4.2 : Catalogue unifié multi-types ─────────────────────

# Requête UNION ALL pour agréger les 3 types d'entités
_CATALOG_UNION_SQL = """
    SELECT
        'equipment'                      AS entity_type,
        e.equipment_id                   AS entity_id,
        e.label,
        e.brand,
        e.model,
        CAST(NULL AS VARCHAR)            AS reference,
        e.category,
        e.subtype,
        e.condition_label,
        e.location_hint,
        e.status                         AS availability_status,
        CAST(NULL AS DOUBLE)             AS stock_qty,
        CAST(NULL AS DOUBLE)             AS stock_min_alert,
        COALESCE(e.archived, FALSE)      AS archived,
        COALESCE(e.migration_status, 'NOT_REVIEWED') AS migration_status,
        (SELECT em.final_drive_file_id
         FROM equipment_media em
         WHERE em.equipment_id = e.equipment_id
         ORDER BY CASE em.image_role
             WHEN 'overview'  THEN 1
             WHEN 'nameplate' THEN 2
             ELSE 3
         END LIMIT 1)                    AS primary_photo_file_id,
        (SELECT COUNT(*)
         FROM equipment_media em
         WHERE em.equipment_id = e.equipment_id) AS photo_count
    FROM equipment e

    UNION ALL

    SELECT
        'accessory'                      AS entity_type,
        a.accessory_id                   AS entity_id,
        a.label,
        a.brand,
        a.model,
        CAST(NULL AS VARCHAR)            AS reference,
        a.category,
        CAST(NULL AS VARCHAR)            AS subtype,
        CAST(NULL AS VARCHAR)            AS condition_label,
        a.location_hint,
        CAST(NULL AS VARCHAR)            AS availability_status,
        CAST(a.stock_qty AS DOUBLE)      AS stock_qty,
        CAST(NULL AS DOUBLE)             AS stock_min_alert,
        COALESCE(a.archived, FALSE)      AS archived,
        COALESCE(a.migration_status, 'NOT_REVIEWED') AS migration_status,
        a.drive_file_id                  AS primary_photo_file_id,
        CASE WHEN a.drive_file_id IS NOT NULL THEN 1 ELSE 0 END AS photo_count
    FROM accessories a

    UNION ALL

    SELECT
        'consumable'                     AS entity_type,
        c.consumable_id                  AS entity_id,
        c.label,
        c.brand,
        CAST(NULL AS VARCHAR)            AS model,
        c.reference,
        c.category,
        CAST(NULL AS VARCHAR)            AS subtype,
        CAST(NULL AS VARCHAR)            AS condition_label,
        c.location_hint,
        CAST(NULL AS VARCHAR)            AS availability_status,
        c.stock_qty,
        c.stock_min_alert,
        COALESCE(c.archived, FALSE)      AS archived,
        COALESCE(c.migration_status, 'NOT_REVIEWED') AS migration_status,
        c.drive_file_id                  AS primary_photo_file_id,
        CASE WHEN c.drive_file_id IS NOT NULL THEN 1 ELSE 0 END AS photo_count
    FROM consumables c
"""


@app.get(
    "/api/catalog",
    tags=["v4.2 Catalogue"],
    summary="Catalogue unifié : équipements + accessoires + consommables",
)
def get_catalog(
    q: Optional[str] = None,
    entity_type: Optional[str] = None,   # "equipment" | "accessory" | "consumable"
    category: Optional[str] = None,
    brand: Optional[str] = None,
    archived: Optional[bool] = None,
    migration_status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    _: None = Security(_require_token),
) -> CatalogListResponse:
    """
    Retourne toutes les entités de premier rang dans un payload harmonisé.
    Supporte les mêmes filtres que les endpoints individuels, plus `entity_type`.
    """
    conditions: List[str] = []
    params: List[Any] = []

    # Archivage — par défaut on masque les archivés
    if archived:
        conditions.append("archived = TRUE")
    else:
        conditions.append("(archived IS NULL OR archived = FALSE)")

    if entity_type:
        conditions.append("entity_type = ?")
        params.append(entity_type)

    if q:
        like = f"%{q.lower()}%"
        conditions.append(
            "(LOWER(label) LIKE ? OR LOWER(brand) LIKE ? OR LOWER(model) LIKE ?"
            " OR LOWER(reference) LIKE ? OR LOWER(category) LIKE ?)"
        )
        params += [like, like, like, like, like]

    if category:
        conditions.append("LOWER(category) = ?")
        params.append(category.lower())

    if brand:
        conditions.append("LOWER(brand) = ?")
        params.append(brand.lower())

    if migration_status:
        conditions.append("migration_status = ?")
        params.append(migration_status)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * page_size

    try:
        count_rows = _rows(
            f"SELECT COUNT(*) AS cnt FROM ({_CATALOG_UNION_SQL}) catalog {where}",
            params,
        )
        total = int(count_rows[0]["cnt"]) if count_rows else 0

        item_rows = _rows(
            f"""SELECT * FROM ({_CATALOG_UNION_SQL}) catalog
                {where}
                ORDER BY label
                LIMIT ? OFFSET ?""",
            params + [page_size, offset],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    items: List[CatalogItem] = []
    for r in item_rows:
        sq = r.get("stock_qty")
        sm = r.get("stock_min_alert")
        stock_ok = (float(sq) >= float(sm)) if (sq is not None and sm is not None) else None
        items.append(CatalogItem(
            entity_type=r["entity_type"],
            entity_id=r["entity_id"],
            label=r.get("label", ""),
            brand=r.get("brand"),
            model=r.get("model"),
            reference=r.get("reference"),
            category=r.get("category"),
            subtype=r.get("subtype"),
            condition_label=r.get("condition_label"),
            location_hint=r.get("location_hint"),
            availability_status=r.get("availability_status"),
            stock_qty=float(sq) if sq is not None else None,
            stock_min_alert=float(sm) if sm is not None else None,
            stock_ok=stock_ok,
            archived=bool(r.get("archived", False)),
            migration_status=r.get("migration_status", "NOT_REVIEWED"),
            primary_photo_file_id=r.get("primary_photo_file_id"),
            photo_count=int(r.get("photo_count") or 0),
        ))

    entity_types_present = sorted({i.entity_type for i in items})
    return CatalogListResponse(
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + page_size) < total,
        entity_types=entity_types_present,
        items=items,
    )


# ─── v4.2 : Photos accessoires & consommables ─────────────────

@app.get(
    "/api/accessories/{accessory_id}/photos",
    tags=["v4.2 Catalogue"],
    summary="Photos d'un accessoire",
)
def get_accessory_photos(
    accessory_id: str,
    _: None = Security(_require_token),
) -> PhotoListResponse:
    """Retourne toutes les photos d'un accessoire depuis accessory_media (v4.4)."""
    if not _rows("SELECT accessory_id FROM accessories WHERE accessory_id = ?", [accessory_id]):
        raise HTTPException(status_code=404, detail=f"Accessoire {accessory_id} introuvable.")
    photo_rows = _rows(
        "SELECT * FROM accessory_media WHERE accessory_id = ? ORDER BY image_index",
        [accessory_id],
    )
    photos = [
        EquipmentPhotoRef(
            photo_id=p["media_id"],
            file_id=p.get("final_drive_file_id"),
            folder_id=p.get("final_drive_folder_id"),
            filename=p.get("filename"),
            mime_type=p.get("mime_type"),
            role=p.get("image_role", "overview"),
            sort_order=int(p.get("image_index") or 0),
            is_primary=bool(p.get("is_primary") or False),
            web_view_link=p.get("web_view_link"),
        )
        for p in photo_rows
    ]
    return PhotoListResponse(equipment_id=accessory_id, photos=photos, count=len(photos))


@app.put(
    "/api/accessories/{accessory_id}/photos",
    tags=["v4.2 Catalogue"],
    summary="Définir la photo d'un accessoire",
)
def put_accessory_photos(
    accessory_id: str,
    body: PhotoUpdateRequest,
    _: None = Security(_require_token),
):
    """Remplace entièrement la liste de photos d'un accessoire via accessory_media (v4.4)."""
    if not _rows("SELECT accessory_id FROM accessories WHERE accessory_id = ?", [accessory_id]):
        raise HTTPException(status_code=404, detail=f"Accessoire {accessory_id} introuvable.")
    try:
        _run_write("DELETE FROM accessory_media WHERE accessory_id = ?", [accessory_id])
        for i, photo in enumerate(body.photos):
            media_id = str(uuid.uuid4())
            _run_write(
                """INSERT INTO accessory_media
                   (media_id, accessory_id, final_drive_file_id, final_drive_folder_id,
                    filename, mime_type, image_role, image_index, is_primary,
                    attached_by, attached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'api', CURRENT_TIMESTAMP)""",
                [
                    media_id, accessory_id, photo.file_id, photo.folder_id,
                    photo.filename, photo.mime_type, photo.role or "overview",
                    photo.sort_order if photo.sort_order is not None else i,
                    photo.is_primary,
                ],
            )
        # Mettre à jour drive_file_id (rétrocompatibilité) avec la photo primaire
        primary = next((p for p in body.photos if p.is_primary), body.photos[0] if body.photos else None)
        if primary:
            _run_write(
                "UPDATE accessories SET drive_file_id = ?, updated_at = ? WHERE accessory_id = ?",
                [primary.file_id, datetime.utcnow().isoformat(), accessory_id],
            )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"ok": True, "accessory_id": accessory_id, "photos_count": len(body.photos)}


@app.get(
    "/api/consumables/{consumable_id}/photos",
    tags=["v4.2 Catalogue"],
    summary="Photos d'un consommable",
)
def get_consumable_photos(
    consumable_id: str,
    _: None = Security(_require_token),
) -> PhotoListResponse:
    """Retourne toutes les photos d'un consommable depuis consumable_media (v4.4)."""
    if not _rows("SELECT consumable_id FROM consumables WHERE consumable_id = ?", [consumable_id]):
        raise HTTPException(status_code=404, detail=f"Consommable {consumable_id} introuvable.")
    photo_rows = _rows(
        "SELECT * FROM consumable_media WHERE consumable_id = ? ORDER BY image_index",
        [consumable_id],
    )
    photos = [
        EquipmentPhotoRef(
            photo_id=p["media_id"],
            file_id=p.get("final_drive_file_id"),
            folder_id=p.get("final_drive_folder_id"),
            filename=p.get("filename"),
            mime_type=p.get("mime_type"),
            role=p.get("image_role", "overview"),
            sort_order=int(p.get("image_index") or 0),
            is_primary=bool(p.get("is_primary") or False),
            web_view_link=p.get("web_view_link"),
        )
        for p in photo_rows
    ]
    return PhotoListResponse(equipment_id=consumable_id, photos=photos, count=len(photos))


@app.put(
    "/api/consumables/{consumable_id}/photos",
    tags=["v4.2 Catalogue"],
    summary="Définir la photo d'un consommable",
)
def put_consumable_photos(
    consumable_id: str,
    body: PhotoUpdateRequest,
    _: None = Security(_require_token),
):
    """Remplace entièrement la liste de photos d'un consommable via consumable_media (v4.4)."""
    if not _rows("SELECT consumable_id FROM consumables WHERE consumable_id = ?", [consumable_id]):
        raise HTTPException(status_code=404, detail=f"Consommable {consumable_id} introuvable.")
    try:
        _run_write("DELETE FROM consumable_media WHERE consumable_id = ?", [consumable_id])
        for i, photo in enumerate(body.photos):
            media_id = str(uuid.uuid4())
            _run_write(
                """INSERT INTO consumable_media
                   (media_id, consumable_id, final_drive_file_id, final_drive_folder_id,
                    filename, mime_type, image_role, image_index, is_primary,
                    attached_by, attached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'api', CURRENT_TIMESTAMP)""",
                [
                    media_id, consumable_id, photo.file_id, photo.folder_id,
                    photo.filename, photo.mime_type, photo.role or "overview",
                    photo.sort_order if photo.sort_order is not None else i,
                    photo.is_primary,
                ],
            )
        primary = next((p for p in body.photos if p.is_primary), body.photos[0] if body.photos else None)
        if primary:
            _run_write(
                "UPDATE consumables SET drive_file_id = ?, updated_at = ? WHERE consumable_id = ?",
                [primary.file_id, datetime.utcnow().isoformat(), consumable_id],
            )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"ok": True, "consumable_id": consumable_id, "photos_count": len(body.photos)}


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
    # workers=1 obligatoire : DuckDB n'accepte qu'un seul processus écrivain
    # à la fois sur le même fichier .duckdb
    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=API_PORT,
        log_level="info",
        workers=1,
    )
