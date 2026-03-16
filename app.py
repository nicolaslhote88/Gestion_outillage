"""
SIGA — Système d'Ingestion et de Gestion d'Atelier
Frontend Streamlit — Interface de gestion d'inventaire outillage

Architecture : single-file app.py modulaire avec fonctions par section.
Connexion DuckDB en read_only=True (compatible avec n8n qui écrit en parallèle).
"""

import json
import os
import re
import subprocess
import streamlit as st
import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build as _build_gdrive

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION GLOBALE
# ─────────────────────────────────────────────────────────────

DB_PATH = "/files/duckdb/siga_v1.duckdb"

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


def db_is_reachable() -> bool:
    """Vérifie la disponibilité de la DB sans garder la connexion ouverte."""
    try:
        with duckdb.connect(DB_PATH, read_only=True) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


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


@st.cache_data(ttl=3600, show_spinner=False)
def get_drive_image_bytes(file_id: str) -> bytes | None:
    """Télécharge une image Drive côté serveur via service account.
    Retourne None si le service account n'est pas configuré (fallback URL)."""
    if not file_id or str(file_id) in ("nan", "None", ""):
        return None
    sa_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/secrets/service_account.json")
    if not Path(sa_path).exists():
        return None
    try:
        creds = service_account.Credentials.from_service_account_file(
            sa_path,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        svc = _build_gdrive("drive", "v3", credentials=creds, cache_discovery=False)
        return svc.files().get_media(fileId=file_id, supportsAllDrives=True).execute()
    except Exception as e:
        import logging
        logging.warning("get_drive_image_bytes(%s): %s", file_id, e)
        return None


def drive_img_src(file_id: str, size: int = 400):
    """Retourne bytes (proxy SA) ou URL Drive en fallback.
    Permet d'afficher les images sans que l'utilisateur ait accès au Drive."""
    data = get_drive_image_bytes(file_id)
    return data if data is not None else drive_thumbnail_url(file_id, size)


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
    st.markdown('<p class="section-title">⚠ Centre de Validation</p>', unsafe_allow_html=True)
    st.markdown('<p class="section-subtitle">Équipements détectés par l\'IA nécessitant une vérification humaine</p>', unsafe_allow_html=True)

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

        with st.expander(expander_title, expanded=False):
            img_col, info_col = st.columns([1, 2])

            # ── Photo ──────────────────────────────────────────
            with img_col:
                media_df = run_query("""
                    SELECT final_drive_file_id, image_role
                    FROM equipment_media
                    WHERE equipment_id = ?
                    ORDER BY
                        CASE image_role
                            WHEN 'overview'   THEN 1
                            WHEN 'nameplate'  THEN 2
                            WHEN 'detail'     THEN 3
                            ELSE 4
                        END
                    LIMIT 3
                """, [row["equipment_id"]])

                if not media_df.empty:
                    # Affiche la photo principale
                    main_media = media_df.iloc[0]
                    file_id    = main_media.get("final_drive_file_id")
                    img_src    = drive_img_src(file_id, 600) if file_id else None
                    if img_src:
                        st.image(img_src, use_container_width=True,
                                 caption=f"Vue : {null_str(main_media.get('image_role'))}")
                    else:
                        st.info("📷 Aucune image disponible")

                    # Miniatures supplémentaires
                    if len(media_df) > 1:
                        thumb_cols = st.columns(len(media_df) - 1)
                        for idx, (_, m) in enumerate(media_df.iloc[1:].iterrows()):
                            fid = m.get("final_drive_file_id")
                            if fid:
                                thumb_cols[idx].image(
                                    drive_img_src(fid, 200),
                                    use_container_width=True,
                                    caption=null_str(m.get("image_role")),
                                )
                else:
                    st.info("📷 Aucune image disponible")

            # ── Informations détectées ──────────────────────────
            with info_col:
                missing = safe_json(row.get("missing_fields_json"), [])
                if isinstance(missing, dict):
                    missing = list(missing.keys())
                missing_set = {str(f).lower() for f in missing}

                st.markdown("**Informations détectées par l'IA**")

                def field_row(field_key: str, field_label: str, value):
                    """Affiche un champ avec surbrillance si manquant."""
                    val_str = null_str(value)
                    if field_key.lower() in missing_set:
                        st.markdown(
                            f"🔴 **{field_label}** : "
                            f'<span style="color:#fca5a5">{val_str}</span> '
                            f'<span class="badge badge-red">Manquant</span>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(f"✅ **{field_label}** : {val_str}")

                field_row("label",         "Nom / Désignation",  row.get("label"))
                field_row("brand",         "Marque",             row.get("brand"))
                field_row("model",         "Modèle",             row.get("model"))
                field_row("serial_number", "N° de série",        row.get("serial_number"))
                field_row("subtype",       "Type d'outil",       row.get("subtype"))
                field_row("condition_label","État",              row.get("condition_label"))
                field_row("location_hint", "Emplacement",        row.get("location_hint"))

                # Specs techniques
                specs = safe_json(row.get("technical_specs_json"), {})
                if specs:
                    st.markdown("---")
                    st.markdown("**Spécifications techniques**")
                    for k, v in specs.items():
                        st.markdown(f"&nbsp;&nbsp;&nbsp;• **{k}** : {v}")

                # Raisons de révision
                if reasons:
                    st.markdown("---")
                    st.markdown("**Raisons de la révision**")
                    for r in reasons:
                        st.markdown(f"&nbsp;&nbsp;&nbsp;⚠ {r}")

                # Notes
                notes = null_str(row.get("notes"))
                if notes != "—":
                    st.markdown("---")
                    st.markdown(f"📝 **Notes** : _{notes}_")

                # Lien Drive
                folder_url = drive_folder_url(row.get("final_drive_folder_id"))
                if folder_url:
                    st.markdown(f"[📁 Ouvrir le dossier Drive]({folder_url})")

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
        SELECT final_drive_file_id, image_role, image_index
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
        st.markdown("**Photos**")
        if not media_df.empty:
            # Photo principale
            main = media_df.iloc[0]
            fid  = main.get("final_drive_file_id")
            if fid:
                st.image(drive_img_src(fid, 700), use_container_width=True)
            # Galerie miniatures
            if len(media_df) > 1:
                nb = min(len(media_df) - 1, 3)
                thumb_cols = st.columns(nb)
                for i, (_, m) in enumerate(media_df.iloc[1:nb+1].iterrows()):
                    fid2 = m.get("final_drive_file_id")
                    if fid2:
                        thumb_cols[i].image(
                            drive_img_src(fid2, 250),
                            use_container_width=True,
                            caption=null_str(m.get("image_role")),
                        )
        else:
            st.info("Aucune image disponible.")

    # ── Détails ───────────────────────────────────────────────
    with right_col:
        st.markdown("**Identification**")
        info_data = {
            "Type"       : null_str(row.get("subtype")),
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

    # Lien Drive
    folder_url = drive_folder_url(row.get("final_drive_folder_id"))
    if folder_url:
        st.markdown("---")
        st.markdown(f"[📁 Ouvrir le dossier Drive complet]({folder_url})")

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

    # ── Affichage en grille 3 colonnes ─────────────────────────
    COLS = 3
    rows = [results_df.iloc[i:i+COLS] for i in range(0, len(results_df), COLS)]

    for chunk in rows:
        cols = st.columns(COLS)
        for col_idx, (_, item) in enumerate(chunk.iterrows()):
            with cols[col_idx]:
                # Photo miniature
                file_id = item.get("main_file_id")
                if file_id and str(file_id) not in ("nan", "None", ""):
                    st.image(
                        drive_img_src(file_id, 400),
                        use_container_width=True,
                    )
                else:
                    st.markdown(
                        '<div style="background:#1e293b;border-radius:8px;height:180px;'
                        'display:flex;align-items:center;justify-content:center;'
                        'color:#475569;font-size:2rem;">📷</div>',
                        unsafe_allow_html=True,
                    )

                # Infos carte
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

                # Bouton détail → ouvre la modale
                if st.button("Voir les détails", key=f"detail_{item['equipment_id']}"):
                    show_equipment_modal(item["equipment_id"])

                st.markdown("<div style='margin-bottom:12px'></div>", unsafe_allow_html=True)

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
    st.markdown('<p class="section-title">🔒 Journal des Accès</p>', unsafe_allow_html=True)
    st.markdown('<p class="section-subtitle">Connexions enregistrées par Traefik</p>', unsafe_allow_html=True)

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
        return

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
        return

    # ── KPIs ──────────────────────────────────────────────────
    nb_401   = sum(1 for e in entries if e["Statut"] == 401)
    nb_ok    = sum(1 for e in entries if 200 <= e["Statut"] < 300)
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
        st.markdown("---")

        page = st.radio(
            "Navigation",
            options=["📊 Dashboard", "🏭 Parc Matériel", "⚠ Centre de Validation", "🔒 Journal des Accès"],
            label_visibility="collapsed",
        )

        # Badge count validation dans le menu
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

    return page

# ─────────────────────────────────────────────────────────────
#  POINT D'ENTRÉE
# ─────────────────────────────────────────────────────────────

def main():
    page = render_sidebar()

    if page == "📊 Dashboard":
        render_dashboard()
    elif page == "🏭 Parc Matériel":
        render_parc_materiel()
    elif page == "⚠ Centre de Validation":
        render_validation()
    elif page == "🔒 Journal des Accès":
        render_access_log()


if __name__ == "__main__":
    main()
