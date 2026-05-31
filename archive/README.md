# Archive SIGA

Ce dossier contient les éléments conservés pour mémoire, hors flux officiel.

## `2026-05-31-local-sprawl/`

Rangement non destructif des exports et notes locales accumulés avant le réalignement du dépôt avec `origin/main`.

Contenu typique:

- anciennes variantes `SIGA-Telegram-Collector-Buffer-*`;
- anciennes variantes `SIGA-Telegram-Batch-Processor-*`;
- ancien workflow direct Telegram vers Drive;
- notes de debug et exports intermédiaires;
- copies locales de notice ou skill remplacées par les versions canoniques de `docs/`.

Ces fichiers ne sont pas une source de vérité. Ils ne doivent pas être réimportés dans n8n sans analyse explicite.

## `2026-05-31-legacy-auto-ingestion/`

Ancien export de référence du workflow automatique `SIGA - Ingestion Atelier V1` et son résumé.

Ce workflow représentait l'ancien modèle: analyser immédiatement le message terrain et écrire directement dans SIGA. Il est conservé pour mémoire, mais le flux cible passe désormais par un dossier Drive `pending` traité plus tard par l'agent SIGA.
