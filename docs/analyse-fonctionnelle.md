# Analyse fonctionnelle — SIGA

**Système d'Ingestion et de Gestion d'Atelier**
Version du document : 2026-03-17

---

## 1. Contexte et objectif

SIGA est un système d'inventaire d'atelier mono-utilisateur conçu pour un usage opérationnel immédiat.
L'objectif central est de permettre à Nicolas d'enregistrer un équipement en envoyant une photo sur WhatsApp, sans friction, sans formulaire, sans saisie manuelle.

Le système est déjà partiellement opérationnel. La priorité n'est pas de le refondre mais de prouver un run réel bout-en-bout et de corriger les points de rupture constatés.

---

## 2. Architecture générale

```
WhatsApp (opérateur)
       |
       | message "SIGA: ..." + photo(s)
       v
  OpenClaw Gateway
       |
       | webhook POST /siga-ingestion-v1
       v
  n8n (SIGA - Ingestion Atelier V1)
       |
       |-- OpenAI Vision (OCR / classification)
       |-- Google Drive (stockage images)
       |-- DuckDB (base métier locale)
       |-- Google Sheets (projection export)
       |
       | message de complétion
       v
  WhatsApp (opérateur)

  Google Sheets
       |
       v
  AppSheet (application mobile atelier)

  DuckDB
       |
       v
  Streamlit (interface admin desktop)
```

---

## 3. Flux principal d'ingestion

### 3.1 Déclenchement

- L'opérateur envoie un message WhatsApp commençant par `SIGA:` accompagné d'une ou plusieurs photos de la plaque signalétique de l'équipement.
- Le routeur local (OpenClaw) détecte le préfixe et transfère la requête au webhook n8n.
- Le webhook répond immédiatement (ACK) ; le traitement se poursuit en asynchrone.

### 3.2 Normalisation et journalisation

- Extraction des faits du message : texte, images, métadonnées.
- Création d'un `ingestion_id` unique.
- Écriture d'un log initial dans `equipment_ingestion_log` (DuckDB).

### 3.3 Stockage temporaire Drive

- Création (ou réutilisation) d'un dossier temporaire `SIGA_TEMP/{ingestion_id}` à la racine de Google Drive.
- Upload des images reçues dans ce dossier temporaire.

### 3.4 OCR et classification

- Chaque image est envoyée à OpenAI Vision.
- L'IA extrait : marque, modèle, numéro de série, état, catégorie.
- Les résultats sont fusionnés avec les métadonnées Drive.
- Un flag `review_required` est positionné si la confiance est insuffisante.

### 3.5 Construction du draft équipement

- Assemblage des champs du draft (`Build Draft v2`).
- Application des règles de statut : `draft` / `review_required` / `validated`.
- Validation de la structure avant écriture.

### 3.6 Finalisation Drive

- Résolution ou création de l'arborescence finale :
  ```
  {drive_root}/SIGA/onboarding/{année}/{mois}/{ingestion_id}/
  ```
- Déplacement et renommage des fichiers depuis le dossier temporaire.
- Génération et upload d'un fichier `manifest_{ingestion_id}.json`.

### 3.7 Écriture base métier

- Écriture dans `equipment` (fiche équipement).
- Écriture dans `equipment_media` (références images).
- Mise à jour du log `equipment_ingestion_log`.

### 3.8 Complétion

- Construction du message de réponse WhatsApp.
- Envoi via HTTP vers OpenClaw `/tools/invoke` (outil `message`).
- L'opérateur reçoit un récapitulatif de l'équipement ingéré.

---

## 4. Composants techniques

| Composant | Rôle | Technologie |
|---|---|---|
| Gateway WhatsApp | Réception / envoi messages | OpenClaw (local) |
| Orchestration | Pipeline d'ingestion | n8n (Docker, VPS) |
| Vision / OCR | Analyse image, extraction données | OpenAI Vision |
| Stockage images | Archivage et arborescence | Google Drive |
| Base métier | Persistance structurée | DuckDB (`/files/duckdb/siga_v1.duckdb`) |
| Export inventaire | Projection données pour mobile | Google Sheets |
| Application mobile | Consultation inventaire atelier | AppSheet |
| Interface admin | Validation, dashboard, write-back | Streamlit |

---

## 5. Modèle de données (DuckDB)

### `equipment`
Fiche principale de chaque équipement ingéré.
Champs clés : `equipment_id`, `ingestion_id`, `category`, `brand`, `model`, `serial_number`, `condition_label`, `status`, `location_hint`, `review_required`, `business_context_json`.

### `equipment_media`
Références aux fichiers image finaux sur Drive.
Champs clés : `media_id`, `equipment_id`, `drive_file_id`, `drive_web_content_link`, `index`.

### `equipment_ingestion_log`
Traçabilité complète de chaque run d'ingestion : entrée, sorties, erreurs, timings.

### `equipment_review_log`
Historique des validations / corrections admin.

---

## 6. Schéma Google Sheets (projection export)

Voir `docs/google-sheets-schema.md` pour le détail des colonnes.

L'URL image dans le Sheet doit utiliser le format `https://drive.google.com/uc?export=view&id=FILE_ID` pour un rendu correct dans AppSheet.

---

## 7. Application mobile — AppSheet

AppSheet lit le Google Sheet et génère une application mobile native.

Vues prévues :
- **Liste** : tous les équipements, tri par date d'ingestion
- **Détail** : fiche complète avec photo, marque, modèle, état, emplacement
- **Galerie** : grille d'images
- **À valider** : équipements flaggés `review_required = true`

Filtres utiles : catégorie, statut, état.

Mode gratuit (Prototype) suffisant pour un usage mono-utilisateur.

---

## 8. Interface admin — Streamlit

Interface desktop pour l'opérateur.

Fonctionnalités prévues :
- Dashboard KPI (nb équipements, taux de validation, catégories)
- Inventaire filtrable
- Galerie image
- File de validation : proposition IA + image + bouton accepter / corriger
- Write-back DuckDB sur validation

---

## 9. Décisions structurantes

| ID | Décision |
|---|---|
| D001 | Déploiement Docker sur VPS dédié |
| D002 | Mode mono-utilisateur (Nicolas) |
| D003 | Canal d'entrée : WhatsApp avec préfixe `SIGA:` |
| D004 | OCR et Vision via API OpenAI |
| D005 | Stockage images : Google Drive |
| D006 | Base métier : DuckDB local (acceptable MVP) |
| D007 | Interface admin : Streamlit |
| D008 | Application mobile : AppSheet (remplace Glide) |
| D009 | Mode réponse webhook : ACK rapide + traitement asynchrone |
| D010 | Envoi message final : HTTP vers OpenClaw `/tools/invoke` |
| D011 | Conservation scans CNI : max 2 semaines après retour sans dommage |
| D012 | Emplacements atelier : référentiel modifiable (table DuckDB ou liste Streamlit) |

---

## 10. État d'avancement

### Implémenté et à valider en réel

- Pipeline n8n complet (71 nœuds, webhook actif)
- OCR OpenAI inline
- Arborescence Drive (correction du bug `folderId root` appliquée)
- Manifest Drive (correction du bug `name/content vide` appliquée)
- Écriture DuckDB (`equipment`, `equipment_media`, `ingestion_log`)
- ACK asynchrone + message final via OpenClaw

### En attente de validation

- Run réel documenté WhatsApp → complétion
- Projection Google Sheets (nœud n8n à créer)
- Application AppSheet (en attente du Google Sheet)
- Interface Streamlit (cadre existant, à compléter)

### Problème connu — multi-photos

OpenClaw transmet actuellement `mediaCount: 1` même si le message WhatsApp contient plusieurs photos. Le workflow est conçu pour traiter N images en parallèle mais ne peut traiter que ce qu'il reçoit.

**Action requise :** Configurer OpenClaw pour envoyer toutes les images dans un seul appel webhook avec la structure :
```json
{
  "images": [
    { "index": 1, "filename": "img1.jpg", "mime_type": "image/jpeg", "content_base64": "..." },
    { "index": 2, "filename": "img2.jpg", "mime_type": "image/jpeg", "content_base64": "..." }
  ]
}
```

---

## 11. Dette technique n8n (à traiter après stabilisation)

| ID | Description |
|---|---|
| DT-01 | Global Error Handler (`workflow_error_log` DuckDB) |
| DT-02 | Sous-workflow `Drive - Ensure Folder Exists` (dédupliquer les 4 blocs Search/If/Create) |
| DT-03 | Découpage du nœud `Merge OCR + Drive Metadata` en 3 nœuds atomiques |
| DT-04 | Découpage du nœud `Build Draft v2` en 3 nœuds atomiques |
| DT-05 | Nœud de projection Google Sheets |
| DT-06 | Configuration AppSheet complète |

---

## 12. Prochaine étape immédiate

1. Réimporter `SIGA-Ingestion-Atelier-V1.reference.json` dans n8n.
2. Envoyer un message WhatsApp `SIGA: test` avec 3 photos.
3. Capturer le run n8n complet (logs, arborescence Drive, tables DuckDB).
4. Corriger le premier point cassant observé.
5. Rejouer avant toute refactorisation.
