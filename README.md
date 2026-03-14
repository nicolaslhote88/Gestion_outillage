# Gestion_outillage

Base de travail GitHub du projet SIGA pour stabiliser le pipeline d'ingestion atelier deja implemente dans n8n.

La priorite n'est pas de refondre le produit. L'ordre de travail retenu est:

1. prouver un run reel WhatsApp -> fin de traitement,
2. corriger ce qui casse encore,
3. seulement ensuite resserrer la robustesse et la dette d'orchestration n8n.

## Ce que contient ce depot

- `SIGA-Ingestion-Atelier-V1.drivefix.final.json`: export n8n canonique de reference.
- `tmp/siga_workflow_live.json`: copie de travail du workflow live actuel.
- `tmp/siga_workflow_live_summary.md`: synthese lisible du workflow exporte.
- `docs/current-state.md`: decisions projet et etat reel du pipeline.
- `docs/live-validation-checklist.md`: checklist pour prouver le fonctionnement de bout en bout.

## Cadrage actuel

- Le pipeline live exporte est deja structure autour d'un webhook n8n, d'une orchestration DuckDB, de Google Drive et d'une analyse image OpenAI.
- Le front mobile ne part plus sur Glide. L'hypothese de travail retenue est AppSheet sur Google Sheet, en surcouche du pipeline n8n.
- Ce depot sert d'abord a figer l'existant et a produire des preuves de fonctionnement reel avant toute refactorisation.

## Prochaine etape

Executer un test reel documente sur le webhook `siga-ingestion-v1`, capturer le resultat de bout en bout et corriger les points de rupture observes.
