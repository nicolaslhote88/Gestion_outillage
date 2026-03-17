# SIGA Workflow Live Summary

- Generated on: `2026-03-14`
- Source export: `C:\Users\nicol\Downloads\SIGA - Ingestion Atelier V1.json`
- Workflow name: `SIGA - Ingestion Atelier V1`
- Workflow id: `THVms0LwRMEn0kBY`
- Version id: `25f31c92-60bd-475f-82b7-b2d72854c82d`
- Active: `true`
- Webhook: `POST siga-ingestion-v1`
- Node count: `71`

## Node types

- `n8n-nodes-base.code`: 39
- `n8n-nodes-base.if`: 12
- `n8n-nodes-base.googleDrive`: 11
- `n8n-nodes-base.httpRequest`: 7
- `n8n-nodes-base.webhook`: 1
- `@n8n/n8n-nodes-langchain.openAi`: 1

## Execution spine

1. `Webhook In` -> `Normalize Input` -> `Input Gate`
2. `DuckDB Init Schema` -> `Write Ingestion Log` -> `Extract Message Facts`
3. Temp folder resolution in Google Drive, with reuse if already known
4. Image split, temp upload, OCR inline via OpenAI, metadata merge
5. `Build Draft v2` and finalization planning
6. Final folder resolution and creation: base -> onboarding -> year -> month -> ingestion
7. Temp file move, final rename, manifest upload
8. DuckDB writes for `equipment`, `equipment_media`, and final ingestion log update
9. Final response and WhatsApp completion message preparation

## External dependencies

- Google Drive credential name: `Google Drive account`
- OpenAI credential name: `OpenAi account`
- DuckDB path used in Python nodes: `/files/duckdb/siga_v1.duckdb`

## Corrections appliquees

### 2026-03-14 — Bug arborescence Drive (folderId root en dur)

Tous les noeuds `Create *` avaient leur `folderId` fige a `root` (racine My Drive).
Chaque dossier etait donc cree a plat a la racine au lieu d'etre imbrique.

Noeuds corriges (6) :

| Noeud | Parent avant (bug) | Parent apres (correct) |
|---|---|---|
| `Create Temp Drive Folder` | root | `$json.temp_root_folder_id` |
| `Create Final Base Folder` | root | `$json.finalization_plan.default_drive_root_id` |
| `Create Final Onboarding Folder` | root | `$json.final_base_folder_id` |
| `Create Final Year Folder` | root | `$json.final_onboarding_folder_id` |
| `Create Final Month Folder` | root | `$json.final_year_folder_id` |
| `Create Final Ingestion Folder` | root | `$json.final_month_folder_id` |

Note : `Create Temp Root Folder` reste a `root` — correct, SIGA_TEMP est bien a la racine.

Arborescence attendue apres correction :
```
My Drive/
  SIGA_TEMP/
    {ingestion_id}/
  {default_drive_root}/
    SIGA/
      onboarding/
        {year}/
          {month}/
            {ingestion_id}/
```

### 2026-03-14 — Bug manifest vide et sans nom (Upload Manifest)

Le noeud `Upload Manifest` utilisait l'operation `createFromText` mais n'avait
ni `name` ni `content` definis — le fichier etait donc cree sans nom et sans contenu.

Parametres ajoutes :

| Parametre | Valeur |
|---|---|
| `name` | `={{'manifest_' + $json.ingestion_id + '.json'}}` |
| `content` | `={{$json.manifest_json_text}}` |

Le contenu JSON est produit par `Build Manifest Text` (champ `manifest_json_text`).

---

## Probleme : 3 photos envoyees -> 1 seule archivee

**Cause : connecteur OpenClaw / WhatsApp, pas le workflow.**

Le webhook n8n recoit `mediaCount: 1` et `images: [1 item]` meme si le message
WhatsApp contient 3 photos. Le workflow est concu pour traiter N images en parallele
(Split Images -> OCR -> Merge -> Build Draft), mais si le connecteur n'en transmet
qu'une seule, une seule est traitee.

**Action requise :** Configurer OpenClaw pour envoyer toutes les images en une seule
requete webhook, avec la structure :
```json
{
  "images": [
    { "index": 1, "filename": "img1.jpg", "mime_type": "image/jpeg", "content_base64": "..." },
    { "index": 2, "filename": "img2.jpg", "mime_type": "image/jpeg", "content_base64": "..." },
    { "index": 3, "filename": "img3.jpg", "mime_type": "image/jpeg", "content_base64": "..." }
  ]
}
```

---

## Immediate focus

- Reimporter `tmp/siga_workflow_live.json` dans n8n pour appliquer les 2 corrections
- Verifier que le manifest est bien cree avec nom et contenu JSON dans le dossier final
- Configurer OpenClaw pour envoyer toutes les photos dans un seul webhook
- Faire un run reel avec 3 photos et verifier arborescence + manifest + nb photos
