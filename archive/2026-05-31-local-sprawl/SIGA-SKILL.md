# SIGA — Ingestion Atelier (Skill Cowork)

## Déclenchement

Ce skill est invoqué quand l'utilisateur envoie des photos et/ou une description d'un outil, accessoire ou consommable à ajouter dans son atelier.

Phrases typiques : "ajoute ça dans SIGA", "ingère cet outil", "enregistre cette perceuse", "ajoute ces consommables", envoi direct de photos sans autre instruction.

---

## Configuration technique (constantes)

```
SSH_KEY   : /sessions/upbeat-compassionate-franklin/mnt/.ssh/codex_vps_tailscale_ed25519
SSH_OPTS  : -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o ConnectTimeout=20
VPS       : root@100.104.236.78
DUCKDB    : /files/duckdb/siga_v1.duckdb
N8N_MEDIA_WEBHOOK : https://n8n.srv961978.hstgr.cloud/webhook/siga-media-upload
WEBHOOK_TOKEN     : lire depuis env n8n → SIGA_CLAUDE_WEBHOOK_TOKEN
UPLOADS_DIR       : /sessions/upbeat-compassionate-franklin/mnt/uploads/
```

---

## ÉTAPE 1 — Analyse des entrées

### 1a. Analyse visuelle des photos

Pour chaque photo fournie :

1. **Identifier le type d'objet** : outil électroportatif, outil à main, accessoire (batterie, lame, mandrin…), consommable (foret, disque, filtre…)
2. **Lire la plaque signalétique** si visible : marque, modèle, référence, tension, puissance
3. **Évaluer l'état** : neuf (emballé), bon état, usagé, à réparer
4. **Repérer les objets secondaires** sur la même photo (batterie montée, lame en place, accessoires visibles)
5. **Attribuer un rôle** à chaque photo :
   - `overview` — vue d'ensemble de l'objet
   - `nameplate` — photo de la plaque signalétique (marque/modèle)
   - `detail` — détail spécifique (connecteur, usure, accessoire monté)

### 1b. Extraction des informations textuelles

Extraire depuis le texte de l'utilisateur : catégorie, état, emplacement de rangement, quantités, informations spécifiques.

### 1c. Recherche web complémentaire

Si la marque + modèle sont identifiés ET des informations manquent (catégorie exacte, compatibilités), faire une recherche web :
- Fiche technique officielle du fabricant
- Accessoires et consommables compatibles référencés
- Gamme de la batterie (voltage, interface de charge)

---

## ÉTAPE 2 — Classification des entités

### Règles de classification

| Type | Critères | Exemples |
|---|---|---|
| **equipment** | Outil physique unique, a son propre rangement, ne se consomme pas | Perceuse, scie, visseuse, meuleuse, marteau |
| **accessory** | Se fixe ou s'utilise avec un outil, a un stock comptable | Batterie 18V, chargeur, mandrin, adaptateur, mallette |
| **consumable** | Usage limité, stock qui diminue | Foret, disque abrasif, lame de scie, visserie, filtre, bougie |

### Catégories validées en base

**equipment.category :** `Machines & Électroportatif` | `Outillage Manuel` | `Équipement de Chantier & Levage`

**accessories.category :** `Batterie` | `Chargeur` | `Coffret / ensemble d'accessoires` | `Réservoir / gobelet` | `Lance / buse` | `Flexible air` | `Flexible HP` | `Perche / support` | `Pistolet de pulvérisation` | `Accessoire nettoyeur HP`

**consumables.category :** `Disque diamant` | `Disque abrasif` | `Disque de coupe` | `Lame de scie` | `Lame` | `Foret` | `Abrasif` | `Visserie` | `Filtre`

Si la catégorie n'existe pas dans cette liste, en créer une cohérente. Ne pas utiliser "None" ou une chaîne vide.

### Liens à créer

- **links_compatibility** : un équipement ↔ un accessoire (batterie qui s'adapte, mandrin compatible, mallette de transport)
- **links_consumables** : un équipement ↔ un consommable (foret pour perceuse, disque pour meuleuse, lame pour scie)

---

## ÉTAPE 3 — Construction du plan JSON interne

Construire mentalement (ne pas afficher à l'utilisateur) le plan structuré :

```json
{
  "ingestion_id": "CLAUDE-YYYYMMDDHHMMSS",
  "equipment": [
    {
      "label": "Perceuse Visseuse Makita 18V",
      "brand": "Makita",
      "model": "DHP484",
      "category": "Machines & Électroportatif",
      "condition": "Bon état",
      "location": "",
      "ai_metadata": {
        "technologie": "18V Li-ion sans fil",
        "domaines": ["perçage", "vissage"],
        "source": "claude-cowork"
      },
      "classification_confidence": 0.95,
      "_photos": [
        {"upload_filename": "photo1.jpg", "role": "overview"},
        {"upload_filename": "photo2.jpg", "role": "nameplate"}
      ]
    }
  ],
  "accessories": [
    {
      "label": "Batterie Makita 18V 5Ah",
      "brand": "Makita",
      "model": "BL1850B",
      "category": "Batterie",
      "stock_qty": 2,
      "location_hint": "",
      "notes": "Compatible tous outils Makita LXT 18V",
      "_photos": []
    }
  ],
  "consumables": [
    {
      "label": "Foret béton SDS-Plus Ø10mm",
      "brand": "Bosch",
      "reference": "2608833800",
      "category": "Foret",
      "unit": "pcs",
      "stock_qty": 5,
      "stock_min_alert": 2,
      "location_hint": "",
      "notes": "",
      "_photos": []
    }
  ],
  "links_compatibility": [
    {"equipment_label": "Perceuse Visseuse Makita 18V", "accessory_label": "Batterie Makita 18V 5Ah", "note": ""}
  ],
  "links_consumables": [
    {"equipment_label": "Perceuse Visseuse Makita 18V", "consumable_label": "Foret béton SDS-Plus Ø10mm", "qty_per_use": 1, "note": ""}
  ]
}
```

---

## ÉTAPE 4 — Présentation interactive du plan

Afficher le plan sous cette forme :

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 PLAN D'INGESTION SIGA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔧 Équipement(s) à créer :
  • [Marque] [Modèle] — [Catégorie] — [État]
    📷 [N photo(s)] : overview, nameplate

🔌 Accessoire(s) à créer :
  • [Marque] [Modèle] — [Catégorie] — Stock : [N]

📌 Consommable(s) à créer :
  • [Marque] [Modèle] — [Catégorie] — [unité] — Stock : [N]

🔗 Liens à créer :
  • [Équipement] ↔ [Accessoire]
  • [Équipement] ↔ [Consommable]

⚠️  Points à confirmer :
  • [Champ incertain ou manquant → valeur retenue par défaut]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Terminer par : **"Confirmes-tu ce plan ? Tu peux corriger ou compléter avant l'exécution."**

Ne pas exécuter quoi que ce soit avant confirmation explicite.

---

## ÉTAPE 5 — Exécution après confirmation

Générer un `ingestion_id` : `CLAUDE-{YYYYMMDDHHMMSS}` (format datetime sans séparateurs).

### 5a. Vérification des doublons (SSH)

```bash
ssh -i /sessions/upbeat-compassionate-franklin/mnt/.ssh/codex_vps_tailscale_ed25519 \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=no root@100.104.236.78 \
  "python3 << 'PYEOF'
import duckdb, json
conn = duckdb.connect('/files/duckdb/siga_v1.duckdb', read_only=True)
results = {}
# Equipment duplicates
for label, brand in [('LABEL', 'BRAND')]:
    row = conn.execute(
        \"SELECT equipment_id, label FROM equipment WHERE lower(label)=lower(?) AND archived=false\",
        [label]
    ).fetchone()
    results[label] = {'existing_id': str(row[0]) if row else None}
# Accessory duplicates
for label, brand in [('LABEL', 'BRAND')]:
    row = conn.execute(
        \"SELECT accessory_id FROM accessories WHERE lower(label)=lower(?) AND archived=false\",
        [label]
    ).fetchone()
    results[label] = {'existing_id': str(row[0]) if row else None}
conn.close()
print(json.dumps(results))
PYEOF"
```

Si un doublon est détecté, le signaler à l'utilisateur et demander s'il faut créer quand même ou utiliser l'existant.

### 5b. Écriture DuckDB via SSH

Construire et exécuter le script Python complet via SSH. Template :

```python
import duckdb, uuid, json, time
from contextlib import contextmanager
from datetime import datetime

DB_PATH = '/files/duckdb/siga_v1.duckdb'

@contextmanager
def db_con():
    con = None
    for attempt in range(5):
        try:
            con = duckdb.connect(DB_PATH)
            break
        except Exception as e:
            if 'lock' in str(e).lower() and attempt < 4:
                time.sleep(0.3 * (2 ** attempt))
            else:
                raise
    try:
        yield con
    finally:
        if con: con.close()

INGESTION_ID = 'CLAUDE-YYYYMMDDHHMMSS'
results = {'equipment': [], 'accessories': [], 'consumables': [], 'links': [], 'errors': []}

with db_con() as con:
    # ── Equipment ─────────────────────────────────────────────────
    for eq in [
        # Remplir avec les équipements du plan
        # {'label': '...', 'brand': '...', 'model': '...', ...}
    ]:
        eq_id = str(uuid.uuid4())
        con.execute("""
            INSERT INTO equipment
            (equipment_id, label, brand, model, category, condition, location,
             status, archived, migration_status, ai_metadata, classification_confidence)
            VALUES (?,?,?,?,?,?,?, 'disponible', false, 'REVIEWED', ?, ?)
        """, [
            eq_id, eq['label'], eq.get('brand',''), eq.get('model',''),
            eq.get('category',''), eq.get('condition','Bon état'), eq.get('location',''),
            json.dumps(eq.get('ai_metadata',{})),
            eq.get('classification_confidence', 0.9)
        ])
        results['equipment'].append({'label': eq['label'], 'id': eq_id, 'status': 'created'})

    # ── Accessories ───────────────────────────────────────────────
    for acc in [
        # {'label': '...', 'brand': '...', 'model': '...', 'category': '...', 'stock_qty': 1, ...}
    ]:
        acc_id = str(uuid.uuid4())
        con.execute("""
            INSERT INTO accessories
            (accessory_id, label, brand, model, category, stock_qty, location_hint, notes, archived, migration_status)
            VALUES (?,?,?,?,?,?,?,?, false, 'REVIEWED')
        """, [
            acc_id, acc['label'], acc.get('brand',''), acc.get('model',''),
            acc.get('category',''), acc.get('stock_qty',0),
            acc.get('location_hint',''), acc.get('notes','')
        ])
        results['accessories'].append({'label': acc['label'], 'id': acc_id, 'status': 'created'})

    # ── Consumables ───────────────────────────────────────────────
    for con_item in [
        # {'label': '...', 'brand': '...', 'reference': '...', 'category': '...', 'unit': 'pcs', 'stock_qty': 0, ...}
    ]:
        con_id = str(uuid.uuid4())
        con.execute("""
            INSERT INTO consumables
            (consumable_id, label, brand, reference, category, unit,
             stock_qty, stock_min_alert, location_hint, notes, archived, migration_status)
            VALUES (?,?,?,?,?,?,?,?,?,?, false, 'REVIEWED')
        """, [
            con_id, con_item['label'], con_item.get('brand',''), con_item.get('reference',''),
            con_item.get('category',''), con_item.get('unit','pcs'),
            con_item.get('stock_qty',0), con_item.get('stock_min_alert',0),
            con_item.get('location_hint',''), con_item.get('notes','')
        ])
        results['consumables'].append({'label': con_item['label'], 'id': con_id, 'status': 'created'})

    # ── Build label→id map ─────────────────────────────────────────
    eq_map  = {r['label']: r['id'] for r in results['equipment']}
    acc_map = {r['label']: r['id'] for r in results['accessories']}
    con_map = {r['label']: r['id'] for r in results['consumables']}

    # ── Links compatibility (equipment ↔ accessory) ────────────────
    for link in [
        # {'equipment_label': '...', 'accessory_label': '...', 'note': ''}
    ]:
        eq_id  = eq_map.get(link['equipment_label'])
        acc_id = acc_map.get(link['accessory_label'])
        if eq_id and acc_id:
            con.execute(
                "INSERT INTO links_compatibility (link_id, equipment_id, accessory_id, note) VALUES (?,?,?,?)",
                [str(uuid.uuid4()), eq_id, acc_id, link.get('note','')]
            )
            results['links'].append({'type': 'compatibility', 'equipment': link['equipment_label'], 'accessory': link['accessory_label']})
        else:
            results['errors'].append(f"link_compat: equipment '{link['equipment_label']}' ou accessory '{link['accessory_label']}' introuvable")

    # ── Links consumables (equipment ↔ consumable) ─────────────────
    for link in [
        # {'equipment_label': '...', 'consumable_label': '...', 'qty_per_use': 1, 'note': ''}
    ]:
        eq_id  = eq_map.get(link['equipment_label'])
        con_id = con_map.get(link['consumable_label'])
        if eq_id and con_id:
            con.execute(
                "INSERT INTO links_consumables (link_id, equipment_id, consumable_id, qty_per_use, note) VALUES (?,?,?,?,?)",
                [str(uuid.uuid4()), eq_id, con_id, link.get('qty_per_use',1), link.get('note','')]
            )
            results['links'].append({'type': 'consumable', 'equipment': link['equipment_label'], 'consumable': link['consumable_label']})
        else:
            results['errors'].append(f"link_con: equipment '{link['equipment_label']}' ou consumable '{link['consumable_label']}' introuvable")

import json as _json
print(_json.dumps(results, indent=2))
```

Exécuter via :
```bash
ssh -i /sessions/upbeat-compassionate-franklin/mnt/.ssh/codex_vps_tailscale_ed25519 \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=no root@100.104.236.78 \
  "python3 << 'PYEOF'
{SCRIPT ICI}
PYEOF"
```

Récupérer le JSON retourné (stdout) pour obtenir les IDs créés.

### 5c. Upload photos via webhook n8n

Pour chaque photo associée à une entité, lire le fichier depuis le répertoire uploads et l'envoyer au webhook n8n.

D'abord, lire le token depuis l'environnement n8n :
```bash
ssh -i /sessions/upbeat-compassionate-franklin/mnt/.ssh/codex_vps_tailscale_ed25519 \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=no root@100.104.236.78 \
  "docker exec root-n8n-1 printenv SIGA_CLAUDE_WEBHOOK_TOKEN"
```

Ensuite, pour chaque photo :
```python
import base64, json, urllib.request

upload_path = '/sessions/upbeat-compassionate-franklin/mnt/uploads/{NOM_FICHIER}'
with open(upload_path, 'rb') as f:
    data_b64 = base64.b64encode(f.read()).decode()

payload = json.dumps({
    'siga_token': '{TOKEN_LU}',
    'entity_type': '{equipment|accessory|consumable}',
    'entity_id': '{UUID_CREE_EN_5b}',
    'role': '{overview|nameplate|detail}',
    'filename': '{NOM_FICHIER}',
    'mime_type': 'image/jpeg',
    'data_base64': data_b64,
    'ingestion_id': '{INGESTION_ID}'
}).encode()

req = urllib.request.Request(
    'https://n8n.srv961978.hstgr.cloud/webhook/siga-media-upload',
    data=payload,
    headers={'Content-Type': 'application/json'},
    method='POST'
)
with urllib.request.urlopen(req, timeout=60) as resp:
    print(json.loads(resp.read()))
```

Exécuter directement dans `mcp__workspace__bash` (pas via SSH — la sandbox a accès à internet et aux uploads locaux).

Si le webhook retourne `{"ok": true, "media_id": "...", "web_view_link": "..."}` → succès.
Si le webhook retourne une erreur ou n'est pas joignable → signaler à l'utilisateur, continuer sans photo plutôt que d'échouer toute l'ingestion.

---

## ÉTAPE 6 — Rapport final

Après l'exécution complète, afficher :

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ INGESTION TERMINÉE — {ingestion_id}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Créé :
  🔧 [N] équipement(s)  — IDs : ...
  🔌 [N] accessoire(s)  — IDs : ...
  📌 [N] consommable(s) — IDs : ...
  🔗 [N] lien(s)

Photos :
  ✅ [N] uploadée(s) sur Drive
  🔗 [lien Drive si disponible]

Erreurs éventuelles :
  ⚠️  [message si applicable]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Prérequis (configuration unique)

Avant la première utilisation, l'utilisateur doit :

1. **Créer le dossier Google Drive** `SIGA_CLAUDE_INGEST` (dans le même Google Drive que SIGA_TEMP et SIGA)
2. **Récupérer son ID Drive** (depuis l'URL du dossier dans le navigateur)
3. **Ajouter les variables dans le `.env` du VPS** (fichier `/root/.env`) :
   ```
   SIGA_CLAUDE_FOLDER_ID=<id_du_dossier_google_drive>
   SIGA_CLAUDE_WEBHOOK_TOKEN=<token_secret_au_choix>
   ```
4. **Redémarrer n8n** : `docker compose restart n8n` depuis `/root/`
5. **Importer le workflow** `SIGA-Claude-Media-Upload-workflow.json` dans n8n et l'activer
