# Etat actuel SIGA

Date de référence: 2026-05-31

## Verdict

Le projet SIGA a été recentré. Le flux cible n'est plus une ingestion automatique complète au moment de l'envoi terrain. Le bon modèle est maintenant:

1. Telegram sert à capturer vite un brief terrain et des photos.
2. n8n dépose ces éléments dans Google Drive, sans décider à la place de Nicolas.
3. Un agent SIGA traite plus tard le dossier Drive, avec le contexte métier et les règles de `docs/SIGA-API-OpenClaw-Notice.md`.
4. L'agent agit sur la base SIGA et sur Drive en respectant la cohérence DuckDB + fichiers.

## Etat réel n8n

| Workflow | Etat | Rôle |
|---|---:|---|
| `SIGA - Telegram Collector Buffer v2` | actif | Reçoit les messages Telegram et bufferise les parts d'album |
| `SIGA - Telegram Batch Processor v8 (Loop Clean)` | actif | Regroupe les parts, crée le dossier final Drive, écrit `brief.json`, confirme Telegram |
| `SIGA - Claude Media Upload` | actif | Endpoint support pour uploader un média dans Drive depuis un agent |
| `SIGA - Global Error Handler` | actif | Gestion d'erreur n8n |
| `SIGA - Delete Equipment Drive Folder` | actif | Utilitaire de suppression de dossier Drive depuis le portail/API |
| `SIGA - Ingestion Atelier V1` | désactivé | Ancien workflow automatique complet, conservé en archive |

Exports associés:

- flux actif: `workflows/live/`
- utilitaires: `workflows/support/`
- ancien monolithe: `workflows/archive/legacy-auto-ingestion/`

## Etat du portail SIGA

Le portail `https://siga.nlhconsulting.fr` est restauré derrière Traefik.

Le service Docker `siga-dashboard` lance:

- Streamlit sur `8501`
- l'API SIGA interne sur `8001`
- le serveur MCP SIGA

La base utilisée est `/local-files/duckdb/siga_v1.duckdb`.

## Dossier Drive d'entrée

Le dépôt terrain se fait dans `SIGA_ATELIER`.

Pour un message simple:

```text
SIGA_ATELIER/<YYYYMMDD-HHmmss>_msg<message_id>/
  brief.json
  photo_<...>.jpg
```

Pour un album Telegram:

```text
SIGA_ATELIER/album_<chat_id>_<media_group_id>/
  brief.json
  photo_1.jpg
  photo_2.jpg
  ...
```

Le `brief.json` doit rester en `status: "pending"` tant que l'agent SIGA ne l'a pas traité.

## Ce qui est archivé

Les exports locaux dispersés, anciennes versions de collector et batch processor, notes intermédiaires et anciens essais ont été rangés dans:

```text
archive/2026-05-31-local-sprawl/
```

Ils restent consultables, mais ne sont plus la source de vérité.

## Risques à surveiller

- Le batch processor doit rester actif, sinon les messages Telegram restent uniquement en buffer.
- Telegram envoie chaque photo d'un album comme un message séparé: le regroupement dépend de `media_group_id`.
- L'agent SIGA ne doit jamais créer ou modifier une fiche sans maintenir la cohérence Drive + DuckDB.
- Les anciens workflows automatiques ne doivent pas être réactivés sans décision explicite.

## Correctif batch processor du 31/05/2026

Le batch processor avait un défaut critique: il créait le dossier final Drive, puis pouvait échouer sur la confirmation Telegram avant d'écrire le marqueur de traitement et avant de nettoyer le buffer. Résultat: le même groupe pouvait être retraité au run suivant et créer plusieurs dossiers packages.

Correctif appliqué:

- la confirmation Telegram est non bloquante;
- le nettoyage du buffer s'exécute même si un groupe est déjà marqué traité;
- les fichiers `__part_*.json`, médias bufferisés et marqueur `processed` du groupe courant sont supprimés après création du package;
- le marqueur est supprimé en dernier pour éviter un retraitement si une suppression intermédiaire échoue.

## Prochaine validation

Faire un test réel Telegram:

1. envoyer un brief avec plusieurs photos;
2. vérifier qu'un seul dossier final est créé dans Drive;
3. vérifier que toutes les photos sont présentes;
4. vérifier que `brief.json` contient le texte, les photos, les métadonnées Telegram et `status: "pending"`;
5. traiter manuellement ce dossier avec l'agent SIGA.
