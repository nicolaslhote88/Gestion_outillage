# Workflows n8n SIGA

Ce dossier reflète l'état n8n réel au 31/05/2026.

## `live/`

Workflows du flux cible Telegram vers Drive:

- `SIGA-Telegram-Collector-Buffer-v2.json`
- `SIGA-Telegram-Batch-Processor-v8-Loop-Clean.json`
- `SIGA-Claude-Media-Upload.json`

## `support/`

Workflows utilitaires conservés:

- `SIGA-Global-Error-Handler.json`
- `SIGA-Delete-Equipment-Drive-Folder.live.json`

## `archive/`

Workflows conservés pour mémoire, mais hors flux cible:

- `legacy-auto-ingestion/SIGA-Ingestion-Atelier-V1.live-legacy.json`

L'ancien workflow `SIGA - Ingestion Atelier V1` est désactivé dans n8n. Il ne doit pas être réactivé sans décision explicite, car il correspond à l'ancien modèle d'analyse automatique immédiate.

## Règle de maintenance

Toute modification n8n doit être réexportée ici après validation réelle. Le dépôt doit rester aligné avec le système vivant.
