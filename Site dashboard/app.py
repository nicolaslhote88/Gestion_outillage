"""
SIGA — Système d'Ingestion et de Gestion d'Atelier
Frontend Streamlit — Interface de gestion d'inventaire outillage

Architecture : single-file app.py modulaire avec fonctions par section.
Connexion DuckDB en read_only=True (compatible avec n8n qui écrit en parallèle).
"""

import base64
import io
import json
import os
import re
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build as _build_gdrive
from fpdf import FPDF, XPos, YPos
from PIL import Image

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION GLOBALE
# ─────────────────────────────────────────────────────────────

DB_PATH = "/files/duckdb/siga_v1.duckdb"
# Fichier JSON écrit par l'API pour piloter le kiosque sans polling DuckDB
KIOSK_STATE_FILE = Path(DB_PATH).parent / "kiosk_state.json"

st.set_page_config(
    page_title="SIGA — Gestion Outillage",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────
#  CSS PERSONNALISÉ — look SaaS moderne
# ─────────────────────────────────────────────────────────────

CUSTOM_CSS = """
<style>
/* ── Police & fond général ── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
    border-right: 1px solid #334155;
}
[data-testid="stSidebar"] * {
    color: #e2e8f0 !important;
}
[data-testid="stSidebar"] .stRadio label {
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
    transition: background 0.2s;
}
[data-testid="stSidebar"] .stRadio label:hover {
    background: #334155;
}

/* ── Metric cards ── */
[data-testid="stMetric"] {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 18px 20px 14px 20px;
}
[data-testid="stMetricValue"] {
    font-size: 2rem !important;
    font-weight: 700;
    color: #f1f5f9 !important;
}
[data-testid="stMetricLabel"] {
    color: #94a3b8 !important;
    font-size: 0.8rem !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
[data-testid="stMetricDelta"] svg { display: none; }

/* ── Cards équipement ── */
.equip-card {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 14px;
    margin-bottom: 12px;
    transition: border-color 0.2s, transform 0.15s;
}
.equip-card:hover {
    border-color: #3b82f6;
    transform: translateY(-2px);
}
.equip-card h4 {
    margin: 6px 0 2px 0;
    font-size: 0.95rem;
    font-weight: 600;
    color: #f1f5f9;
}
.equip-card p {
    margin: 2px 0;
    font-size: 0.8rem;
    color: #94a3b8;
}

/* ── Badges statut ── */
.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.73rem;
    font-weight: 600;
    letter-spacing: 0.03em;
    white-space: nowrap;
}
.badge-green  { background: #064e3b; color: #6ee7b7; border: 1px solid #065f46; }
.badge-red    { background: #450a0a; color: #fca5a5; border: 1px solid #7f1d1d; }
.badge-yellow { background: #422006; color: #fed7aa; border: 1px solid #7c2d12; }
.badge-blue   { background: #1e3a5f; color: #93c5fd; border: 1px solid #1e40af; }
.badge-gray   { background: #1e293b; color: #94a3b8; border: 1px solid #475569; }

/* ── Section title ── */
.section-title {
    font-size: 1.4rem;
    font-weight: 700;
    color: #f1f5f9;
    margin-bottom: 4px;
}
.section-subtitle {
    color: #64748b;
    font-size: 0.88rem;
    margin-bottom: 20px;
}

/* ── Séparateur ── */
hr { border-color: #334155 !important; }

/* ── Expander validation ── */
[data-testid="stExpander"] {
    border: 1px solid #334155 !important;
    border-radius: 10px !important;
    background: #0f172a !important;
    margin-bottom: 10px;
}

/* ── Tableau ── */
[data-testid="stDataFrame"] {
    border-radius: 10px;
    overflow: hidden;
}

/* ── Bouton principal ── */
.stButton > button {
    background: #3b82f6 !important;
    color: white !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 500 !important;
    padding: 6px 16px !important;
    font-size: 0.82rem !important;
    transition: background 0.2s !important;
}
.stButton > button:hover {
    background: #2563eb !important;
}

/* ── Masquer le menu hamburger & footer Streamlit ── */
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }
header    { visibility: hidden; }
</style>
"""

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
#  UTILITAIRES BASE DE DONNÉES
# ─────────────────────────────────────────────────────────────

def run_query(sql: str, params=None) -> pd.DataFrame:
    """Exécute une requête SQL et retourne un DataFrame.
    Ouvre et ferme la connexion à chaque appel pour ne jamais bloquer
    les écritures concurrentes de n8n (DuckDB file-lock).
    """
    try:
        with duckdb.connect(DB_PATH, read_only=True) as conn:
            if params:
                return conn.execute(sql, params).df()
            return conn.execute(sql).df()
    except duckdb.IOException as e:
        st.error(f"❌ Base de données inaccessible (verrou en cours ?) : {e}")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Erreur SQL : {e}")
        return pd.DataFrame()


def run_write(sql: str, params=None, _retries: int = 5) -> bool:
    """Exécute une requête SQL en écriture (INSERT/UPDATE/CREATE).
    Réessaie jusqu'à _retries fois avec backoff exponentiel si la base
    est verrouillée par n8n (erreur 'database is locked').
    Retourne True si succès, False sinon."""
    delay = 2
    last_err = None
    for attempt in range(_retries):
        try:
            with duckdb.connect(DB_PATH, read_only=False) as conn:
                if params:
                    conn.execute(sql, params)
                else:
                    conn.execute(sql)
            return True
        except duckdb.IOException as e:
            last_err = e
            if attempt < _retries - 1:
                time.sleep(delay)
                delay *= 2
        except Exception as e:
            st.error(f"Erreur SQL écriture : {e}")
            return False
    st.error(f"❌ Base de données inaccessible après {_retries} tentatives (verrou n8n ?) : {last_err}")
    return False


def _read_kiosk_state() -> dict:
    """Lit l'état courant du kiosque depuis le fichier JSON écrit par l'API.

    Aucune connexion DuckDB n'est ouverte — supprime le verrou continu
    observé avec le polling sur la table ui_commands.
    """
    try:
        return json.loads(KIOSK_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"command_type": "CLEAR_SCREEN", "updated_at": "", "data": {}}


def _b64img(file_id: str | None, *, mime: str = "image/jpeg") -> str | None:
    """Retourne une data-URL base64 pour un file_id Google Drive, ou None."""
    if not file_id or str(file_id) in ("None", "nan", ""):
        return None
    img = get_drive_image_bytes(file_id)
    if not img:
        return None
    return f"data:{mime};base64,{base64.b64encode(img).decode()}"



def get_current_user() -> str:
    """Retourne le login de l'utilisateur authentifié (en minuscules).

    Ordre de priorité :
    1. Header X-Forwarded-User injecté par Traefik (si headerField configuré)
    2. Décodage du header Authorization: Basic <base64(user:pass)>
       (Traefik BasicAuth laisse ce header par défaut, removeHeader=false)
    Retourne 'visiteur' si aucun header n'est disponible.
    """
    try:
        import base64
        headers = st.context.headers

        # 1. X-Forwarded-User (Traefik avec headerField configuré)
        fwd = headers.get("X-Forwarded-User", "").strip()
        if fwd:
            return fwd.lower()

        # 2. Authorization: Basic <base64> (fallback universel)
        auth = headers.get("Authorization", "")
        if auth.lower().startswith("basic "):
            decoded = base64.b64decode(auth[6:]).decode("utf-8", errors="replace")
            username = decoded.split(":", 1)[0].strip()
            if username:
                return username.lower()
    except Exception:
        pass
    return "visiteur"


def is_admin() -> bool:
    """Retourne True uniquement pour l'utilisateur 'nicolas'."""
    return get_current_user() == "nicolas"


# Pages accessibles selon le rôle
_ADMIN_PAGES = [
    "🏭 Parc Matériel",
    "⚠ Centre de Validation",
    "📦 Suivi des Mouvements",
    "🧰 Gestion des Kits",
    "📊 Dashboard",
    "🔒 Journal des Accès",
]
_USER_PAGES = [
    "🏭 Parc Matériel",
    "📦 Suivi des Mouvements",
    "🧰 Gestion des Kits",
    "📊 Dashboard",
]


def allowed_pages() -> list[str]:
    """Retourne la liste des pages accessibles pour l'utilisateur courant."""
    return _ADMIN_PAGES if is_admin() else _USER_PAGES


def db_is_reachable() -> bool:
    """Vérifie la disponibilité de la DB sans garder la connexion ouverte."""
    try:
        with duckdb.connect(DB_PATH, read_only=True) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def init_db_tables() -> None:
    """Crée (ou migre) toutes les tables applicatives SIGA."""

    # ── Mouvements individuels ─────────────────────────────
    run_write("""
        CREATE TABLE IF NOT EXISTS equipment_movements (
            movement_id           VARCHAR PRIMARY KEY,
            equipment_id          VARCHAR,
            movement_type         VARCHAR,
            borrower_name         VARCHAR,
            borrower_contact      VARCHAR,
            out_date              TIMESTAMP,
            expected_return_date  TIMESTAMP,
            actual_return_date    TIMESTAMP,
            notes                 VARCHAR,
            batch_id              VARCHAR,
            kit_id                VARCHAR,
            created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migration : colonnes ajoutées après v1
    run_write("ALTER TABLE equipment_movements ADD COLUMN IF NOT EXISTS batch_id VARCHAR")
    run_write("ALTER TABLE equipment_movements ADD COLUMN IF NOT EXISTS kit_id   VARCHAR")

    # ── Kits (caisses à outils) ───────────────────────────
    run_write("""
        CREATE TABLE IF NOT EXISTS kits (
            kit_id      VARCHAR PRIMARY KEY,
            name        VARCHAR,
            description VARCHAR,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Audit trail modifications fiches ──────────────────
    run_write("""
        CREATE TABLE IF NOT EXISTS equipment_audit (
            audit_id      VARCHAR PRIMARY KEY,
            equipment_id  VARCHAR,
            action        VARCHAR,
            changed_fields VARCHAR,
            operator      VARCHAR,
            changed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Boîte aux lettres UI (pilotage kiosque par IA) ────
    run_write("""
        CREATE TABLE IF NOT EXISTS ui_commands (
            command_id   VARCHAR PRIMARY KEY,
            target_ui    VARCHAR,
            command_type VARCHAR,
            payload      VARCHAR,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            executed     BOOLEAN DEFAULT FALSE
        )
    """)


def safe_json(value, default=None):
    """Parse une colonne JSON stockée en VARCHAR. Retourne default si NULL ou invalide."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default if default is not None else {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default if default is not None else {}


def drive_thumbnail_url(file_id: str, size: int = 400) -> str:
    """Transforme un Drive file_id en URL d'image directement affichable."""
    if not file_id or pd.isna(file_id) if isinstance(file_id, float) else False:
        return ""
    return f"https://drive.google.com/thumbnail?id={file_id}&sz=w{size}"


@st.cache_resource
def _drive_service_ro():
    """Service Google Drive lecture seule, mis en cache pour toute la session Streamlit.
    Évite de reconstruire la connexion (Credentials + HTTP discovery) à chaque image."""
    sa_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/secrets/service_account.json")
    if not Path(sa_path).exists():
        return None
    try:
        creds = service_account.Credentials.from_service_account_file(
            sa_path, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        return _build_gdrive("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        import sys
        print(f"[DRIVE_SVC_RO] {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return None


@st.cache_resource
def _drive_service_rw():
    """Service Google Drive lecture/écriture, mis en cache pour toute la session."""
    sa_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/secrets/service_account.json")
    if not Path(sa_path).exists():
        return None
    try:
        creds = service_account.Credentials.from_service_account_file(
            sa_path, scopes=["https://www.googleapis.com/auth/drive"]
        )
        return _build_gdrive("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        import sys
        print(f"[DRIVE_SVC_RW] {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return None


def trash_drive_folder(folder_id: str) -> bool:
    """Déplace un dossier Drive à la corbeille via service account.
    Retourne True si succès, False sinon."""
    if not folder_id or str(folder_id) in ("nan", "None", ""):
        return False
    svc = _drive_service_rw()
    if svc is None:
        return False
    try:
        svc.files().update(
            fileId=folder_id,
            body={"trashed": True},
            supportsAllDrives=True,
        ).execute()
        return True
    except Exception as e:
        import sys
        print(f"[DRIVE_TRASH] folder_id={folder_id} → {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return False


_DELETE_WEBHOOK_URL    = "https://n8n.srv961978.hstgr.cloud/webhook/siga-delete-equipment-folder"
_DELETE_WEBHOOK_SECRET = "TON_SECRET_ICI"  # secret partagé — à changer en prod


def call_delete_equipment_webhook(folder_id: str, equipment_id: str = "", label: str = "") -> bool:
    """Appelle le webhook n8n de suppression du dossier Drive d'un équipement.
    Fallback automatique sur trash_drive_folder() si l'URL n'est pas configurée."""
    webhook_url = _DELETE_WEBHOOK_URL.strip()
    if not webhook_url:
        return trash_drive_folder(folder_id)

    payload = {
        "folder_id": folder_id,
        "equipment_id": equipment_id,
        "label": label,
    }
    headers = {
        "Content-Type": "application/json",
        "X-SIGA-Shared-Secret": _DELETE_WEBHOOK_SECRET,
    }

    try:
        resp = requests.post(webhook_url, json=payload, headers=headers, timeout=15)
        data = resp.json() if resp.content else {}
        return bool(data.get("ok", False))
    except Exception as e:
        import sys
        print(f"[DELETE_WEBHOOK] folder_id={folder_id} → {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        # Fallback sur suppression directe
        return trash_drive_folder(folder_id)


def drive_direct_url(file_id: str) -> str:
    """URL de rendu direct Google Drive (uc?export=view)."""
    if not file_id:
        return ""
    return f"https://drive.google.com/uc?export=view&id={file_id}"


def drive_folder_url(folder_id: str) -> str:
    """Construit l'URL web d'un dossier Drive à partir de son ID."""
    if not folder_id or str(folder_id) in ("nan", "None", ""):
        return ""
    return f"https://drive.google.com/drive/folders/{folder_id}"


@st.cache_data(ttl=7200, show_spinner=False)
def get_drive_image_bytes(file_id: str) -> bytes | None:
    """Télécharge une image Drive côté serveur via service account mis en cache.
    Retourne None si le service account n'est pas configuré (fallback URL).
    TTL 2h — le service Drive est mis en cache via _drive_service_ro()."""
    if not file_id or str(file_id) in ("nan", "None", ""):
        return None
    svc = _drive_service_ro()
    if svc is None:
        return None
    try:
        return svc.files().get_media(fileId=file_id, supportsAllDrives=True).execute()
    except Exception as e:
        import sys
        print(f"[DRIVE_ERR] file_id={file_id} → {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return None


def drive_img_src(file_id: str, size: int = 400):
    """Retourne bytes (proxy SA) ou URL Drive en fallback.
    Permet d'afficher les images sans que l'utilisateur ait accès au Drive."""
    data = get_drive_image_bytes(file_id)
    return data if data is not None else drive_thumbnail_url(file_id, size)


@st.cache_data(ttl=3600, show_spinner=False)
def get_drive_thumb(file_id: str, max_px: int = 160, quality: int = 55) -> bytes | None:
    """Miniature compressée pour la galerie : télécharge via SA puis redimensionne
    à max_px (côté long) et encode en JPEG à la qualité indiquée."""
    raw = get_drive_image_bytes(file_id)
    if not raw:
        return None
    try:
        img   = Image.open(io.BytesIO(raw))
        ratio = max_px / max(img.width, img.height)
        if ratio < 1:
            img = img.resize(
                (int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS
            )
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=quality)
        buf.seek(0)
        return buf.read()
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
#  UTILITAIRES UI
# ─────────────────────────────────────────────────────────────

CONDITION_BADGE = {
    "neuf":          ("badge-green",  "Neuf"),
    "bon":           ("badge-green",  "Bon état"),
    "usé":           ("badge-yellow", "Usé"),
    "use":           ("badge-yellow", "Usé"),
    "très usé":      ("badge-red",    "Très usé"),
    "tres use":      ("badge-red",    "Très usé"),
    "hors service":  ("badge-red",    "Hors service"),
    "inconnu":       ("badge-gray",   "Inconnu"),
}


def condition_badge(condition_label: str) -> str:
    """Retourne le HTML du badge de condition."""
    if not condition_label:
        return '<span class="badge badge-gray">—</span>'
    key = str(condition_label).lower().strip()
    css_class, label = CONDITION_BADGE.get(key, ("badge-blue", condition_label))
    return f'<span class="badge {css_class}">{label}</span>'


def review_badge(review_required) -> str:
    """Badge 'À réviser' ou 'Validé'."""
    if review_required:
        return '<span class="badge badge-red">⚠ À réviser</span>'
    return '<span class="badge badge-green">✓ Validé</span>'


def confidence_badge(confidence) -> str:
    """Badge de niveau de confiance IA."""
    try:
        conf = float(confidence) if confidence is not None else 0.0
    except (TypeError, ValueError):
        conf = 0.0
    if conf >= 0.85:
        return f'<span class="badge badge-green">IA {conf:.0%}</span>'
    if conf >= 0.60:
        return f'<span class="badge badge-yellow">IA {conf:.0%}</span>'
    return f'<span class="badge badge-red">IA {conf:.0%}</span>'


def null_str(value, fallback: str = "—") -> str:
    """Convertit NULL/NaN en chaîne de remplacement."""
    if value is None:
        return fallback
    if isinstance(value, float) and pd.isna(value):
        return fallback
    s = str(value).strip()
    return s if s and s.lower() not in ("nan", "none", "null") else fallback


_PARIS_TZ = "Europe/Paris"

def fmt_datetime(ts) -> str:
    """Formate un timestamp UTC (naïf ou localisé) en heure locale Europe/Paris."""
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return "N/A"
    dt = pd.to_datetime(ts)
    if dt.tzinfo is None:
        dt = dt.tz_localize("UTC")
    dt = dt.tz_convert(_PARIS_TZ)
    return dt.strftime("%d/%m/%Y %H:%M")


def fmt_datetime_series(series: pd.Series) -> pd.Series:
    """Applique fmt_datetime sur une Series pandas."""
    dt = pd.to_datetime(series)
    if dt.dt.tz is None:
        dt = dt.dt.tz_localize("UTC")
    return dt.dt.tz_convert(_PARIS_TZ).dt.strftime("%d/%m/%Y %H:%M")

# ─────────────────────────────────────────────────────────────
#  VUE 1 : DASHBOARD
# ─────────────────────────────────────────────────────────────

def render_dashboard():
    st.markdown('<p class="section-title">📊 Dashboard</p>', unsafe_allow_html=True)
    st.markdown('<p class="section-subtitle">Vue d\'ensemble du parc outillage</p>', unsafe_allow_html=True)

    # ── KPIs ──────────────────────────────────────────────────
    kpi_df = run_query("""
        SELECT
            COUNT(*)                                        AS total,
            COUNT(*) FILTER (WHERE review_required = true) AS a_reviser,
            SUM(purchase_price)                             AS valeur_totale,
            MAX(received_at)                                AS dernier_ajout
        FROM equipment
    """)

    if kpi_df.empty:
        st.warning("Aucune donnée disponible dans la base.")
        return

    row = kpi_df.iloc[0]
    total       = int(row.get("total", 0) or 0)
    a_reviser   = int(row.get("a_reviser", 0) or 0)
    valeur      = row.get("valeur_totale")
    dernier     = row.get("dernier_ajout")

    valeur_str  = f"{valeur:,.0f} €".replace(",", " ") if valeur and not pd.isna(valeur) else "N/A"
    dernier_str = fmt_datetime(dernier)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("🔧 Total équipements",     total)
    col2.metric("⚠ En attente validation", a_reviser,
                delta=f"{a_reviser/total*100:.0f}% du parc" if total else None)
    col3.metric("💰 Valeur estimée",        valeur_str)
    col4.metric("🕐 Dernier ajout",         dernier_str)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Graphiques ────────────────────────────────────────────
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        cat_df = run_query("""
            SELECT
                COALESCE(subtype, 'Inconnu') AS categorie,
                COUNT(*) AS nb
            FROM equipment
            GROUP BY subtype
            ORDER BY nb DESC
            LIMIT 12
        """)
        if not cat_df.empty:
            fig = px.bar(
                cat_df,
                x="nb", y="categorie",
                orientation="h",
                title="Répartition par type d'outil",
                labels={"nb": "Quantité", "categorie": ""},
                color="nb",
                color_continuous_scale=px.colors.sequential.Blues_r,
                template="plotly_dark",
            )
            fig.update_layout(
                paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
                font_color="#94a3b8",
                showlegend=False, coloraxis_showscale=False,
                height=340,
                margin=dict(l=10, r=10, t=40, b=10),
            )
            fig.update_xaxes(gridcolor="#1e293b")
            fig.update_yaxes(gridcolor="#1e293b")
            st.plotly_chart(fig, use_container_width=True)

    with chart_col2:
        brand_df = run_query("""
            SELECT
                COALESCE(brand, 'Inconnue') AS marque,
                COUNT(*) AS nb
            FROM equipment
            GROUP BY brand
            ORDER BY nb DESC
            LIMIT 8
        """)
        if not brand_df.empty:
            fig2 = px.pie(
                brand_df,
                names="marque", values="nb",
                title="Répartition par marque",
                template="plotly_dark",
                hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Set3,
            )
            fig2.update_layout(
                paper_bgcolor="#0f172a",
                font_color="#94a3b8",
                height=340,
                margin=dict(l=10, r=10, t=40, b=10),
                legend=dict(bgcolor="#1e293b", bordercolor="#334155"),
            )
            st.plotly_chart(fig2, use_container_width=True)

    st.markdown("---")

    # ── Dernières entrées ─────────────────────────────────────
    st.markdown("#### 🕐 Dernières entrées")
    last_df = run_query("""
        SELECT
            label, brand, subtype, condition_label, confidence,
            review_required, received_at
        FROM equipment
        ORDER BY received_at DESC
        LIMIT 5
    """)

    if not last_df.empty:
        # Formatter pour affichage propre
        display_df = last_df.copy()
        display_df["Date"] = fmt_datetime_series(display_df["received_at"])
        display_df["Équipement"] = display_df["label"].fillna("—")
        display_df["Marque"]     = display_df["brand"].fillna("—")
        display_df["Type"]       = display_df["subtype"].fillna("—")
        display_df["État"]       = display_df["condition_label"].fillna("—")
        display_df["Confiance"]  = display_df["confidence"].apply(
            lambda x: f"{float(x):.0%}" if x is not None and not pd.isna(x) else "—"
        )
        display_df["Révision"]   = display_df["review_required"].apply(
            lambda x: "⚠ Oui" if x else "✓ Non"
        )
        st.dataframe(
            display_df[["Date", "Équipement", "Marque", "Type", "État", "Confiance", "Révision"]],
            use_container_width=True,
            hide_index=True,
        )

# ─────────────────────────────────────────────────────────────
#  VUE 2 : CENTRE DE VALIDATION
# ─────────────────────────────────────────────────────────────

def render_validation():
    if not is_admin():
        st.error("🔒 Accès réservé à l'administrateur.")
        return
    st.markdown('<p class="section-title">⚠ Centre de Validation</p>', unsafe_allow_html=True)
    st.markdown('<p class="section-subtitle">Équipements détectés par l\'IA nécessitant une vérification humaine</p>', unsafe_allow_html=True)

    # Équipement ciblé depuis la modale (édition directe depuis Parc Matériel)
    # On garde ces valeurs en session tant que l'édition n'est pas terminée
    edit_target_id = st.session_state.get("edit_equipment_id", None)
    _return_to     = st.session_state.get("edit_return_to",    None)

    def _finish_edit():
        """Nettoie la session et retourne à la page d'origine."""
        st.session_state.pop("edit_equipment_id", None)
        st.session_state.pop("edit_return_to",    None)
        if _return_to:
            st.session_state["_nav_request"] = _return_to

    if edit_target_id:
        items_df = run_query("""
            SELECT
                e.equipment_id, e.label, e.brand, e.model, e.serial_number,
                e.subtype, e.category, e.condition_label, e.location_hint,
                e.confidence, e.notes,
                e.review_reasons_json, e.missing_fields_json,
                e.technical_specs_json, e.business_context_json,
                e.final_drive_folder_id,
                e.received_at
            FROM equipment e
            WHERE e.review_required = true OR e.equipment_id = ?
            ORDER BY e.equipment_id = ? DESC, e.received_at DESC
        """, [edit_target_id, edit_target_id])
    else:
        items_df = run_query("""
            SELECT
                e.equipment_id, e.label, e.brand, e.model, e.serial_number,
                e.subtype, e.category, e.condition_label, e.location_hint,
                e.confidence, e.notes,
                e.review_reasons_json, e.missing_fields_json,
                e.technical_specs_json, e.business_context_json,
                e.final_drive_folder_id,
                e.received_at
            FROM equipment e
            WHERE e.review_required = true
            ORDER BY e.received_at DESC
        """)

    if items_df.empty:
        if not edit_target_id:
            st.success("✅ Aucun équipement en attente de validation. Le parc est à jour !")
        return

    st.info(f"**{len(items_df)} équipement(s)** en attente de révision.", icon="ℹ️")

    for _, row in items_df.iterrows():
        # Récupère les raisons de révision
        reasons = safe_json(row.get("review_reasons_json"), [])
        if isinstance(reasons, dict):
            reasons = list(reasons.values())
        reasons_str = " · ".join(reasons) if reasons else "Vérification manuelle requise"

        label = null_str(row.get("label"), "Équipement sans nom")
        conf  = row.get("confidence")
        conf_str = f"{float(conf):.0%}" if conf is not None and not pd.isna(conf) else "?"

        expander_title = f"🔧 {label}   |   Confiance IA : {conf_str}   |   {reasons_str}"

        is_target = (edit_target_id is not None and row["equipment_id"] == edit_target_id)
        with st.expander(expander_title, expanded=is_target):
            img_col, info_col = st.columns([1, 2])

            # ── Photo ──────────────────────────────────────────
            with img_col:
                media_df = run_query("""
                    SELECT final_drive_file_id, image_role, image_index
                    FROM equipment_media
                    WHERE equipment_id = ?
                    ORDER BY
                        CASE image_role
                            WHEN 'overview'   THEN 1
                            WHEN 'nameplate'  THEN 2
                            WHEN 'detail'     THEN 3
                            ELSE 4
                        END,
                        image_index
                """, [row["equipment_id"]])

                if not media_df.empty:
                    # Photo principale (grande)
                    main_media = media_df.iloc[0]
                    file_id    = main_media.get("final_drive_file_id")
                    img_src    = drive_img_src(file_id, 600) if file_id else None
                    if img_src:
                        try:
                            st.image(img_src, use_container_width=True,
                                     caption=f"Photo 1 · {null_str(main_media.get('image_role'))}")
                        except Exception:
                            st.warning(f"⚠️ Image corrompue ou inaccessible (Drive ID : `{file_id}`)")
                    else:
                        st.info("📷 Aucune image disponible")

                    # Toutes les photos restantes (3 par ligne)
                    remaining_media = media_df.iloc[1:]
                    for chunk_start in range(0, len(remaining_media), 3):
                        chunk = remaining_media.iloc[chunk_start:chunk_start + 3]
                        tcols = st.columns(3)
                        for j, (_, m) in enumerate(chunk.iterrows()):
                            fid = m.get("final_drive_file_id")
                            if fid:
                                photo_num = chunk_start + j + 2  # 2-based (1 = main)
                                try:
                                    tcols[j].image(
                                        drive_img_src(fid, 200),
                                        use_container_width=True,
                                        caption=f"Photo {photo_num} · {null_str(m.get('image_role'))}",
                                    )
                                except Exception:
                                    tcols[j].warning(f"⚠️ Image corrompue (`{fid}`)")
                else:
                    st.info("📷 Aucune image disponible")

            # ── Formulaire d'édition ────────────────────────────
            with info_col:
                missing = safe_json(row.get("missing_fields_json"), [])
                if isinstance(missing, dict):
                    missing = list(missing.keys())
                missing_set = {str(f).lower() for f in missing}

                eq_id = row["equipment_id"]

                # ── Bouton valider rapide ───────────────────────
                quick_validate_key = f"quick_validate_{eq_id}"
                if st.button("✅ Valider directement (sans modification)",
                             key=quick_validate_key, use_container_width=True):
                    ok = run_write("""
                        UPDATE equipment SET review_required = false
                        WHERE equipment_id = ?
                    """, [eq_id])
                    if ok:
                        run_write("""
                            INSERT INTO equipment_audit
                                (audit_id, equipment_id, action, changed_fields, operator)
                            VALUES (?, ?, 'VALIDATE', 'review_required', ?)
                        """, [str(uuid.uuid4()), eq_id, get_current_user()])
                        st.success("✅ Fiche validée.")
                        _finish_edit()
                        st.rerun()

                st.markdown("---")

                # Raisons de révision (lecture seule)
                if reasons:
                    st.markdown("**Raisons de la révision**")
                    for r in reasons:
                        st.markdown(f"&nbsp;&nbsp;&nbsp;⚠ {r}")
                    st.markdown("---")

                st.markdown("**Corriger les informations**")

                def _label(field_key: str, field_label: str) -> str:
                    if field_key.lower() in missing_set:
                        return f"🔴 {field_label} *(manquant)*"
                    return f"✅ {field_label}"

                CONDITION_OPTIONS = ["neuf", "bon", "use", "tres use", "hors service", "inconnu"]

                # ── Sélecteur vignette principale ──────────────────
                photo_options = {}
                if not media_df.empty:
                    for idx, (_, m) in enumerate(media_df.iterrows()):
                        fid = m.get("final_drive_file_id")
                        if fid:
                            role = null_str(m.get("image_role"), "autre")
                            photo_options[f"Photo {idx + 1} ({role})"] = fid

                # ── Champs de base (sans st.form pour permettre les suppressions
                #    dans les sections ci-dessous sans perdre la saisie) ─────────
                if photo_options:
                    current_main_fid = media_df.iloc[0].get("final_drive_file_id") if not media_df.empty else None
                    option_keys = list(photo_options.keys())
                    default_idx = 0
                    for i, (k, v) in enumerate(photo_options.items()):
                        if v == current_main_fid:
                            default_idx = i
                            break
                    f_main_photo_label = st.selectbox(
                        "🖼 Vignette principale (Parc Matériel)",
                        options=option_keys,
                        index=default_idx,
                        key=f"main_photo_{eq_id}",
                    )
                    f_main_photo_fid = photo_options[f_main_photo_label]
                else:
                    f_main_photo_fid = None

                f_label    = st.text_input(_label("label",          "Nom / Désignation"),
                                           value=null_str(row.get("label"), ""),         key=f"label_{eq_id}")
                f_brand    = st.text_input(_label("brand",          "Marque"),
                                           value=null_str(row.get("brand"), ""),         key=f"brand_{eq_id}")
                f_model    = st.text_input(_label("model",          "Modèle"),
                                           value=null_str(row.get("model"), ""),         key=f"model_{eq_id}")
                f_serial   = st.text_input(_label("serial_number",  "N° de série"),
                                           value=null_str(row.get("serial_number"), ""), key=f"serial_{eq_id}")
                f_subtype  = st.text_input(_label("subtype",        "Type d'outil"),
                                           value=null_str(row.get("subtype"), ""),       key=f"subtype_{eq_id}")

                cur_cond = null_str(row.get("condition_label"), "inconnu").lower()
                cond_idx = CONDITION_OPTIONS.index(cur_cond) if cur_cond in CONDITION_OPTIONS else len(CONDITION_OPTIONS) - 1
                f_condition = st.selectbox(_label("condition_label", "État"),
                                           options=CONDITION_OPTIONS, index=cond_idx,    key=f"cond_{eq_id}")

                f_location = st.text_input(_label("location_hint",  "Emplacement"),
                                           value=null_str(row.get("location_hint"), ""), key=f"loc_{eq_id}")
                f_notes    = st.text_area("📝 Notes",
                                          value=null_str(row.get("notes"), ""),          key=f"notes_{eq_id}",
                                          height=80)

                # ── Édition spécifications techniques ──────────────
                st.markdown("---")
                specs = safe_json(row.get("technical_specs_json"), {})
                _del_specs_key = f"del_specs_{eq_id}"
                if _del_specs_key not in st.session_state:
                    st.session_state[_del_specs_key] = set()
                active_specs = {k: v for k, v in specs.items()
                                if k not in st.session_state[_del_specs_key]}

                with st.expander(f"⚙ Spécifications techniques ({len(active_specs)} entrée(s))", expanded=True):
                    if active_specs:
                        for _sk, _sv in list(active_specs.items()):
                            _c1, _c2, _c3 = st.columns([3, 4, 1])
                            _c1.text_input("Clé", value=_sk,
                                           key=f"sk_{eq_id}_{_sk}", label_visibility="collapsed")
                            _c2.text_input("Valeur", value=str(_sv),
                                           key=f"sv_{eq_id}_{_sk}", label_visibility="collapsed")
                            if _c3.button("🗑", key=f"sdel_{eq_id}_{_sk}", help="Supprimer cette spec"):
                                st.session_state[_del_specs_key].add(_sk)
                                st.rerun()  # force re-render pour masquer la ligne immédiatement
                    else:
                        st.caption("Aucune spécification technique.")
                    if st.button("🗑 Tout effacer les specs", key=f"clear_specs_{eq_id}"):
                        run_write("UPDATE equipment SET technical_specs_json = '{}' WHERE equipment_id = ?",
                                  [eq_id])
                        st.session_state.pop(_del_specs_key, None)
                        st.rerun()

                # ── Édition contexte métier (accessoires, consommables, éléments) ──
                biz = safe_json(row.get("business_context_json"), {})

                def _edit_biz_list(section_key: str, icon: str, title: str):
                    """Affiche une section de liste éditable du business_context."""
                    _alt_keys = {"accessories": "accessoires",
                                 "consumables": "consommables",
                                 "associated_items": "elements_associes",
                                 "condition_notes": "constats"}
                    items = biz.get(section_key) or biz.get(_alt_keys.get(section_key, ""), [])
                    if isinstance(items, str):
                        items = [items] if items else []
                    if not isinstance(items, list):
                        items = []

                    _del_biz_key = f"del_biz_{eq_id}_{section_key}"
                    if _del_biz_key not in st.session_state:
                        st.session_state[_del_biz_key] = set()

                    active_count = sum(1 for i in range(len(items))
                                       if i not in st.session_state[_del_biz_key])

                    with st.expander(f"{icon} {title} ({active_count} entrée(s))", expanded=True):
                        for _orig_idx, _item in enumerate(items):
                            if _orig_idx in st.session_state[_del_biz_key]:
                                continue
                            _raw = _item if isinstance(_item, str) else json.dumps(_item, ensure_ascii=False)
                            _bc1, _bc2 = st.columns([5, 1])
                            _bc1.text_area("", value=_raw,
                                           key=f"biz_{eq_id}_{section_key}_{_orig_idx}",
                                           height=60, label_visibility="collapsed")
                            if _bc2.button("🗑", key=f"bizdel_{eq_id}_{section_key}_{_orig_idx}",
                                           help="Supprimer cet élément"):
                                st.session_state[_del_biz_key].add(_orig_idx)
                                st.rerun()  # force re-render pour masquer l'item immédiatement

                        if active_count == 0:
                            st.caption("Aucun élément.")

                        if st.button(f"🗑 Tout vider", key=f"clear_biz_{eq_id}_{section_key}"):
                            _new_biz = dict(biz)
                            _new_biz[section_key] = []
                            run_write(
                                "UPDATE equipment SET business_context_json = ? WHERE equipment_id = ?",
                                [json.dumps(_new_biz, ensure_ascii=False), eq_id])
                            st.session_state.pop(_del_biz_key, None)
                            st.rerun()

                _edit_biz_list("accessories",    "✦", "Accessoires livrés")
                _edit_biz_list("consumables",    "⚙", "Consommables associés")
                _edit_biz_list("associated_items", "🔗", "Éléments associés")
                _edit_biz_list("condition_notes", "📝", "Constats visuels")

                # ── Bouton unique "Valider et enregistrer tout" ─────
                st.markdown("---")
                if st.button("💾 Valider et enregistrer tout", key=f"save_all_{eq_id}",
                             type="primary", use_container_width=True):
                    # Lecture des valeurs saisies (session_state via clés widget)
                    sv_label    = st.session_state.get(f"label_{eq_id}",  f_label)
                    sv_brand    = st.session_state.get(f"brand_{eq_id}",  f_brand)
                    sv_model    = st.session_state.get(f"model_{eq_id}",  f_model)
                    sv_serial   = st.session_state.get(f"serial_{eq_id}", f_serial)
                    sv_subtype  = st.session_state.get(f"subtype_{eq_id}", f_subtype)
                    sv_cond     = st.session_state.get(f"cond_{eq_id}",   f_condition)
                    sv_loc      = st.session_state.get(f"loc_{eq_id}",    f_location)
                    sv_notes    = st.session_state.get(f"notes_{eq_id}",  f_notes)
                    sv_photo    = photo_options.get(
                        st.session_state.get(f"main_photo_{eq_id}", ""), f_main_photo_fid
                    ) if photo_options else f_main_photo_fid

                    # Sauvegarde champs de base
                    ok = run_write("""
                        UPDATE equipment SET
                            label           = ?,
                            brand           = ?,
                            model           = ?,
                            serial_number   = ?,
                            subtype         = ?,
                            condition_label = ?,
                            location_hint   = ?,
                            notes           = ?,
                            review_required = false
                        WHERE equipment_id = ?
                    """, [
                        sv_label or None, sv_brand or None, sv_model or None,
                        sv_serial or None, sv_subtype or None, sv_cond,
                        sv_loc or None, sv_notes or None, eq_id,
                    ])

                    # Sauvegarde vignette principale
                    if ok and sv_photo:
                        run_write("""
                            UPDATE equipment_media
                            SET image_role = CASE
                                WHEN final_drive_file_id = ? THEN 'overview'
                                WHEN image_role = 'overview' THEN 'detail'
                                ELSE image_role
                            END
                            WHERE equipment_id = ?
                        """, [sv_photo, eq_id])

                    # Sauvegarde spécifications techniques
                    if ok:
                        _del_s = st.session_state.get(_del_specs_key, set())
                        _new_specs = {}
                        for _orig_k, _orig_v in specs.items():
                            if _orig_k in _del_s:
                                continue
                            _nk = st.session_state.get(f"sk_{eq_id}_{_orig_k}", _orig_k)
                            _nv = st.session_state.get(f"sv_{eq_id}_{_orig_k}", str(_orig_v))
                            if _nk.strip():
                                _new_specs[_nk.strip()] = _nv
                        run_write("UPDATE equipment SET technical_specs_json = ? WHERE equipment_id = ?",
                                  [json.dumps(_new_specs, ensure_ascii=False), eq_id])
                        st.session_state.pop(_del_specs_key, None)

                    # Sauvegarde contexte métier
                    if ok:
                        _new_biz = dict(biz)
                        for _sk_biz in ("accessories", "consumables", "associated_items", "condition_notes"):
                            _del_biz_key = f"del_biz_{eq_id}_{_sk_biz}"
                            _del_b = st.session_state.get(_del_biz_key, set())
                            _alt_keys2 = {"accessories": "accessoires",
                                          "consumables": "consommables",
                                          "associated_items": "elements_associes",
                                          "condition_notes": "constats"}
                            _items_b = biz.get(_sk_biz) or biz.get(_alt_keys2.get(_sk_biz, ""), [])
                            if isinstance(_items_b, str):
                                _items_b = [_items_b] if _items_b else []
                            if not isinstance(_items_b, list):
                                _items_b = []
                            _new_list = []
                            for _i_b in range(len(_items_b)):
                                if _i_b in _del_b:
                                    continue
                                _raw_val = st.session_state.get(
                                    f"biz_{eq_id}_{_sk_biz}_{_i_b}",
                                    _items_b[_i_b] if isinstance(_items_b[_i_b], str)
                                    else json.dumps(_items_b[_i_b], ensure_ascii=False)
                                ).strip()
                                if _raw_val:
                                    try:
                                        _new_list.append(json.loads(_raw_val))
                                    except Exception:
                                        _new_list.append(_raw_val)
                            _new_biz[_sk_biz] = _new_list
                            st.session_state.pop(_del_biz_key, None)
                        run_write("UPDATE equipment SET business_context_json = ? WHERE equipment_id = ?",
                                  [json.dumps(_new_biz, ensure_ascii=False), eq_id])

                    if ok:
                        _changed = ", ".join(filter(None, [
                            "label"         if sv_label    != null_str(row.get("label"),          "") else "",
                            "brand"         if sv_brand    != null_str(row.get("brand"),          "") else "",
                            "model"         if sv_model    != null_str(row.get("model"),          "") else "",
                            "serial_number" if sv_serial   != null_str(row.get("serial_number"),  "") else "",
                            "subtype"       if sv_subtype  != null_str(row.get("subtype"),         "") else "",
                            "condition"     if sv_cond     != null_str(row.get("condition_label"), "") else "",
                            "location"      if sv_loc      != null_str(row.get("location_hint"),  "") else "",
                            "notes"         if sv_notes    != null_str(row.get("notes"),          "") else "",
                            "specs/biz",
                        ])) or "aucun changement détecté"
                        run_write("""
                            INSERT INTO equipment_audit
                                (audit_id, equipment_id, action, changed_fields, operator)
                            VALUES (?, ?, 'UPDATE', ?, ?)
                        """, [str(uuid.uuid4()), eq_id, _changed, get_current_user()])
                        st.success("✅ Équipement validé et mis à jour.")
                        _finish_edit()
                        st.rerun()

                # ── Bouton Supprimer avec confirmation ─────────────
                st.markdown("---")
                folder_id_for_del = null_str(row.get("final_drive_folder_id"), "")
                confirm_key = f"confirm_del_{eq_id}"

                if not st.session_state.get(confirm_key):
                    if st.button("🗑 Supprimer définitivement", key=f"del_btn_{eq_id}",
                                 use_container_width=False):
                        st.session_state[confirm_key] = True
                        st.rerun()
                else:
                    st.warning(
                        "⚠️ Supprimer **définitivement** cet équipement de la base "
                        + ("**et mettre son dossier Drive à la corbeille** ?" if folder_id_for_del else "?")
                    )
                    c1, c2 = st.columns(2)
                    if c1.button("✅ Confirmer la suppression", key=f"del_yes_{eq_id}", type="primary"):
                        run_write("DELETE FROM equipment_media WHERE equipment_id = ?", [eq_id])
                        run_write("DELETE FROM equipment WHERE equipment_id = ?", [eq_id])
                        if folder_id_for_del:
                            drive_ok = call_delete_equipment_webhook(
                                folder_id_for_del, eq_id,
                                null_str(row.get("label"), "Équipement"),
                            )
                            if drive_ok:
                                st.info("📁 Dossier Drive déplacé à la corbeille.")
                            else:
                                st.warning("⚠️ Suppression DB OK mais le dossier Drive n'a pas pu être mis à la corbeille.")
                        st.session_state.pop(confirm_key, None)
                        _finish_edit()
                        st.rerun()
                    if c2.button("↩ Annuler", key=f"del_no_{eq_id}"):
                        st.session_state.pop(confirm_key, None)
                        _finish_edit()
                        st.rerun()

                # Lien Drive
                folder_url = drive_folder_url(row.get("final_drive_folder_id"))
                if folder_url:
                    st.markdown(f"[📁 Ouvrir le dossier Drive]({folder_url})")

# ─────────────────────────────────────────────────────────────
#  PARTAGE — Texte brut & PDF
# ─────────────────────────────────────────────────────────────

def generate_share_text(equipment_row, business_context_dict: dict) -> str:
    """Génère un texte brut partageable (WhatsApp / Mail).
    Champs exclus : purchase_price, purchase_currency, usage_notes, source_message_text.
    Aucun lien Drive dans le texte généré.
    """
    r = equipment_row
    biz = business_context_dict or {}

    def v(key, fallback="—"):
        val = r.get(key)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return fallback
        return str(val).strip() or fallback

    lines = []
    lines.append(f"🔧 Fiche Équipement : {v('brand')} {v('model')}")
    lines.append(f"🏷️ Type : {v('category')} - {v('subtype')}")
    lines.append(f"🔢 N° de série : {v('serial_number')}")
    lines.append(f"⚙️ État : {v('condition_label')}")

    # Constats visuels (condition_notes dans business_context ou notes directes)
    condition_notes = biz.get("condition_notes") or biz.get("constats") or []
    if isinstance(condition_notes, str) and condition_notes:
        condition_notes = [condition_notes]
    if condition_notes:
        lines.append("")
        lines.append("📝 Constats visuels :")
        for note in condition_notes:
            lines.append(f"  - {note}")

    # Éléments associés (accessoires, consommables, associated_items)
    accessories = biz.get("accessories") or biz.get("accessoires") or []
    consumables = biz.get("consumables") or biz.get("consommables") or []
    associated  = biz.get("associated_items") or biz.get("elements_associes") or []
    all_items   = list(accessories) + list(consumables) + list(associated)

    if all_items:
        lines.append("")
        lines.append("📦 Éléments associés (Accessoires/Consommables) :")
        for item in all_items:
            if isinstance(item, dict):
                label = (
                    item.get("label") or item.get("raw_label")
                    or item.get("detected_object_type") or "?"
                )
                brand = item.get("brand", "")
                model = item.get("model", "")
                parts = [label]
                if brand:
                    parts.append(brand)
                if model:
                    parts.append(model)
                lines.append(f"  - {' · '.join(parts)}")
            else:
                lines.append(f"  - {item}")

    return "\n".join(lines)


def generate_equipment_pdf(equipment_row, media_dataframe: pd.DataFrame, business_context_dict: dict) -> bytes:
    """Génère un PDF léger avec infos et photos embarquées.
    Champs exclus : purchase_price, purchase_currency, usage_notes, source_message_text.
    Liens Drive exclus du texte ; images téléchargées directement pour embed.
    """
    r = equipment_row
    biz = business_context_dict or {}

    # Remplace les caractères Unicode hors Latin-1 par des équivalents ASCII.
    # Helvetica (fpdf2 core font) ne supporte que Latin-1 ; les tirets cadratins,
    # guillemets typographiques, ellipses, etc. lèvent FPDFUnicodeEncodingException.
    _UNICODE_MAP = str.maketrans({
        "\u2014": "-",    # em dash —
        "\u2013": "-",    # en dash –
        "\u2018": "'",    # guillemet '
        "\u2019": "'",    # guillemet '
        "\u201c": '"',    # guillemet "
        "\u201d": '"',    # guillemet "
        "\u2026": "...",  # ellipsis …
        "\u2022": "-",    # puce •
        "\u00b7": "-",    # middle dot ·
        "\u00a0": " ",    # espace insécable
    })

    def _s(text: str) -> str:
        """Assainit une chaîne pour Helvetica (Latin-1 strict)."""
        text = text.translate(_UNICODE_MAP)
        return text.encode("latin-1", errors="replace").decode("latin-1")

    def v(key, fallback="-"):
        val = r.get(key)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return fallback
        return _s(str(val).strip()) or fallback

    _NL = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_margins(15, 15, 15)

    # ── En-tête ────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"Fiche Equipement : {v('brand')} {v('model')}", **_NL)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 6, f"Type : {v('category')} - {v('subtype')}", **_NL)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    # ── Identification ─────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Identification", **_NL)
    pdf.set_font("Helvetica", "", 10)
    fields = [
        ("N de serie", v("serial_number")),
        ("Etat",       v("condition_label")),
        ("Emplacement", v("location_hint")),
        ("Mode acquisition", v("ownership_mode")),
    ]
    for field_label, field_val in fields:
        pdf.cell(55, 6, f"{field_label} :", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.cell(0, 6, field_val, **_NL)
    pdf.ln(4)

    # ── Constats visuels ───────────────────────────────────────
    condition_notes = biz.get("condition_notes") or biz.get("constats") or []
    if isinstance(condition_notes, str) and condition_notes:
        condition_notes = [condition_notes]
    if condition_notes:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Constats visuels", **_NL)
        pdf.set_font("Helvetica", "", 10)
        for note in condition_notes:
            pdf.multi_cell(0, 6, _s(f"- {note}"), **_NL)
        pdf.ln(2)

    # ── Éléments associés ──────────────────────────────────────
    accessories = biz.get("accessories") or biz.get("accessoires") or []
    consumables = biz.get("consumables") or biz.get("consommables") or []
    associated  = biz.get("associated_items") or biz.get("elements_associes") or []
    all_items   = list(accessories) + list(consumables) + list(associated)

    if all_items:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Elements associes (Accessoires/Consommables)", **_NL)
        pdf.set_font("Helvetica", "", 10)
        for item in all_items:
            if isinstance(item, dict):
                item_label = (
                    item.get("label") or item.get("raw_label")
                    or item.get("detected_object_type") or "?"
                )
                item_brand = item.get("brand", "")
                item_model = item.get("model", "")
                parts = [item_label]
                if item_brand:
                    parts.append(item_brand)
                if item_model:
                    parts.append(item_model)
                line = " - ".join(parts)
            else:
                line = str(item)
            pdf.multi_cell(0, 6, _s(f"- {line}"), **_NL)
        pdf.ln(2)

    # ── Photos ─────────────────────────────────────────────────
    if not media_dataframe.empty:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Photos", **_NL)
        pdf.ln(2)

        for _, m in media_dataframe.iterrows():
            file_id = m.get("final_drive_file_id") or m.get("temp_drive_file_id")
            if not file_id or str(file_id) in ("nan", "None", ""):
                continue
            try:
                raw = get_drive_image_bytes(str(file_id))
                if not raw:
                    continue
                img = Image.open(io.BytesIO(raw)).convert("RGB")
                # Redimensionnement : largeur max 600px
                max_w = 600
                if img.width > max_w:
                    ratio = max_w / img.width
                    img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=80)
                buf.seek(0)
                # Calcul dimensions PDF (page 180mm de large utile)
                page_w = 180
                img_w  = page_w
                img_h  = img_w * img.height / img.width
                if pdf.get_y() + img_h > pdf.h - 20:
                    pdf.add_page()
                pdf.image(buf, x=15, w=img_w)
                pdf.ln(4)
            except Exception as exc:
                import sys
                print(f"[PDF_IMG] file_id={file_id} → {type(exc).__name__}: {exc}", file=sys.stderr)
                continue  # On continue sans l'image si le téléchargement échoue

    return bytes(pdf.output())


# ─────────────────────────────────────────────────────────────
#  MODALE DÉTAIL ÉQUIPEMENT  (st.dialog — Streamlit ≥ 1.32)
# ─────────────────────────────────────────────────────────────

@st.dialog("Fiche équipement", width="large")
def show_equipment_modal(equipment_id: str):
    """Fenêtre modale avec le détail complet d'un équipement."""
    detail_df = run_query("""
        SELECT *
        FROM equipment
        WHERE equipment_id = ?
    """, [equipment_id])

    if detail_df.empty:
        st.error("Équipement introuvable.")
        return

    row = detail_df.iloc[0]

    media_df = run_query("""
        SELECT final_drive_file_id, temp_drive_file_id, image_role, image_index
        FROM equipment_media
        WHERE equipment_id = ?
        ORDER BY image_index
    """, [equipment_id])

    # En-tête
    header_col, badge_col = st.columns([3, 1])
    with header_col:
        st.markdown(f"### {null_str(row.get('label'), 'Équipement')}")
        st.markdown(f"_{null_str(row.get('brand'))} — {null_str(row.get('model'))}_")
    with badge_col:
        st.markdown(
            condition_badge(row.get("condition_label")) + " " +
            review_badge(row.get("review_required")) + " " +
            confidence_badge(row.get("confidence")),
            unsafe_allow_html=True,
        )

    st.markdown("---")
    left_col, right_col = st.columns([1, 1])

    # ── Photos ────────────────────────────────────────────────
    with left_col:
        zoom_key = f"modal_zoom_{equipment_id}"
        zoomed_fid = st.session_state.get(zoom_key)

        if not media_df.empty:
            if zoomed_fid:
                # ── Vue agrandie (pleine largeur) ─────────────
                try:
                    st.image(get_drive_thumb(zoomed_fid, max_px=1400, quality=90),
                             use_container_width=True)
                except Exception:
                    st.warning(f"⚠️ Image inaccessible (Drive ID : `{zoomed_fid}`)")
                # pas de st.rerun() — le clic garde la modale ouverte
                if st.button("✖ Fermer l'agrandissement", key=f"zoom_close_{equipment_id}",
                             use_container_width=True):
                    st.session_state.pop(zoom_key, None)
            else:
                # ── Galerie ───────────────────────────────────
                st.markdown("**Photos** — *cliquez 🔍 pour agrandir*")
                main = media_df.iloc[0]
                fid  = main.get("final_drive_file_id")
                if fid:
                    try:
                        st.image(get_drive_thumb(fid, max_px=800, quality=80),
                                 use_container_width=True)
                    except Exception:
                        st.warning(f"⚠️ Image corrompue ou inaccessible (Drive ID : `{fid}`)")
                    if st.button("🔍 Agrandir", key=f"zoom_main_{equipment_id}",
                                 use_container_width=True):
                        st.session_state[zoom_key] = fid

                remaining = media_df.iloc[1:]
                for chunk_start in range(0, len(remaining), 3):
                    chunk = remaining.iloc[chunk_start:chunk_start + 3]
                    cols = st.columns(3)
                    for j, (_, m) in enumerate(chunk.iterrows()):
                        fid2 = m.get("final_drive_file_id")
                        if fid2:
                            try:
                                cols[j].image(
                                    get_drive_thumb(fid2, max_px=800, quality=80),
                                    use_container_width=True,
                                    caption=null_str(m.get("image_role")),
                                )
                                if cols[j].button("🔍", key=f"zoom_{equipment_id}_{fid2}",
                                                  use_container_width=True):
                                    st.session_state[zoom_key] = fid2
                            except Exception:
                                cols[j].warning(f"⚠️ Image corrompue (`{fid2}`)")
        else:
            st.info("Aucune image disponible.")

    # ── Détails ───────────────────────────────────────────────
    with right_col:
        st.markdown("**Identification**")
        info_data = {
            "Type"       : null_str(row.get("subtype")),
            "Marque"     : null_str(row.get("brand")),
            "Modèle"     : null_str(row.get("model")),
            "État"       : null_str(row.get("condition_label")),
            "Catégorie"  : null_str(row.get("category")),
            "N° de série": null_str(row.get("serial_number")),
            "Emplacement": null_str(row.get("location_hint")),
            "Mode acquis.": null_str(row.get("ownership_mode")),
            "Prix achat" : (
                f"{row['purchase_price']:.2f} {null_str(row.get('purchase_currency', '€'))}"
                if row.get("purchase_price") and not pd.isna(row["purchase_price"])
                else "—"
            ),
        }
        for k, v in info_data.items():
            st.markdown(f"**{k}** : {v}")

        # Specs techniques
        specs = safe_json(row.get("technical_specs_json"), {})
        if specs:
            st.markdown("---")
            st.markdown("**Spécifications techniques**")
            for k, v in specs.items():
                st.markdown(f"• **{k}** : {v}")

        # Accessoires & consommables
        biz = safe_json(row.get("business_context_json"), {})
        accessories = biz.get("accessories") or biz.get("accessoires") or []
        consumables = biz.get("consumables") or biz.get("consommables") or []
        associated  = biz.get("associated_items") or biz.get("elements_associes") or []

        def _fmt_item(item) -> str:
            """Formate un item (dict ou str) en ligne lisible."""
            if isinstance(item, dict):
                label = item.get("label") or item.get("raw_label") or item.get("detected_object_type") or "?"
                brand = item.get("brand", "")
                model = item.get("model", "")
                condition = item.get("item_condition") or item.get("consumable_state") or ""
                parts = [label]
                if brand:
                    parts.append(brand)
                if model:
                    parts.append(model)
                if condition:
                    parts.append(f"*({condition})*")
                return " · ".join(parts)
            return str(item)

        if accessories:
            st.markdown("---")
            st.markdown("**Accessoires livrés**")
            for a in accessories:
                st.markdown(f"&nbsp;&nbsp;✦ {_fmt_item(a)}")

        if consumables:
            st.markdown("---")
            st.markdown("**Consommables associés**")
            for c in consumables:
                st.markdown(f"&nbsp;&nbsp;⚙ {_fmt_item(c)}")

        if associated:
            st.markdown("---")
            st.markdown("**Éléments associés**")
            for item in associated:
                st.markdown(f"&nbsp;&nbsp;🔗 {_fmt_item(item)}")

        # Notes
        notes = null_str(row.get("notes"))
        if notes != "—":
            st.markdown("---")
            st.info(f"📝 {notes}")

    # ── Disponibilité & Suivi ──────────────────────────────────
    st.markdown("---")
    st.subheader("Disponibilité & Suivi")

    active_mv_df = run_query("""
        SELECT movement_id, movement_type, borrower_name, borrower_contact,
               out_date, expected_return_date, notes
        FROM equipment_movements
        WHERE equipment_id = ? AND actual_return_date IS NULL
        ORDER BY out_date DESC
        LIMIT 1
    """, [equipment_id])

    if active_mv_df.empty:
        # ── Matériel disponible ──────────────────────────────
        st.markdown(
            '<span style="background:#166534;color:#bbf7d0;padding:4px 12px;'
            'border-radius:20px;font-size:0.85rem;font-weight:600">'
            '🟢 Disponible</span>',
            unsafe_allow_html=True,
        )
        st.markdown("")

        with st.form(key=f"checkout_form_{equipment_id}", clear_on_submit=True):
            st.markdown("**Sortir le matériel**")
            mv_type = st.selectbox(
                "Type de sortie",
                options=["LOAN", "RENTAL", "MAINTENANCE"],
                format_func=lambda x: {"LOAN": "🤝 Prêt", "RENTAL": "💶 Location", "MAINTENANCE": "🔧 Maintenance"}[x],
            )
            borrower = st.text_input("Nom de l'emprunteur / destinataire *")
            contact  = st.text_input("Téléphone / Email (optionnel)")
            ret_date = st.date_input("Date de retour prévue", value=None)
            notes_out = st.text_area("Notes", height=80)
            submitted = st.form_submit_button("📤 Confirmer la sortie", use_container_width=True)

        if submitted:
            if not borrower.strip():
                st.error("Le nom de l'emprunteur est obligatoire.")
            else:
                ok = run_write("""
                    INSERT INTO equipment_movements
                        (movement_id, equipment_id, movement_type, borrower_name,
                         borrower_contact, out_date, expected_return_date, notes)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
                """, [
                    str(uuid.uuid4()),
                    equipment_id,
                    mv_type,
                    borrower.strip(),
                    contact.strip() or None,
                    datetime.combine(ret_date, datetime.min.time()) if ret_date else None,
                    notes_out.strip() or None,
                ])
                if ok:
                    st.success(f"✅ Sortie enregistrée pour {borrower.strip()}.")
                    st.rerun()
    else:
        # ── Matériel sorti ───────────────────────────────────
        mv = active_mv_df.iloc[0]
        mv_type_label = {"LOAN": "En prêt", "RENTAL": "En location", "MAINTENANCE": "En maintenance"}.get(
            str(mv.get("movement_type", "")), "Sorti"
        )
        borrower_disp = mv.get("borrower_name") or "?"
        st.markdown(
            f'<span style="background:#7f1d1d;color:#fecaca;padding:4px 12px;'
            f'border-radius:20px;font-size:0.85rem;font-weight:600">'
            f'🔴 {mv_type_label} chez {borrower_disp}</span>',
            unsafe_allow_html=True,
        )
        st.markdown("")

        exp_ret = mv.get("expected_return_date")
        if exp_ret is not None and not (isinstance(exp_ret, float) and pd.isna(exp_ret)):
            try:
                exp_dt = pd.Timestamp(exp_ret)
                if exp_dt < pd.Timestamp.now():
                    st.warning(f"⚠️ Retour prévu le **{exp_dt.strftime('%d/%m/%Y')}** — en retard !")
                else:
                    st.info(f"📅 Retour prévu le **{exp_dt.strftime('%d/%m/%Y')}**")
            except Exception:
                pass

        mv_id = mv.get("movement_id")
        if st.button("🔙 Déclarer le retour", key=f"checkin_btn_{equipment_id}", use_container_width=True):
            ok = run_write("""
                UPDATE equipment_movements
                SET actual_return_date = CURRENT_TIMESTAMP
                WHERE movement_id = ?
            """, [mv_id])
            if ok:
                st.success("✅ Retour enregistré.")
                st.rerun()

    # ── Historique des 5 derniers mouvements ─────────────────
    hist_df = run_query("""
        SELECT
            movement_type     AS "Type",
            borrower_name     AS "Emprunteur",
            strftime(out_date, '%d/%m/%Y')              AS "Sorti le",
            strftime(actual_return_date, '%d/%m/%Y')    AS "Rendu le",
            notes             AS "Notes"
        FROM equipment_movements
        WHERE equipment_id = ? AND actual_return_date IS NOT NULL
        ORDER BY actual_return_date DESC
        LIMIT 5
    """, [equipment_id])

    if not hist_df.empty:
        st.markdown("**Historique des derniers mouvements**")
        hist_df["Type"] = hist_df["Type"].map(
            {"LOAN": "🤝 Prêt", "RENTAL": "💶 Location", "MAINTENANCE": "🔧 Maintenance"}
        ).fillna(hist_df["Type"])
        st.dataframe(hist_df, use_container_width=True, hide_index=True)

    # ── Partager la fiche ──────────────────────────────────────
    st.markdown("---")
    st.subheader("Partager la fiche")

    biz_share = safe_json(row.get("business_context_json"), {})
    share_text = generate_share_text(row, biz_share)
    pdf_bytes  = generate_equipment_pdf(row, media_df, biz_share)

    st.download_button(
        label="📥 Télécharger la Fiche (PDF pour WhatsApp/Mail)",
        data=pdf_bytes,
        file_name=f"fiche_{null_str(row.get('brand'), 'equipement')}_{null_str(row.get('model'), equipment_id)}.pdf".replace(" ", "_"),
        mime="application/pdf",
        use_container_width=True,
    )
    st.code(share_text, language="markdown")

    # Lien Drive + actions selon le rôle
    st.markdown("---")
    folder_url = drive_folder_url(row.get("final_drive_folder_id"))

    if is_admin():
        # Admin : Drive + Modifier + Supprimer
        footer_drive, footer_edit, footer_del = st.columns([2, 1, 1])
        if folder_url:
            footer_drive.markdown(f"[📁 Ouvrir le dossier Drive complet]({folder_url})")
        if footer_edit.button("✏️ Modifier", key=f"edit_btn_{equipment_id}", use_container_width=True):
            st.session_state["edit_equipment_id"] = equipment_id
            st.session_state["edit_return_to"] = st.session_state.get("nav_radio", "🏭 Parc Matériel")
            st.session_state["_nav_request"] = "⚠ Centre de Validation"
            st.toast("⏳ Chargement de la vue modification…")
            st.rerun()
        # Bouton suppression avec double confirmation
        # Note : pas de st.rerun() sur les états intermédiaires — dans un st.dialog,
        # st.rerun() ferme la modale. Le re-render se fait automatiquement au clic.
        del_key = f"confirm_del_modal_{equipment_id}"
        if not st.session_state.get(del_key):
            if footer_del.button("🗑 Supprimer", key=f"del_btn_{equipment_id}", use_container_width=True):
                st.session_state[del_key] = True
        else:
            footer_del.warning("Confirmer ?")
            col_yes, col_no = footer_del.columns(2)
            if col_yes.button("✓", key=f"del_yes_{equipment_id}", use_container_width=True):
                folder_id = null_str(row.get("final_drive_folder_id"), "")
                run_write("DELETE FROM equipment_media WHERE equipment_id = ?", [equipment_id])
                run_write("DELETE FROM equipment WHERE equipment_id = ?", [equipment_id])
                if folder_id and folder_id != "—":
                    call_delete_equipment_webhook(
                        folder_id, equipment_id,
                        null_str(row.get("label"), "Équipement"),
                    )
                st.session_state.pop(del_key, None)
                st.rerun()  # Ferme la modale + rafraîchit le parc après suppression
            if col_no.button("✗", key=f"del_no_{equipment_id}", use_container_width=True):
                st.session_state.pop(del_key, None)
    else:
        # Utilisateur standard : Drive uniquement, lecture seule sur les caractéristiques
        if folder_url:
            st.markdown(f"[📁 Ouvrir le dossier Drive complet]({folder_url})")
        st.caption("🔒 Caractéristiques en lecture seule — contactez l'administrateur pour modifier.")

# ─────────────────────────────────────────────────────────────
#  VUE 3 : PARC MATÉRIEL — Galerie & Recherche
# ─────────────────────────────────────────────────────────────

def render_parc_materiel():
    st.markdown('<p class="section-title">🏭 Parc Matériel</p>', unsafe_allow_html=True)
    st.markdown('<p class="section-subtitle">Recherchez et consultez l\'ensemble du parc outillage</p>', unsafe_allow_html=True)

    # ── Filtres sidebar ────────────────────────────────────────
    with st.sidebar:
        st.markdown("---")
        st.markdown("### Filtres")

        # Récupère les valeurs distinctes pour les filtres
        brands_df    = run_query("SELECT DISTINCT brand FROM equipment WHERE brand IS NOT NULL ORDER BY brand")
        subtypes_df  = run_query("SELECT DISTINCT subtype FROM equipment WHERE subtype IS NOT NULL ORDER BY subtype")
        conds_df     = run_query("SELECT DISTINCT condition_label FROM equipment WHERE condition_label IS NOT NULL ORDER BY condition_label")

        brands_list    = brands_df["brand"].tolist()       if not brands_df.empty else []
        subtypes_list  = subtypes_df["subtype"].tolist()   if not subtypes_df.empty else []
        conds_list     = conds_df["condition_label"].tolist() if not conds_df.empty else []

        sel_brands   = st.multiselect("Marque",  brands_list,   key="filter_brand")
        sel_subtypes = st.multiselect("Type",    subtypes_list, key="filter_subtype")
        sel_conds    = st.multiselect("État",    conds_list,    key="filter_cond")

        show_review_only = st.checkbox("⚠ À réviser seulement", value=False, key="filter_review")

    # ── Barre de recherche ─────────────────────────────────────
    search = st.text_input(
        "🔍 Recherche libre (nom, marque, modèle, N° série…)",
        placeholder="Ex: Bosch, meuleuse, perceuse, SN-12345…",
        key="search_parc",
    )

    # ── Construction de la requête dynamique ───────────────────
    conditions = ["1=1"]
    params     = []

    if search:
        conditions.append("""(
            LOWER(label)         LIKE ?
            OR LOWER(brand)      LIKE ?
            OR LOWER(model)      LIKE ?
            OR LOWER(serial_number) LIKE ?
            OR LOWER(subtype)    LIKE ?
            OR LOWER(notes)      LIKE ?
        )""")
        like_val = f"%{search.lower()}%"
        params.extend([like_val] * 6)

    if sel_brands:
        placeholders = ", ".join(["?"] * len(sel_brands))
        conditions.append(f"brand IN ({placeholders})")
        params.extend(sel_brands)

    if sel_subtypes:
        placeholders = ", ".join(["?"] * len(sel_subtypes))
        conditions.append(f"subtype IN ({placeholders})")
        params.extend(sel_subtypes)

    if sel_conds:
        placeholders = ", ".join(["?"] * len(sel_conds))
        conditions.append(f"condition_label IN ({placeholders})")
        params.extend(sel_conds)

    if show_review_only:
        conditions.append("review_required = true")

    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT
            e.equipment_id, e.label, e.brand, e.model, e.serial_number,
            e.subtype, e.condition_label, e.confidence, e.review_required,
            e.location_hint, e.received_at,
            (
                SELECT em.final_drive_file_id
                FROM equipment_media em
                WHERE em.equipment_id = e.equipment_id
                ORDER BY
                    CASE em.image_role
                        WHEN 'overview'  THEN 1
                        WHEN 'nameplate' THEN 2
                        ELSE 3
                    END
                LIMIT 1
            ) AS main_file_id
        FROM equipment e
        WHERE {where_clause}
        ORDER BY e.received_at DESC
    """
    results_df = run_query(sql, params if params else None)

    # ── Compteur résultats ─────────────────────────────────────
    nb = len(results_df)
    if search or sel_brands or sel_subtypes or sel_conds or show_review_only:
        st.caption(f"{nb} résultat(s) trouvé(s)")
    else:
        st.caption(f"{nb} équipement(s) dans le parc")

    if results_df.empty:
        st.info("Aucun équipement ne correspond à votre recherche.")
        return

    # ── Pagination : 20 items par page, chargés progressivement ─
    PAGE_SIZE = 20
    _page_key = "parc_page"
    # Réinitialise la pagination quand les filtres changent
    _filter_sig = f"{search}|{sel_brands}|{sel_subtypes}|{sel_conds}|{show_review_only}"
    if st.session_state.get("_parc_filter_sig") != _filter_sig:
        st.session_state[_page_key] = 1
        st.session_state["_parc_filter_sig"] = _filter_sig
    current_page = st.session_state.get(_page_key, 1)
    shown = current_page * PAGE_SIZE
    display_df = results_df.iloc[:shown]

    # ── Affichage en grille 5 colonnes ─────────────────────────
    COLS = 5
    rows = [display_df.iloc[i:i+COLS] for i in range(0, len(display_df), COLS)]

    for chunk in rows:
        cols = st.columns(COLS)
        for col_idx, (_, item) in enumerate(chunk.iterrows()):
            with cols[col_idx]:
                # Photo miniature (800px, JPEG q=80) : affichage petit en carte,
                # qualité suffisante pour le mode Fullscreen natif Streamlit
                file_id = item.get("main_file_id")
                if file_id and str(file_id) not in ("nan", "None", ""):
                    thumb = get_drive_thumb(file_id, max_px=800, quality=80)
                    if thumb:
                        st.image(thumb, use_container_width=True)
                    else:
                        st.markdown(
                            '<div style="background:#1e293b;border-radius:6px;height:90px;'
                            'display:flex;align-items:center;justify-content:center;'
                            'color:#475569;font-size:1.4rem;">📷</div>',
                            unsafe_allow_html=True,
                        )
                else:
                    st.markdown(
                        '<div style="background:#1e293b;border-radius:6px;height:90px;'
                        'display:flex;align-items:center;justify-content:center;'
                        'color:#475569;font-size:1.4rem;">📷</div>',
                        unsafe_allow_html=True,
                    )

                # Infos carte (taille réduite)
                brand = null_str(item.get("brand"))
                model = null_str(item.get("model"))
                label = null_str(item.get("label"), "Équipement sans nom")

                st.markdown(
                    f"**{label}**  \n"
                    f"<span style='color:#94a3b8;font-size:0.82rem'>{brand} · {model}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    condition_badge(item.get("condition_label")) + "&nbsp;" +
                    confidence_badge(item.get("confidence")),
                    unsafe_allow_html=True,
                )

                # Boutons action : Détails | 🧺 Kit | 📤 Sortie
                eid = item["equipment_id"]
                in_kit  = eid in st.session_state.get("kit_basket",  {})
                in_loan = eid in st.session_state.get("loan_basket", {})

                def _display_name(it):
                    return (
                        null_str(it.get("label"), "")
                        or " ".join(filter(None, [
                            null_str(it.get("brand"), ""),
                            null_str(it.get("model"), ""),
                        ]))
                        or it["equipment_id"]
                    )

                btn_col, kit_col, loan_col = st.columns([3, 1, 1])
                with btn_col:
                    if st.button("Voir", key=f"detail_{eid}", use_container_width=True):
                        show_equipment_modal(eid)
                with kit_col:
                    if st.button(
                        "✓" if in_kit else "🧺",
                        key=f"basket_{eid}", use_container_width=True,
                        help="Retirer du panier Kit" if in_kit else "Ajouter au panier Kit",
                    ):
                        if in_kit:
                            del st.session_state["kit_basket"][eid]
                        else:
                            st.session_state["kit_basket"][eid] = _display_name(item)
                        st.rerun()
                with loan_col:
                    if st.button(
                        "✓" if in_loan else "📤",
                        key=f"loan_{eid}", use_container_width=True,
                        help="Retirer de la sortie groupée" if in_loan else "Ajouter à la sortie groupée",
                    ):
                        if in_loan:
                            del st.session_state["loan_basket"][eid]
                        else:
                            st.session_state["loan_basket"][eid] = _display_name(item)
                        st.rerun()

                # Bouton validation rapide (visible uniquement si review_required)
                if is_admin() and item.get("review_required"):
                    if st.button("⚡ Valider", key=f"qval_{eid}", use_container_width=True,
                                 help="Valider cette fiche directement sans la modifier"):
                        ok = run_write(
                            "UPDATE equipment SET review_required = false WHERE equipment_id = ?", [eid]
                        )
                        if ok:
                            run_write("""
                                INSERT INTO equipment_audit
                                    (audit_id, equipment_id, action, changed_fields, operator)
                                VALUES (?, ?, 'VALIDATE', 'review_required', ?)
                            """, [str(uuid.uuid4()), eid, get_current_user()])
                            st.rerun()

                st.markdown("<div style='margin-bottom:12px'></div>", unsafe_allow_html=True)

    # ── Bouton "Charger la suite" ───────────────────────────────
    if shown < nb:
        remaining = nb - shown
        if st.button(
            f"⬇ Charger la suite ({remaining} équipement(s) restant(s))",
            key="parc_load_more",
            use_container_width=True,
        ):
            st.session_state[_page_key] = current_page + 1
            st.rerun()
    else:
        st.caption(f"✓ Tous les {nb} équipements affichés.")

# ─────────────────────────────────────────────────────────────
#  VUE 4 : JOURNAL DES ACCÈS (logs Traefik)
# ─────────────────────────────────────────────────────────────

# Nom du conteneur Traefik (ajustez si différent)
TRAEFIK_CONTAINER = "root-traefik-1"
# Chemin alternatif si les logs sont montés en fichier
TRAEFIK_LOG_FILE  = "/var/log/traefik/access.log"

# Regex Apache Combined Log Format
_CLF_RE = re.compile(
    r'(?P<ip>\S+)\s+-\s+(?P<user>\S+)\s+\[(?P<date>[^\]]+)\]\s+'
    r'"(?P<method>\S+)\s+(?P<path>\S+)\s+\S+"\s+(?P<status>\d{3})\s+(?P<size>\S+)'
)


def _read_traefik_logs(n: int) -> list[str] | None:
    """Retourne les N dernières lignes du fichier access.log de Traefik."""
    log_path = Path(TRAEFIK_LOG_FILE)
    if log_path.exists():
        try:
            all_lines = log_path.read_text(errors="replace").splitlines()
            return all_lines[-n:] if len(all_lines) > n else all_lines
        except OSError:
            pass
    return None


def _parse_access_log(lines: list[str]) -> list[dict]:
    """Parse les lignes CLF en liste de dicts."""
    entries = []
    for line in lines:
        m = _CLF_RE.search(line)
        if not m:
            continue
        # Conversion heure UTC → Paris
        try:
            ts = pd.to_datetime(m.group("date"), format="%d/%b/%Y:%H:%M:%S %z")
            ts_paris = ts.tz_convert(_PARIS_TZ)
            date_str = ts_paris.strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            date_str = m.group("date")

        path = m.group("path")
        # Filtre le bruit (health-checks, fichiers statiques)
        # /_stcore/stream est conservé : c'est la connexion WebSocket de l'utilisateur
        if path.startswith("/_stcore/") and path != "/_stcore/stream":
            continue
        if any(path.startswith(p) for p in ("/static", "/healthz", "/favicon")):
            continue

        entries.append({
            "Heure":    date_str,
            "IP":       m.group("ip"),
            "Utilisateur": m.group("user"),
            "Chemin":   path[:80],
            "Méthode":  m.group("method"),
            "Statut":   int(m.group("status")),
        })
    return entries


def _status_badge(code: int) -> str:
    if code < 300:
        color = "#22c55e"
    elif code < 400:
        color = "#3b82f6"
    elif code == 401:
        color = "#ef4444"
    elif code < 500:
        color = "#f97316"
    else:
        color = "#dc2626"
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:.8rem;font-weight:600">{code}</span>'


def render_access_log():
    if not is_admin():
        st.error("🔒 Accès réservé à l'administrateur.")
        return
    st.markdown('<p class="section-title">🔒 Journal des Accès</p>', unsafe_allow_html=True)
    st.markdown('<p class="section-subtitle">Connexions Traefik · Historique des modifications</p>',
                unsafe_allow_html=True)

    tab_traefik, tab_audit = st.tabs(["🌐 Accès réseau (Traefik)", "📝 Audit fiches équipement"])

    with tab_audit:
        audit_df = run_query("""
            SELECT
                strftime(a.changed_at, '%d/%m/%Y %H:%M') AS "Horodatage",
                a.operator                                AS "Opérateur",
                a.action                                  AS "Action",
                COALESCE(NULLIF(e.label,''),
                         e.brand || ' ' || e.model,
                         a.equipment_id)                  AS "Équipement",
                a.changed_fields                          AS "Champs modifiés",
                e.condition_label                         AS "État"
            FROM equipment_audit a
            LEFT JOIN equipment e ON e.equipment_id = a.equipment_id
            ORDER BY a.changed_at DESC
            LIMIT 500
        """)
        if audit_df.empty:
            st.info("Aucune modification enregistrée pour l'instant.")
        else:
            st.caption(f"{len(audit_df)} entrée(s) — 500 dernières")
            st.dataframe(audit_df, use_container_width=True, hide_index=True,
                         column_config={
                             "Horodatage": st.column_config.TextColumn(width="small"),
                             "Opérateur":  st.column_config.TextColumn(width="small"),
                             "Action":     st.column_config.TextColumn(width="small"),
                         })

    with tab_traefik:
        # ── Options ───────────────────────────────────────────────
        col_a, col_b, col_c, col_d, col_e = st.columns([2, 2, 2, 2, 1])
        with col_a:
            nb_lines = st.select_slider(
                "Dernières lignes", options=[50, 100, 200, 500, 1000], value=200,
            )
        with col_b:
            only_logins = st.checkbox("Connexions uniquement", value=True)
        with col_c:
            only_401 = st.checkbox("Seulement les refus (401)")
        with col_d:
            search_ip = st.text_input("Filtrer par IP", placeholder="176.152…")
        with col_e:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🔄 Actualiser"):
                st.rerun()

        # ── Lecture ───────────────────────────────────────────────
        raw = _read_traefik_logs(nb_lines)

        # ── Infos fichier (diagnostic) ────────────────────────────
        log_path = Path(TRAEFIK_LOG_FILE)
        if log_path.exists():
            import os, datetime as _dt
            import zoneinfo as _zi
            mtime = _dt.datetime.fromtimestamp(os.path.getmtime(log_path), tz=_zi.ZoneInfo("Europe/Paris"))
            size_kb = os.path.getsize(log_path) / 1024
            st.caption(
                f"Fichier : `{TRAEFIK_LOG_FILE}` — "
                f"{size_kb:.1f} Ko — "
                f"dernière écriture : **{mtime.strftime('%d/%m/%Y %H:%M:%S')}**"
            )

        if raw is None:
            st.error(
                "Impossible de lire les logs Traefik. "
                "Deux options :\n\n"
                "**Option A — Docker socket** : montez `/var/run/docker.sock` "
                "dans le conteneur Streamlit.\n\n"
                "**Option B — Fichier log** : ajoutez dans votre `docker-compose.yml` Traefik :\n"
                "```yaml\n"
                "command:\n"
                "  - --accesslog=true\n"
                "  - --accesslog.filepath=/var/log/traefik/access.log\n"
                "volumes:\n"
                "  - traefik_logs:/var/log/traefik\n"
                "```\n"
                f"et montez le même volume dans le conteneur SIGA à `{TRAEFIK_LOG_FILE}`."
            )
            return  # early-return inside tab only shows nothing, acceptable

        entries = _parse_access_log(raw)

        # ── Filtres ───────────────────────────────────────────────
        if only_logins:
            entries = [e for e in entries if e["Utilisateur"] != "-" or e["Statut"] == 401]
        if only_401:
            entries = [e for e in entries if e["Statut"] == 401]
        if search_ip.strip():
            entries = [e for e in entries if search_ip.strip() in e["IP"]]

        if not entries:
            st.info("Aucune entrée correspondant aux filtres.")
        else:
            # ── KPIs ──────────────────────────────────────────────────
            nb_401    = sum(1 for e in entries if e["Statut"] == 401)
            nb_ok     = sum(1 for e in entries if 200 <= e["Statut"] < 300)
            unique_ip = len({e["IP"] for e in entries})
            users     = {e["Utilisateur"] for e in entries if e["Utilisateur"] != "-"}

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Connexions affichées" if only_logins else "Requêtes affichées", len(entries))
            k2.metric("Connexions réussies" if only_logins else "Accès autorisés (2xx)", nb_ok)
            k3.metric("Refus 401", nb_401)
            k4.metric("IPs uniques", unique_ip)

            if users:
                st.caption(f"Utilisateurs identifiés : {', '.join(sorted(users))}")

            st.markdown("---")

            # ── Tableau ───────────────────────────────────────────────
            rows_html = ""
            for e in reversed(entries):   # plus récent en haut
                badge = _status_badge(e["Statut"])
                user  = e["Utilisateur"] if e["Utilisateur"] != "-" else '<span style="color:#94a3b8">—</span>'
                rows_html += (
                    f"<tr>"
                    f"<td style='color:#94a3b8;font-size:.82rem'>{e['Heure']}</td>"
                    f"<td><code style='font-size:.82rem'>{e['IP']}</code></td>"
                    f"<td>{user}</td>"
                    f"<td><code style='font-size:.82rem'>{e['Chemin']}</code></td>"
                    f"<td>{badge}</td>"
                    f"</tr>"
                )

            st.markdown(
                "<table style='width:100%;border-collapse:collapse'>"
                "<thead><tr style='border-bottom:1px solid #334155;color:#94a3b8;font-size:.8rem'>"
                "<th align='left'>Heure</th><th align='left'>IP</th>"
                "<th align='left'>Utilisateur</th><th align='left'>Chemin</th>"
                "<th align='left'>Statut</th>"
                "</tr></thead>"
                f"<tbody>{rows_html}</tbody>"
                "</table>",
                unsafe_allow_html=True,
            )


# ─────────────────────────────────────────────────────────────
#  VUE : SUIVI DES MOUVEMENTS
# ─────────────────────────────────────────────────────────────

def render_suivi_mouvements():
    st.markdown('<p class="section-title">📦 Suivi des Mouvements</p>', unsafe_allow_html=True)
    st.markdown('<p class="section-subtitle">Prêts, locations et envois en maintenance</p>',
                unsafe_allow_html=True)

    # ── Trigger dialog check-in kit (doit être en haut du render) ──
    if "pending_checkin_batch_id" in st.session_state:
        bid   = st.session_state.pop("pending_checkin_batch_id")
        bname = st.session_state.pop("pending_checkin_borrower", "?")
        checkin_kit_dialog(bid, bname)

    # ── KPIs ───────────────────────────────────────────────
    kpi_df = run_query("""
        SELECT
            COUNT(*)                                                        AS total_out,
            COUNT(CASE WHEN expected_return_date < CURRENT_TIMESTAMP THEN 1 END) AS total_late
        FROM equipment_movements
        WHERE actual_return_date IS NULL
    """)
    total_out  = int(kpi_df.iloc[0]["total_out"])  if not kpi_df.empty else 0
    total_late = int(kpi_df.iloc[0]["total_late"]) if not kpi_df.empty else 0

    k1, k2 = st.columns(2)
    k1.metric("📤 Matériels actuellement sortis", total_out)
    k2.metric("⚠️ Retards", total_late,
              delta=f"-{total_late}" if total_late else None,
              delta_color="inverse" if total_late else "off")

    st.markdown("---")

    # ── Sortir un Kit (formulaire checkout groupé) ─────────
    with st.expander("🧰 Sortir un Kit entier", expanded=False):
        kits_df = run_query("SELECT kit_id, name FROM kits ORDER BY created_at DESC")
        if kits_df.empty:
            st.info("Aucun kit disponible. Créez-en un dans la page 🧰 Gestion des Kits.")
        else:
            kit_labels = kits_df["name"].tolist()
            kit_ids    = kits_df["kit_id"].tolist()
            with st.form("kit_checkout_form", clear_on_submit=True):
                sel_kit_idx  = st.selectbox("Kit à sortir", range(len(kit_labels)),
                                            format_func=lambda i: kit_labels[i])
                borrower_kit = st.text_input("Nom de l'emprunteur *")
                contact_kit  = st.text_input("Téléphone / Email (optionnel)")
                ret_date_kit = st.date_input("Date de retour prévue", value=None)
                notes_kit    = st.text_area("Notes", height=60)
                mv_type_kit  = st.selectbox(
                    "Type",
                    ["LOAN", "RENTAL", "MAINTENANCE"],
                    format_func=lambda x: {"LOAN": "🤝 Prêt", "RENTAL": "💶 Location",
                                           "MAINTENANCE": "🔧 Maintenance"}[x],
                )
                submitted_kit = st.form_submit_button("📤 Sortir le Kit", use_container_width=True)

            if submitted_kit:
                if not borrower_kit.strip():
                    st.error("Le nom de l'emprunteur est obligatoire.")
                else:
                    sel_kit_id = kit_ids[sel_kit_idx]
                    items_df = run_query(
                        "SELECT equipment_id FROM kit_items WHERE kit_id = ?", [sel_kit_id]
                    )
                    if items_df.empty:
                        st.warning("Ce kit ne contient aucun outil. Composez-le d'abord.")
                    else:
                        batch_id = str(uuid.uuid4())
                        exp_dt   = (datetime.combine(ret_date_kit, datetime.min.time())
                                    if ret_date_kit else None)
                        errors = 0
                        for eid in items_df["equipment_id"].tolist():
                            ok = run_write("""
                                INSERT INTO equipment_movements
                                    (movement_id, equipment_id, movement_type,
                                     borrower_name, borrower_contact,
                                     out_date, expected_return_date, notes,
                                     batch_id, kit_id)
                                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?)
                            """, [str(uuid.uuid4()), eid, mv_type_kit,
                                  borrower_kit.strip(), contact_kit.strip() or None,
                                  exp_dt, notes_kit.strip() or None,
                                  batch_id, sel_kit_id])
                            if not ok:
                                errors += 1
                        if errors == 0:
                            st.success(
                                f"✅ {len(items_df)} outil(s) du kit **{kit_labels[sel_kit_idx]}** "
                                f"sortis pour **{borrower_kit.strip()}**."
                            )
                            st.rerun()
                        else:
                            st.error(f"{errors} insertion(s) échouée(s).")

    st.markdown("---")

    # ── Requête enrichie des mouvements actifs ─────────────
    out_df = run_query("""
        SELECT
            m.movement_id,
            m.equipment_id,
            m.batch_id,
            m.kit_id,
            k.name                                               AS kit_name,
            COALESCE(NULLIF(e.label,''),
                     e.brand || ' ' || e.model,
                     m.equipment_id)                             AS eq_label,
            e.brand                                              AS eq_brand,
            e.model                                              AS eq_model,
            e.subtype                                            AS eq_subtype,
            e.condition_label                                    AS eq_condition,
            pm.file_id                                           AS photo_id,
            m.movement_type,
            m.borrower_name,
            m.borrower_contact,
            m.out_date,
            m.expected_return_date,
            m.notes
        FROM equipment_movements m
        JOIN equipment e ON e.equipment_id = m.equipment_id
        LEFT JOIN kits k ON k.kit_id = m.kit_id
        LEFT JOIN (
            SELECT equipment_id,
                   first(final_drive_file_id ORDER BY image_index) AS file_id
            FROM equipment_media GROUP BY equipment_id
        ) pm ON pm.equipment_id = m.equipment_id
        WHERE m.actual_return_date IS NULL
        ORDER BY m.batch_id NULLS LAST, m.out_date DESC
    """)

    if out_df.empty:
        st.success("✅ Aucun matériel actuellement sorti.")
        return

    TYPE_LABELS = {"LOAN": "🤝 Prêt", "RENTAL": "💶 Location", "MAINTENANCE": "🔧 Maintenance"}
    now = pd.Timestamp.now()

    def _exp_info(ts):
        if ts is None or (isinstance(ts, float) and pd.isna(ts)):
            return False, "-"
        try:
            dt = pd.Timestamp(ts)
            return dt < now, dt.strftime("%d/%m/%Y")
        except Exception:
            return False, "-"

    def _out_str(ts):
        if ts is None or (isinstance(ts, float) and pd.isna(ts)):
            return "-"
        try:
            return pd.Timestamp(ts).strftime("%d/%m/%Y")
        except Exception:
            return "-"

    def _render_photo(fid):
        if fid and str(fid) not in ("nan", "None", ""):
            img = drive_img_src(str(fid), 120)
            if img:
                st.image(img, use_container_width=True)
                return
        st.markdown(
            '<div style="background:#0f172a;border-radius:6px;'
            'text-align:center;padding:18px;font-size:1.6rem">📷</div>',
            unsafe_allow_html=True,
        )

    # ── Sépare mouvements individuels / kits ───────────────
    individual = out_df[out_df["batch_id"].isna() | (out_df["batch_id"] == "")]
    kit_groups = out_df[out_df["batch_id"].notna() & (out_df["batch_id"] != "")]

    # ── Cartes mouvements individuels ──────────────────────
    for _, row in individual.iterrows():
        is_late, exp_str = _exp_info(row.get("expected_return_date"))
        border = "#dc2626" if is_late else "#334155"
        st.markdown(
            f'<div style="border:1px solid {border};border-radius:10px;'
            f'padding:12px 16px;margin-bottom:10px;background:#1e293b">',
            unsafe_allow_html=True,
        )
        col_photo, col_info, col_move = st.columns([1, 3, 2])
        with col_photo:
            _render_photo(row.get("photo_id"))
        with col_info:
            label      = row.get("eq_label") or "-"
            brand_mod  = " ".join(filter(None, [row.get("eq_brand",""), row.get("eq_model","")])) or "-"
            sub        = row.get("eq_subtype") or ""
            cond       = row.get("eq_condition") or ""
            st.markdown(
                f"**{label}**  \n"
                f"<span style='color:#94a3b8;font-size:0.82rem'>{brand_mod}"
                f"{'  ·  '+sub if sub else ''}{'  ·  '+cond if cond else ''}</span>",
                unsafe_allow_html=True,
            )
        with col_move:
            mv_type     = TYPE_LABELS.get(str(row.get("movement_type","")), "")
            borrower    = row.get("borrower_name") or "-"
            contact     = row.get("borrower_contact") or ""
            notes       = row.get("notes") or ""
            late_badge  = (' <span style="background:#7f1d1d;color:#fecaca;padding:1px 7px;'
                           'border-radius:10px;font-size:0.75rem">⚠️ Retard</span>'
                           if is_late else "")
            contact_nl  = ("  \n📞 " + contact) if contact else ""
            notes_nl    = ("  \n📝 " + notes)   if notes   else ""
            st.markdown(
                f"{mv_type} · **{borrower}**{contact_nl}  \n"
                f"<span style='color:#94a3b8;font-size:0.8rem'>"
                f"Sorti le {_out_str(row.get('out_date'))} · Retour prévu {exp_str}"
                f"</span>{late_badge}{notes_nl}",
                unsafe_allow_html=True,
            )
            mv_id = row.get("movement_id")
            if st.button("🔙 Retour", key=f"ret_{mv_id}", use_container_width=True):
                ok = run_write("""
                    UPDATE equipment_movements
                    SET actual_return_date = CURRENT_TIMESTAMP
                    WHERE movement_id = ?
                """, [mv_id])
                if ok:
                    st.success(f"✅ Retour enregistré pour {borrower}.")
                    st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ── Cartes kit (groupées par batch_id) ─────────────────
    if not kit_groups.empty:
        for batch_id, batch in kit_groups.groupby("batch_id", sort=False):
            first_row = batch.iloc[0]
            is_late_any = any(
                _exp_info(r.get("expected_return_date"))[0]
                for _, r in batch.iterrows()
            )
            border    = "#dc2626" if is_late_any else "#7c3aed"
            kit_name  = first_row.get("kit_name") or "Kit"
            borrower  = first_row.get("borrower_name") or "-"
            contact   = first_row.get("borrower_contact") or ""
            mv_type   = TYPE_LABELS.get(str(first_row.get("movement_type","")), "")
            _, exp_str = _exp_info(first_row.get("expected_return_date"))
            n_items   = len(batch)

            st.markdown(
                f'<div style="border:1px solid {border};border-radius:10px;'
                f'padding:12px 16px;margin-bottom:10px;background:#1e293b">',
                unsafe_allow_html=True,
            )
            header_c, action_c = st.columns([4, 1])
            with header_c:
                late_badge = (' <span style="background:#7f1d1d;color:#fecaca;padding:1px 7px;'
                              'border-radius:10px;font-size:0.75rem">⚠️ Retard</span>'
                              if is_late_any else "")
                kit_contact_part = ("  ·  📞 " + contact) if contact else ""
                st.markdown(
                    f'<span style="background:#4c1d95;color:#ddd6fe;padding:2px 9px;'
                    f'border-radius:12px;font-size:0.78rem">🧰 Kit</span> '
                    f"**{kit_name}** — {n_items} outil(s)  \n"
                    f"{mv_type} · **{borrower}**{kit_contact_part}  \n"
                    f"<span style='color:#94a3b8;font-size:0.8rem'>"
                    f"Sorti le {_out_str(first_row.get('out_date'))} · "
                    f"Retour prévu {exp_str}</span>{late_badge}",
                    unsafe_allow_html=True,
                )
            with action_c:
                if st.button("🔙 Retour Kit", key=f"kit_ret_{batch_id}",
                             use_container_width=True):
                    st.session_state["pending_checkin_batch_id"]  = batch_id
                    st.session_state["pending_checkin_borrower"]  = borrower
                    st.rerun()

            # Miniatures des outils du kit
            COLS = 4
            photos = batch.to_dict("records")
            for i in range(0, len(photos), COLS):
                chunk = photos[i:i + COLS]
                cols  = st.columns(len(chunk))
                for j, item in enumerate(chunk):
                    with cols[j]:
                        fid = item.get("photo_id")
                        if fid and str(fid) not in ("nan", "None", ""):
                            img = drive_img_src(str(fid), 100)
                            if img:
                                st.image(img, use_container_width=True)
                        st.caption(item.get("eq_label") or "?")
            st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
#  DIALOG : RETOUR DE KIT (CHECKLIST)
# ─────────────────────────────────────────────────────────────

@st.dialog("Retour de Kit — Checklist", width="large")
def checkin_kit_dialog(batch_id: str, borrower_name: str) -> None:
    """Modale de retour groupé : cocher les outils rendus, décocher ceux manquants."""
    mv_df = run_query("""
        SELECT
            m.movement_id,
            COALESCE(e.label, e.brand || ' ' || e.model, m.equipment_id) AS eq_name,
            e.condition_label,
            pm.file_id AS photo_id
        FROM equipment_movements m
        JOIN equipment e ON e.equipment_id = m.equipment_id
        LEFT JOIN (
            SELECT equipment_id,
                   first(final_drive_file_id ORDER BY image_index) AS file_id
            FROM equipment_media GROUP BY equipment_id
        ) pm ON pm.equipment_id = m.equipment_id
        WHERE m.batch_id = ? AND m.actual_return_date IS NULL
        ORDER BY eq_name
    """, [batch_id])

    if mv_df.empty:
        st.info("Tous les outils de ce lot ont déjà été retournés.")
        return

    st.markdown(f"**Emprunteur :** {borrower_name}  \n"
                f"Cochez les outils rendus, décochez les manquants :")
    st.markdown("---")

    checked: dict[str, bool] = {}
    for _, mv in mv_df.iterrows():
        col_img, col_chk = st.columns([1, 5])
        with col_img:
            fid = mv.get("photo_id")
            if fid and str(fid) not in ("nan", "None", ""):
                img = drive_img_src(str(fid), 80)
                if img:
                    st.image(img, use_container_width=True)
                else:
                    st.markdown("📷")
            else:
                st.markdown("📷")
        with col_chk:
            name  = mv.get("eq_name") or "?"
            cond  = mv.get("condition_label") or ""
            label = f"{name}" + (f" · *{cond}*" if cond else "")
            checked[mv["movement_id"]] = st.checkbox(
                label, value=True, key=f"chk_{mv['movement_id']}"
            )

    st.markdown("---")
    if st.button("✅ Valider le retour", type="primary", use_container_width=True):
        returned = [mid for mid, c in checked.items() if c]
        missing  = len(checked) - len(returned)
        for mid in returned:
            run_write("""
                UPDATE equipment_movements
                SET actual_return_date = CURRENT_TIMESTAMP
                WHERE movement_id = ?
            """, [mid])
        if missing:
            st.warning(f"✅ {len(returned)} outil(s) retourné(s). "
                       f"⚠️ {missing} outil(s) manquant(s) restent en cours.")
        else:
            st.success(f"✅ {len(returned)} outil(s) retourné(s). Kit soldé.")
        st.rerun()


# ─────────────────────────────────────────────────────────────
#  VUE : GESTION DES KITS
# ─────────────────────────────────────────────────────────────

def render_gestion_kits() -> None:
    st.markdown('<p class="section-title">🧰 Gestion des Kits</p>', unsafe_allow_html=True)
    st.markdown('<p class="section-subtitle">Créez et composez vos caisses à outils</p>',
                unsafe_allow_html=True)

    tab_create, tab_compose = st.tabs(["➕ Créer un Kit", "🔧 Composer / Modifier"])

    # ── Créer un kit ─────────────────────────────────────────
    with tab_create:
        with st.form("create_kit_form", clear_on_submit=True):
            kit_name = st.text_input("Nom du Kit *  (ex : Caisse Plomberie 1)")
            kit_desc = st.text_area("Description", height=80)
            submitted = st.form_submit_button("➕ Créer le Kit", use_container_width=True)
        if submitted:
            if not kit_name.strip():
                st.error("Le nom est obligatoire.")
            else:
                ok = run_write(
                    "INSERT INTO kits (kit_id, name, description) VALUES (?, ?, ?)",
                    [str(uuid.uuid4()), kit_name.strip(), kit_desc.strip() or None],
                )
                if ok:
                    st.success(f"✅ Kit **{kit_name.strip()}** créé.")
                    st.rerun()

    # ── Composer un kit ──────────────────────────────────────
    with tab_compose:
        kits_df = run_query("SELECT kit_id, name, description FROM kits ORDER BY created_at DESC")
        if kits_df.empty:
            st.info("Aucun kit existant. Créez-en un dans l'onglet ➕.")
            return

        kit_labels = kits_df["name"].tolist()
        kit_ids    = kits_df["kit_id"].tolist()
        sel_idx    = st.selectbox("Sélectionner un Kit", range(len(kit_labels)),
                                  format_func=lambda i: kit_labels[i], key="kit_compose_sel")
        sel_kit_id = kit_ids[sel_idx]

        kit_desc_val = kits_df.iloc[sel_idx]["description"] or ""
        if kit_desc_val:
            st.caption(kit_desc_val)

        # Équipements disponibles
        all_eq_df = run_query("""
            SELECT equipment_id,
                   COALESCE(NULLIF(label,''), brand || ' ' || model, equipment_id) AS dname
            FROM equipment
            ORDER BY dname
        """)

        # Contenu actuel du kit
        current_df = run_query(
            "SELECT equipment_id FROM kit_items WHERE kit_id = ?", [sel_kit_id]
        )
        current_ids = set(current_df["equipment_id"].tolist()) if not current_df.empty else set()

        eq_by_name  = dict(zip(all_eq_df["dname"], all_eq_df["equipment_id"]))
        default_sel = [n for n, eid in eq_by_name.items() if eid in current_ids]

        sel_names = st.multiselect(
            "Équipements composant ce kit",
            options=list(eq_by_name.keys()),
            default=default_sel,
        )

        col_save, col_del = st.columns([3, 1])
        with col_save:
            if st.button("💾 Mettre à jour le contenu", use_container_width=True):
                sel_ids = [eq_by_name[n] for n in sel_names]
                ok = run_write("DELETE FROM kit_items WHERE kit_id = ?", [sel_kit_id])
                for eid in sel_ids:
                    ok = ok and run_write(
                        "INSERT OR IGNORE INTO kit_items (kit_id, equipment_id) VALUES (?, ?)",
                        [sel_kit_id, eid],
                    )
                if ok:
                    st.success(f"✅ Kit mis à jour ({len(sel_ids)} outil(s)).")
                    st.rerun()
        with col_del:
            if st.button("🗑 Supprimer ce kit", use_container_width=True):
                run_write("DELETE FROM kit_items WHERE kit_id = ?", [sel_kit_id])
                run_write("DELETE FROM kits WHERE kit_id = ?", [sel_kit_id])
                st.success("Kit supprimé.")
                st.rerun()

        # ── Aperçu visuel ─────────────────────────────────
        if current_ids:
            st.markdown("---")
            st.markdown(f"**Contenu du Kit — {len(current_ids)} outil(s)**")
            preview_df = run_query("""
                SELECT e.label, e.brand, e.model, e.subtype, e.condition_label,
                       pm.file_id AS photo_id
                FROM kit_items ki
                JOIN equipment e ON e.equipment_id = ki.equipment_id
                LEFT JOIN (
                    SELECT equipment_id,
                           first(final_drive_file_id ORDER BY image_index) AS file_id
                    FROM equipment_media GROUP BY equipment_id
                ) pm ON pm.equipment_id = e.equipment_id
                WHERE ki.kit_id = ?
                ORDER BY COALESCE(NULLIF(e.label,''), e.brand, e.equipment_id)
            """, [sel_kit_id])

            COLS = 3
            for i in range(0, len(preview_df), COLS):
                chunk = preview_df.iloc[i:i + COLS]
                cols  = st.columns(COLS)
                for j, (_, item) in enumerate(chunk.iterrows()):
                    with cols[j]:
                        fid = item.get("photo_id")
                        if fid and str(fid) not in ("nan", "None", ""):
                            img = drive_img_src(str(fid), 200)
                            if img:
                                st.image(img, use_container_width=True)
                        label = (item.get("label")
                                 or " ".join(filter(None, [item.get("brand"), item.get("model")]))
                                 or "?")
                        sub   = " · ".join(filter(None, [item.get("subtype"), item.get("condition_label")]))
                        st.caption(f"**{label}**" + (f"  \n{sub}" if sub else ""))


# ─────────────────────────────────────────────────────────────
#  SIDEBAR PRINCIPALE — Navigation
# ─────────────────────────────────────────────────────────────

def render_sidebar():
    with st.sidebar:
        st.markdown(
            "<div style='padding:16px 0 8px 0'>"
            "<span style='font-size:1.5rem;font-weight:700;color:#f1f5f9'>⚙ SIGA</span><br>"
            "<span style='font-size:0.75rem;color:#64748b;letter-spacing:0.08em'>"
            "GESTION D'ATELIER</span>"
            "</div>",
            unsafe_allow_html=True,
        )

        # ── Indicateur utilisateur connecté ───────────────────
        current_user = get_current_user()
        if is_admin():
            st.sidebar.success(f"👤 Admin : {current_user}")
        else:
            st.sidebar.info(f"👤 Connecté : {current_user}")

        st.markdown("---")

        # Appliquer une demande de navigation avant que le widget soit instancié.
        # Si la page demandée n'est pas accessible au rôle actuel, rediriger Dashboard.
        if "_nav_request" in st.session_state:
            requested = st.session_state.pop("_nav_request")
            if requested in allowed_pages():
                st.session_state["nav_radio"] = requested
            else:
                st.session_state["nav_radio"] = "🏭 Parc Matériel"

        page = st.radio(
            "Navigation",
            options=allowed_pages(),
            label_visibility="collapsed",
            key="nav_radio",
        )

        # Badge count validation (affiché seulement pour l'admin qui voit la page)
        if is_admin():
            review_count_df = run_query("SELECT COUNT(*) AS n FROM equipment WHERE review_required = true")
            if not review_count_df.empty:
                n = int(review_count_df.iloc[0]["n"] or 0)
                if n > 0:
                    st.markdown(
                        f'<div style="margin-top:-10px;margin-left:8px">'
                        f'<span class="badge badge-red">{n} en attente</span></div>',
                        unsafe_allow_html=True,
                    )

        st.markdown("---")
        st.markdown(
            "<div style='font-size:0.72rem;color:#475569;padding-bottom:8px'>"
            f"Base : <code style='color:#64748b'>{Path(DB_PATH).name}</code></div>",
            unsafe_allow_html=True,
        )

        # Indicateur connexion DB
        if db_is_reachable():
            st.markdown(
                '<span class="badge badge-green">● DB connectée</span>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<span class="badge badge-red">● DB inaccessible</span>',
                unsafe_allow_html=True,
            )

        # ── Panier Kit ────────────────────────────────────
        basket: dict = st.session_state.get("kit_basket", {})
        st.markdown("---")
        n_basket = len(basket)
        basket_title = (
            f"🧺 Panier Kit &nbsp;<span style='background:#1d4ed8;color:#bfdbfe;"
            f"padding:1px 8px;border-radius:10px;font-size:0.75rem'>{n_basket}</span>"
            if n_basket else "🧺 Panier Kit"
        )
        st.markdown(
            f"<div style='font-weight:600;font-size:0.9rem;color:#e2e8f0;"
            f"margin-bottom:6px'>{basket_title}</div>",
            unsafe_allow_html=True,
        )

        if basket:
            for eid, ename in list(basket.items()):
                c_name, c_rm = st.columns([5, 1])
                c_name.markdown(
                    f"<span style='font-size:0.78rem;color:#cbd5e1'>{ename}</span>",
                    unsafe_allow_html=True,
                )
                if c_rm.button("✕", key=f"basket_rm_{eid}"):
                    del st.session_state["kit_basket"][eid]
                    st.rerun()

            kit_name_input = st.text_input(
                "Nom du kit *", key="basket_kit_name",
                placeholder="ex: Caisse Chantier A"
            )
            kit_desc_input = st.text_area(
                "Description", key="basket_kit_desc", height=56
            )
            col_create, col_clear = st.columns([3, 1])
            with col_create:
                if st.button("✅ Créer le Kit", use_container_width=True, type="primary"):
                    if not kit_name_input.strip():
                        st.error("Nom obligatoire.")
                    else:
                        kid = str(uuid.uuid4())
                        ok  = run_write(
                            "INSERT INTO kits (kit_id, name, description) VALUES (?, ?, ?)",
                            [kid, kit_name_input.strip(), kit_desc_input.strip() or None],
                        )
                        for eid in list(basket.keys()):
                            ok = ok and run_write(
                                "INSERT OR IGNORE INTO kit_items (kit_id, equipment_id) VALUES (?, ?)",
                                [kid, eid],
                            )
                        if ok:
                            st.success(f"✅ Kit **{kit_name_input.strip()}** créé ({n_basket} outil(s)).")
                            st.session_state["kit_basket"] = {}
                            st.rerun()
            with col_clear:
                if st.button("🗑", key="basket_clear", help="Vider le panier"):
                    st.session_state["kit_basket"] = {}
                    st.rerun()
        else:
            st.markdown(
                "<div style='font-size:0.76rem;color:#475569;line-height:1.5'>"
                "Ajoutez des outils depuis<br>🏭 <b>Parc Matériel</b> avec le bouton 🧺"
                "</div>",
                unsafe_allow_html=True,
            )

        # ── Sortie groupée ────────────────────────────────
        loan: dict = st.session_state.get("loan_basket", {})
        st.markdown("---")
        n_loan = len(loan)
        loan_title = (
            f"📤 Sortie groupée &nbsp;<span style='background:#b45309;color:#fef3c7;"
            f"padding:1px 8px;border-radius:10px;font-size:0.75rem'>{n_loan}</span>"
            if n_loan else "📤 Sortie groupée"
        )
        st.markdown(
            f"<div style='font-weight:600;font-size:0.9rem;color:#e2e8f0;"
            f"margin-bottom:6px'>{loan_title}</div>",
            unsafe_allow_html=True,
        )

        if loan:
            for eid, ename in list(loan.items()):
                lc_name, lc_rm = st.columns([5, 1])
                lc_name.markdown(
                    f"<span style='font-size:0.78rem;color:#cbd5e1'>{ename}</span>",
                    unsafe_allow_html=True,
                )
                if lc_rm.button("✕", key=f"loan_rm_{eid}"):
                    del st.session_state["loan_basket"][eid]
                    st.rerun()

            loan_type = st.selectbox(
                "Type",
                ["LOAN", "RENTAL", "MAINTENANCE"],
                format_func=lambda x: {"LOAN": "🤝 Prêt", "RENTAL": "💶 Location",
                                       "MAINTENANCE": "🔧 Maintenance"}[x],
                key="loan_type",
            )
            loan_borrower = st.text_input(
                "Emprunteur *", key="loan_borrower",
                placeholder="Nom ou service"
            )
            loan_contact = st.text_input(
                "Téléphone / Email", key="loan_contact"
            )
            loan_ret = st.date_input("Retour prévu", key="loan_ret_date", value=None)
            loan_notes = st.text_area("Notes", key="loan_notes", height=52)

            col_out, col_clr = st.columns([3, 1])
            with col_out:
                if st.button("📤 Sortir le matériel", use_container_width=True, type="primary",
                             key="loan_submit"):
                    if not loan_borrower.strip():
                        st.error("Emprunteur obligatoire.")
                    else:
                        batch_id = str(uuid.uuid4())
                        exp_dt   = (datetime.combine(loan_ret, datetime.min.time())
                                    if loan_ret else None)
                        errors = 0
                        for eid in list(loan.keys()):
                            ok = run_write("""
                                INSERT INTO equipment_movements
                                    (movement_id, equipment_id, movement_type,
                                     borrower_name, borrower_contact,
                                     out_date, expected_return_date, notes, batch_id)
                                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
                            """, [str(uuid.uuid4()), eid, loan_type,
                                  loan_borrower.strip(), loan_contact.strip() or None,
                                  exp_dt, loan_notes.strip() or None, batch_id])
                            if not ok:
                                errors += 1
                        if errors == 0:
                            st.success(
                                f"✅ {n_loan} outil(s) sortis pour "
                                f"**{loan_borrower.strip()}**."
                            )
                            st.session_state["loan_basket"] = {}
                            st.rerun()
                        else:
                            st.error(f"{errors} insertion(s) échouée(s).")
            with col_clr:
                if st.button("🗑", key="loan_clear", help="Vider la liste"):
                    st.session_state["loan_basket"] = {}
                    st.rerun()
        else:
            st.markdown(
                "<div style='font-size:0.76rem;color:#475569;line-height:1.5'>"
                "Sélectionnez des outils depuis<br>🏭 <b>Parc Matériel</b> avec le bouton 📤"
                "</div>",
                unsafe_allow_html=True,
            )

    return page

# ─────────────────────────────────────────────────────────────
#  POINT D'ENTRÉE
# ─────────────────────────────────────────────────────────────

def render_kiosk_screensaver() -> None:
    """Écran de veille kiosque : logo SIGA, heure courante, message d'attente."""
    now_str = datetime.now().strftime("%H:%M:%S")
    st.markdown(
        f"""
        <div style="
            display: flex; flex-direction: column; align-items: center;
            justify-content: center; height: 80vh; gap: 2rem;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            border-radius: 1.5rem; padding: 3rem;
        ">
            <div style="font-size: 5rem; font-weight: 900; color: #38bdf8;
                        letter-spacing: 0.1em; text-shadow: 0 0 40px #38bdf880;">
                SIGA
            </div>
            <div style="font-size: 1.4rem; color: #94a3b8; font-weight: 300;">
                Système d'Ingestion et de Gestion d'Atelier
            </div>
            <div style="font-size: 4rem; font-weight: 700; color: #f1f5f9;
                        font-family: monospace; letter-spacing: 0.05em;">
                {now_str}
            </div>
            <div style="font-size: 1.2rem; color: #64748b; margin-top: 1rem;
                        display: flex; align-items: center; gap: 0.5rem;">
                <span style="display:inline-block; width:10px; height:10px;
                             border-radius:50%; background:#22c55e;
                             animation: pulse 2s infinite;"></span>
                En attente d'ordres de l'IA...
            </div>
        </div>
        <style>
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.3; }}
        }}
        header, section[data-testid="stSidebar"] {{ display: none !important; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _fmt_kiosk_date(d) -> str:
    """Formate une date (datetime ou str JSON) en dd/mm/yyyy."""
    if d is None or (isinstance(d, float) and pd.isna(d)) or str(d) in ("", "None", "nan", "NaT"):
        return "?"
    try:
        return pd.Timestamp(d).strftime("%d/%m/%Y")
    except Exception:
        return str(d)[:10]


def render_kiosk_equipment(data: dict) -> None:
    """Affiche la fiche équipement complète en mode kiosque.

    - Toutes les photos (galerie verticale à gauche)
    - Toutes les infos disponibles (droite) : badges, spécifications, n° série,
      emplacement, notes, état des prêts actifs.
    Aucune connexion DuckDB : données pré-embarquées dans kiosk_state.json.
    """
    if not data:
        render_kiosk_screensaver()
        return

    label     = null_str(data.get("label"),          "Équipement")
    brand     = null_str(data.get("brand"),           "")
    model     = null_str(data.get("model"),           "")
    serial    = null_str(data.get("serial_number"),   "")
    subtype   = null_str(data.get("subtype"),         "")
    condition = null_str(data.get("condition_label"), "")
    location  = null_str(data.get("location_hint"),   "")
    notes     = null_str(data.get("notes"),           "")
    specs     = data.get("technical_specs") or {}
    media     = data.get("media_files") or []   # liste {file_id, role}
    loans_raw = data.get("loans") or []

    st.markdown("""<style>
    header, section[data-testid="stSidebar"] { display: none !important; }
    /* Fond sombre sur toute la page en mode kiosque */
    body, .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    [data-testid="stMainBlockContainer"] {
        background-color: #0f172a !important;
        color: #f1f5f9 !important;
    }
    .kiosk-card  { background: #0f172a; border-radius: 1.5rem; padding: 1.5rem; }
    .kiosk-title { font-size: 2.8rem; font-weight: 900; color: #f1f5f9; line-height: 1.1; }
    .kiosk-sub   { font-size: 1.3rem; color: #38bdf8; font-weight: 600; margin-top: 0.3rem; }
    .kiosk-badge { display:inline-block; padding:0.3rem 0.9rem; border-radius:9999px;
                   font-size:1rem; font-weight:600; margin-right:0.4rem; margin-bottom:0.3rem; }
    .kiosk-spec-label { color:#64748b; font-size:0.9rem; }
    .kiosk-spec-val   { color:#f1f5f9; font-size:1rem; font-weight:600; }
    .kiosk-sep   { border:none; border-top:1px solid #1e293b; margin:1rem 0; }
    </style>""", unsafe_allow_html=True)

    col_img, col_info = st.columns([2, 3], gap="large")

    # ── Pré-chargement parallèle de toutes les images ──────────
    # Télécharge simultanément via ThreadPoolExecutor pour éviter
    # le délai séquentiel (5 photos × ~3 s = 15 s → réduit à ~3 s).
    file_ids = [m["file_id"] for m in media if m.get("file_id")]
    _img_cache: dict[str, bytes | None] = {}
    if file_ids:
        with ThreadPoolExecutor(max_workers=min(len(file_ids), 5)) as _ex:
            _futures = {_ex.submit(get_drive_image_bytes, fid): fid for fid in file_ids}
            for _fut in as_completed(_futures):
                _img_cache[_futures[_fut]] = _fut.result()

    def _b64img_cached(file_id: str, mime: str = "image/jpeg") -> str | None:
        """Retourne data-URL depuis le cache pré-chargé, ou None."""
        img = _img_cache.get(file_id)
        if not img:
            return None
        return f"data:{mime};base64,{base64.b64encode(img).decode()}"

    # ── Colonne gauche : galerie de toutes les photos ──────────
    with col_img:
        if media:
            # Première photo grande, les suivantes en ligne de miniatures
            primary_url = _b64img_cached(media[0]["file_id"])
            if primary_url:
                st.markdown(
                    f"<img src='{primary_url}' style='width:100%;border-radius:1rem;"
                    f"object-fit:cover;max-height:380px;display:block;'>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    "<div style='height:320px;background:#1e293b;border-radius:1rem;"
                    "display:flex;align-items:center;justify-content:center;font-size:5rem;'>🔧</div>",
                    unsafe_allow_html=True,
                )

            # Photos supplémentaires en ligne de miniatures
            if len(media) > 1:
                thumbs_html = "<div style='display:flex;gap:0.5rem;margin-top:0.6rem;flex-wrap:wrap;'>"
                for m in media[1:]:
                    url = _b64img_cached(m["file_id"])
                    if url:
                        role_label = {"overview": "Vue générale", "nameplate": "Plaque"}.get(
                            m.get("role", ""), m.get("role", "")
                        )
                        thumbs_html += (
                            f"<div style='flex:1;min-width:80px;'>"
                            f"<img src='{url}' title='{role_label}' style='width:100%;border-radius:0.6rem;"
                            f"object-fit:cover;height:90px;'>"
                            f"</div>"
                        )
                thumbs_html += "</div>"
                st.markdown(thumbs_html, unsafe_allow_html=True)
        else:
            st.markdown(
                "<div style='height:320px;background:#1e293b;border-radius:1rem;"
                "display:flex;align-items:center;justify-content:center;font-size:5rem;'>🔧</div>",
                unsafe_allow_html=True,
            )

    # ── Colonne droite : toutes les infos ─────────────────────
    with col_info:
        condition_color = {
            "bon": "#22c55e", "fonctionnel": "#84cc16", "dégradé": "#f59e0b",
            "hors service": "#ef4444",
        }.get((condition or "").lower(), "#94a3b8")
        subtype_badge = (
            f"<span class='kiosk-badge' style='background:#334155;color:#94a3b8;'>{subtype}</span>"
            if subtype else ""
        )
        bm = f"{brand} {model}".strip()
        st.markdown(
            f"<div class='kiosk-title'>{label}</div>"
            f"{'<div class=\"kiosk-sub\">' + bm + '</div>' if bm else ''}"
            f"<div style='margin-top:0.9rem;'>"
            f"<span class='kiosk-badge' style='background:{condition_color}22;color:{condition_color};"
            f"border:1px solid {condition_color}44;'>{condition or 'État inconnu'}</span>"
            f"{subtype_badge}</div>",
            unsafe_allow_html=True,
        )

        # Specs techniques — toutes (pas de limite à 8)
        if specs:
            specs_html = "<hr class='kiosk-sep'><div style='display:grid;grid-template-columns:1fr 1fr;gap:0.6rem 1.5rem;'>"
            for k, v in specs.items():
                specs_html += (
                    f"<div><div class='kiosk-spec-label'>{k}</div>"
                    f"<div class='kiosk-spec-val'>{v}</div></div>"
                )
            specs_html += "</div>"
            st.markdown(specs_html, unsafe_allow_html=True)

        # Infos pratiques (serial, emplacement, notes)
        pratique_items = []
        if serial:
            pratique_items.append(("N° série", serial))
        if location:
            pratique_items.append(("Emplacement", location))
        if pratique_items:
            pratique_html = "<hr class='kiosk-sep'><div style='display:flex;gap:2rem;flex-wrap:wrap;'>"
            for lbl, val in pratique_items:
                pratique_html += (
                    f"<div><div class='kiosk-spec-label'>{lbl}</div>"
                    f"<div class='kiosk-spec-val'>{val}</div></div>"
                )
            pratique_html += "</div>"
            st.markdown(pratique_html, unsafe_allow_html=True)

        if notes:
            st.markdown(
                f"<hr class='kiosk-sep'><div style='color:#94a3b8;font-size:0.95rem;"
                f"font-style:italic;background:#1e293b;border-radius:0.6rem;"
                f"padding:0.6rem 1rem;'>📝 {notes}</div>",
                unsafe_allow_html=True,
            )

        # État des prêts
        st.markdown("<hr class='kiosk-sep'>", unsafe_allow_html=True)
        if not loans_raw:
            st.markdown(
                "<div style='text-align:center;padding:1rem;background:#14532d22;"
                "border-radius:1rem;border:1px solid #22c55e44;'>"
                "<span style='font-size:1.4rem;color:#22c55e;font-weight:700;'>✅ Disponible</span></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<div style='text-align:center;padding:1rem;background:#7f1d1d22;"
                "border-radius:1rem;border:1px solid #ef444444;margin-bottom:0.8rem;'>"
                "<span style='font-size:1.4rem;color:#ef4444;font-weight:700;'>🔴 En cours d'utilisation</span></div>",
                unsafe_allow_html=True,
            )
            for mv in loans_raw:
                borrower = null_str(mv.get("borrower_name"), "Inconnu")
                mv_type  = null_str(mv.get("movement_type"), "")
                out_str  = _fmt_kiosk_date(mv.get("out_date"))
                ret_str  = _fmt_kiosk_date(mv.get("expected_return_date"))
                st.markdown(
                    f"<div style='background:#1e293b;border-radius:0.7rem;padding:0.7rem 1rem;"
                    f"margin-bottom:0.4rem;'>"
                    f"<b style='color:#f1f5f9;'>{borrower}</b> "
                    f"<span style='color:#64748b;'>— {mv_type} | Sorti le {out_str} | Retour prévu {ret_str}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )


def render_kiosk_kit(data: dict) -> None:
    """Affiche la fiche d'un kit avec la liste de ses outils (mode kiosque)."""
    name        = null_str(data.get("name"),        "Kit")
    description = null_str(data.get("description"), "")
    items       = data.get("items") or []

    st.markdown("""<style>
    header, section[data-testid="stSidebar"] { display: none !important; }
    .kit-thumb { width:80px;height:80px;object-fit:cover;border-radius:0.5rem;flex-shrink:0; }
    .kit-thumb-ph { width:80px;height:80px;background:#334155;border-radius:0.5rem;
                    display:flex;align-items:center;justify-content:center;
                    font-size:1.8rem;flex-shrink:0; }
    </style>""", unsafe_allow_html=True)

    desc_html = f"<div style='font-size:1.2rem;color:#38bdf8;font-weight:600;margin-top:0.3rem;'>{description}</div>" if description else ""
    st.markdown(
        f"<div style='background:#0f172a;border-radius:1.5rem;padding:1.5rem 2rem;margin-bottom:1.2rem;'>"
        f"<div style='font-size:0.9rem;color:#64748b;font-weight:600;letter-spacing:0.1em;'>🧰 KIT</div>"
        f"<div style='font-size:2.6rem;font-weight:900;color:#f1f5f9;line-height:1.1;'>{name}</div>"
        f"{desc_html}<div style='margin-top:0.8rem;color:#94a3b8;font-size:1rem;'>{len(items)} outil(s)</div></div>",
        unsafe_allow_html=True,
    )

    if items:
        # Grille 2 colonnes ; chaque item = thumbnail + texte côte à côte
        cols = st.columns(2)
        for i, item in enumerate(items):
            lbl  = null_str(item.get("label"),          "—")
            bm   = " ".join(filter(None, [str(item.get("brand") or ""), str(item.get("model") or "")])).strip()
            cond = null_str(item.get("condition_label"), "")
            loc  = null_str(item.get("location_hint"),   "")
            sub  = " · ".join(filter(None, [bm, cond, loc]))
            fid  = item.get("main_file_id")
            url  = _b64img(fid) if fid and str(fid) not in ("None", "nan", "") else None

            img_html = (
                f"<img class='kit-thumb' src='{url}'>" if url
                else "<div class='kit-thumb-ph'>🔧</div>"
            )
            sub_html = f"<div style='color:#64748b;font-size:0.88rem;margin-top:0.2rem;'>{sub}</div>" if sub else ""
            with cols[i % 2]:
                st.markdown(
                    f"<div style='display:flex;align-items:center;gap:0.9rem;"
                    f"background:#1e293b;border-radius:0.8rem;padding:0.7rem;"
                    f"margin-bottom:0.5rem;border-left:3px solid #38bdf8;'>"
                    f"{img_html}"
                    f"<div><div style='color:#f1f5f9;font-weight:700;font-size:1rem;'>{lbl}</div>{sub_html}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    else:
        st.markdown(
            "<div style='color:#64748b;text-align:center;padding:2rem;'>Kit vide</div>",
            unsafe_allow_html=True,
        )


def render_kiosk_movements_active(data: dict) -> None:
    """Affiche le tableau des sorties en cours avec thumbnails (mode kiosque)."""
    items = data.get("items") or []
    count = data.get("count", len(items))
    late  = sum(1 for i in items if i.get("is_late"))

    st.markdown("""<style>
    header, section[data-testid="stSidebar"] { display: none !important; }
    .mv-thumb { width:72px;height:72px;object-fit:cover;border-radius:0.5rem;flex-shrink:0; }
    .mv-thumb-ph { width:72px;height:72px;background:#334155;border-radius:0.5rem;
                   display:flex;align-items:center;justify-content:center;
                   font-size:1.6rem;flex-shrink:0; }
    </style>""", unsafe_allow_html=True)

    late_badge = (
        f"<span style='color:#ef4444;font-size:1.1rem;font-weight:700;'>⚠ {late} en retard</span>"
        if late else ""
    )
    st.markdown(
        f"<div style='background:#0f172a;border-radius:1.5rem;padding:1.2rem 2rem;margin-bottom:1.2rem;'>"
        f"<div style='font-size:0.9rem;color:#64748b;font-weight:600;letter-spacing:0.1em;'>📤 SORTIES EN COURS</div>"
        f"<div style='font-size:2.3rem;font-weight:900;color:#f1f5f9;'>{count} outil(s)</div>"
        f"{late_badge}</div>",
        unsafe_allow_html=True,
    )

    if not items:
        st.markdown(
            "<div style='text-align:center;padding:3rem;color:#22c55e;font-size:1.4rem;"
            "font-weight:700;'>✅ Aucun outil sorti en ce moment</div>",
            unsafe_allow_html=True,
        )
        return

    # Grille 2 colonnes ; chaque item = thumbnail + texte + badge
    cols = st.columns(2)
    for i, item in enumerate(items):
        lbl      = null_str(item.get("label"),         "—")
        borrower = null_str(item.get("borrower_name"), "?")
        mv_type  = null_str(item.get("movement_type"), "")
        out_str  = _fmt_kiosk_date(item.get("out_date"))
        ret_str  = _fmt_kiosk_date(item.get("expected_return_date"))
        kit_name = item.get("kit_name")
        is_late  = bool(item.get("is_late"))
        fid      = item.get("main_file_id")
        url      = _b64img(fid) if fid and str(fid) not in ("None", "nan", "") else None

        img_html  = f"<img class='mv-thumb' src='{url}'>" if url else "<div class='mv-thumb-ph'>🔧</div>"
        badge     = ("<span style='background:#7f1d1d44;color:#ef4444;border:1px solid #ef444466;"
                     "border-radius:9999px;padding:0.15rem 0.7rem;font-size:0.78rem;font-weight:700;"
                     "white-space:nowrap;'>EN RETARD</span>") if is_late else (
                    "<span style='background:#14532d44;color:#22c55e;border:1px solid #22c55e66;"
                     "border-radius:9999px;padding:0.15rem 0.7rem;font-size:0.78rem;'>OK</span>")
        kit_tag   = f" · Kit : {kit_name}" if kit_name else ""
        border_c  = "#ef4444" if is_late else "#38bdf8"

        with cols[i % 2]:
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:0.9rem;background:#1e293b;"
                f"border-radius:0.8rem;padding:0.7rem;margin-bottom:0.5rem;"
                f"border-left:3px solid {border_c};'>"
                f"{img_html}"
                f"<div style='flex:1;min-width:0;'>"
                f"<div style='color:#f1f5f9;font-weight:700;font-size:1rem;'>{lbl}</div>"
                f"<div style='color:#38bdf8;font-size:0.92rem;'>{borrower}</div>"
                f"<div style='color:#64748b;font-size:0.82rem;'>{mv_type} | {out_str} → {ret_str}{kit_tag}</div>"
                f"</div>{badge}</div>",
                unsafe_allow_html=True,
            )


def render_kiosk_confirmation(data: dict) -> None:
    """Affiche un écran de confirmation d'action OpenClaw (mode kiosque)."""
    title    = null_str(data.get("title"),    "Action effectuée")
    subtitle = null_str(data.get("subtitle"), "")
    details  = data.get("details") or []
    batch_id = data.get("batch_id")
    color    = data.get("color", "green")

    color_map = {
        "green": ("#22c55e", "#14532d44", "#22c55e44"),
        "red":   ("#ef4444", "#7f1d1d44", "#ef444444"),
        "blue":  ("#38bdf8", "#0c4a6e44", "#38bdf844"),
    }
    c_text, c_bg, c_border = color_map.get(color, color_map["green"])

    icon_map = {"green": "✅", "red": "⚠️", "blue": "ℹ️"}
    icon = icon_map.get(color, "✅")

    details_html = "".join(
        f"<div style='color:#94a3b8;font-size:1.05rem;padding:0.3rem 0;"
        f"border-bottom:1px solid #1e293b;'>{d}</div>"
        for d in details
    )
    batch_html = (
        f"<div style='margin-top:1.2rem;color:#64748b;font-size:0.88rem;"
        f"font-family:monospace;'>Lot : {batch_id}</div>"
        if batch_id else ""
    )

    st.markdown("""
        <style>
        header, section[data-testid="stSidebar"] { display: none !important; }
        </style>
    """, unsafe_allow_html=True)

    subtitle_html = (
        f"<div style='font-size:1.4rem;color:#94a3b8;text-align:center;'>{subtitle}</div>"
        if subtitle else ""
    )
    details_block = (
        f"<div style='background:{c_bg};border:1px solid {c_border};"
        f"border-radius:1rem;padding:1.2rem 2rem;max-width:700px;width:100%;'>"
        f"{details_html}</div>"
        if details else ""
    )
    st.markdown(
        f"<div style='display:flex;flex-direction:column;align-items:center;"
        f"justify-content:center;min-height:70vh;gap:1.5rem;'>"
        f"<div style='font-size:5rem;'>{icon}</div>"
        f"<div style='font-size:3rem;font-weight:900;color:{c_text};text-align:center;'>{title}</div>"
        f"{subtitle_html}{details_block}{batch_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_kiosk_mode() -> None:
    """Point d'entrée du mode kiosque.

    Lit le fichier JSON écrit par l'API (kiosk_state.json) au lieu de
    polluer DuckDB toutes les 2 s. Aucune connexion base de données n'est
    ouverte pendant la boucle de rafraîchissement — le verrou continu
    observé avec l'ancien polling sur ui_commands est supprimé.

    Le champ `updated_at` sert à détecter les changements d'état entre
    deux cycles : session_state conserve les données jusqu'au prochain
    changement, évitant tout rechargement inutile.
    """
    st_autorefresh(interval=2000, key="kioskreload")

    # Initialise l'état persistant entre refreshes
    for _k in ("kiosk_cmd_type", "kiosk_updated_at", "kiosk_data"):
        if _k not in st.session_state:
            st.session_state[_k] = None

    # Lecture du fichier JSON — aucune connexion DuckDB
    state      = _read_kiosk_state()
    updated_at = state.get("updated_at", "")
    cmd_type   = state.get("command_type", "CLEAR_SCREEN")
    data       = state.get("data") or {}

    # Mise à jour de session_state uniquement si l'état a changé
    if updated_at != st.session_state["kiosk_updated_at"]:
        st.session_state["kiosk_updated_at"] = updated_at
        st.session_state["kiosk_cmd_type"]   = cmd_type
        st.session_state["kiosk_data"]       = data

    # Dispatch vers la vue correspondante
    current_type = st.session_state["kiosk_cmd_type"] or "CLEAR_SCREEN"
    current_data = st.session_state["kiosk_data"] or {}

    if current_type == "SHOW_EQUIPMENT":
        render_kiosk_equipment(current_data)
    elif current_type == "SHOW_KIT":
        render_kiosk_kit(current_data)
    elif current_type == "SHOW_MOVEMENTS_ACTIVE":
        render_kiosk_movements_active(current_data)
    elif current_type == "SHOW_CONFIRMATION":
        render_kiosk_confirmation(current_data)
    else:
        render_kiosk_screensaver()


def main():
    if "kit_basket" not in st.session_state:
        st.session_state["kit_basket"] = {}
    if "loan_basket" not in st.session_state:
        st.session_state["loan_basket"] = {}
    init_db_tables()

    # ── Mode kiosque ──────────────────────────────────────────
    params = st.query_params
    if params.get("kiosk") == "true":
        render_kiosk_mode()
        return

    # ── Mode normal ───────────────────────────────────────────
    page = render_sidebar()

    if page == "🏭 Parc Matériel":
        render_parc_materiel()
    elif page == "⚠ Centre de Validation":
        render_validation()
    elif page == "📦 Suivi des Mouvements":
        render_suivi_mouvements()
    elif page == "🧰 Gestion des Kits":
        render_gestion_kits()
    elif page == "📊 Dashboard":
        render_dashboard()
    elif page == "🔒 Journal des Accès":
        render_access_log()


if __name__ == "__main__":
    main()
