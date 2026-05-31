# SIGA - Gestion Outillage Atelier

SIGA est le système d'inventaire de l'atelier. Le dépôt sert désormais à documenter et versionner le flux simple et opérationnel:

1. Nicolas envoie un brief + des photos depuis Telegram.
2. n8n dépose un dossier Google Drive contenant les photos et un `brief.json`.
3. Le brief reste en `pending`.
4. Quand Nicolas est devant son PC, un agent dédié analyse le dossier et agit sur SIGA via l'API décrite dans `docs/SIGA-API-OpenClaw-Notice.md`.

L'ancien pipeline qui tentait de tout analyser automatiquement au moment de l'envoi terrain est conservé comme legacy, mais il n'est plus le flux cible.

## Structure

| Dossier / fichier | Rôle |
|---|---|
| `docs/analyse-fonctionnelle.md` | Cadrage fonctionnel cible |
| `docs/current-state.md` | Etat réel au 31/05/2026 |
| `docs/SIGA-API-OpenClaw-Notice.md` | Notice API utilisée par l'agent SIGA |
| `docs/brief.schema.json` | Contrat du `brief.json` déposé dans Drive |
| `workflows/live/` | Workflows n8n qui composent le flux cible |
| `workflows/support/` | Workflows utilitaires conservés |
| `workflows/archive/` | Workflows legacy exportés pour mémoire |
| `Site dashboard/` | Portail Streamlit SIGA et API locale |
| `archive/2026-05-31-local-sprawl/` | Exports et variantes locales rangés hors flux officiel |

## Flux cible

```
Telegram
  -> SIGA - Telegram Collector Buffer v2
  -> SIGA - Telegram Batch Processor v8
  -> Google Drive / SIGA_ATELIER / <brief_id>/
       - brief.json
       - photos

Puis, plus tard:

Agent SIGA + docs/SIGA-API-OpenClaw-Notice.md
  -> lecture du dossier Drive
  -> analyse visuelle et décision
  -> API SIGA / DuckDB / Drive
  -> brief.json passe de pending a processed
```

## Etat n8n attendu

Actifs:

- `SIGA - Telegram Collector Buffer v2`
- `SIGA - Telegram Batch Processor v8 (Loop Clean)`
- `SIGA - Claude Media Upload`
- `SIGA - Global Error Handler`
- `SIGA - Delete Equipment Drive Folder`

Archivé / désactivé:

- `SIGA - Ingestion Atelier V1`

## Prochaine validation

Envoyer depuis Telegram un message avec plusieurs photos et une légende. Résultat attendu: un seul dossier Drive, un seul `brief.json`, toutes les photos, statut `pending`.
