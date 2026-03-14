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

## Immediate focus

- Reimporter `tmp/siga_workflow_live.json` dans n8n pour appliquer la correction
- Faire un run reel WhatsApp et verifier que l'arborescence Drive est correcte
- Capturer les erreurs exactes si un autre point casse
