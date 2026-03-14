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

## Immediate focus

- Prove one real end-to-end WhatsApp run
- Capture exact failures before any refactor
- Tighten orchestration only after the live path is stable
