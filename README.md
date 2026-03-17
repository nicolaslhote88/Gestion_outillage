# SIGA — Gestion Outillage Atelier

Système d'Ingestion et de Gestion d'Atelier (SIGA).
Pipeline opérationnel basé sur n8n, WhatsApp, OpenAI Vision, Google Drive et DuckDB.

## Ce que contient ce dépôt

| Fichier / Dossier | Contenu |
|---|---|
| `SIGA-Ingestion-Atelier-V1.reference.json` | Export n8n de référence (version validée) |
| `SIGA-workflow-reference-summary.md` | Résumé lisible du workflow n8n |
| `docs/analyse-fonctionnelle.md` | Analyse fonctionnelle complète du projet |
| `docs/current-state.md` | État opérationnel du pipeline |
| `docs/google-sheets-schema.md` | Schéma de projection Google Sheets |
| `docs/appsheet-integration.md` | Détail intégration AppSheet |
| `docs/live-validation-checklist.md` | Checklist de validation bout-en-bout |
| `docs/BACKLOG_SIGA.md` | Backlog par épic |
| `docs/DECISIONS_LOG_SIGA.md` | Journal des décisions projet |
| `Site dashboard/app.py` | Interface admin Streamlit |
| `Site dashboard/requirements.txt` | Dépendances Python |

## Flux résumé

```
WhatsApp → OpenClaw → n8n → OpenAI Vision + Google Drive + DuckDB → WhatsApp
                       ↓
               Google Sheets → AppSheet (mobile)
                       ↓
                  DuckDB → Streamlit (admin)
```

## Prochaine étape

Réimporter `SIGA-Ingestion-Atelier-V1.reference.json` dans n8n et exécuter un run réel documenté :
message WhatsApp `SIGA:` + photos → capturer le run complet → corriger le premier point cassant.

Voir `docs/analyse-fonctionnelle.md` pour le détail complet.
