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

## Skill agent SIGA

Claude a ajouté un skill local dans `skills/siga-saisie-atelier/`.

Son rôle est de donner à un agent les règles opérationnelles pour traiter les briefs Drive:

- accès API uniquement via SSH + `docker exec` dans le conteneur `siga-dashboard`;
- token lu depuis `SIGA_API_TOKEN` dans le conteneur, jamais stocké dans le dépôt;
- aucune écriture directe dans DuckDB;
- aucune modification Drive hors des endpoints `/api/drive/*`;
- vérification des doublons et confirmation utilisateur avant création ou liaison.
- analyse enrichie des fiches: photos, photo principale, quantités/unités, liens, documentation
  fabricant, stockage, entretien et dépannage.

Ce skill est la matérialisation agent du flux cible décrit dans ce dépôt. Il ne remplace pas la
notice API principale `docs/SIGA-API-OpenClaw-Notice.md`; il la rend actionnable côté agent.
Le brief de démarrage d'une session agent est versionné dans
`skills/siga-saisie-atelier/references/session-brief.md`.

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

Nettoyage effectué après incident:

- 21 dossiers packages dupliqués avaient été créés pour `album_-4887456379_14241702979869292`;
- le premier dossier a été conservé: `1Q9Bny2dCaQl9TxX8cE6P9w8hoA66D9rF`;
- les 20 dossiers suivants ont été mis à la corbeille Drive via `SIGA — Delete Equipment Drive Folder`.

## Correctif Google Drive n8n du 29/06/2026

Les runs `SIGA - Telegram Batch Processor v8 (Loop Clean)` échouaient sur le noeud
`Search Buffered JSON Parts` avec une erreur OAuth Google Drive `invalid_grant`.

Correctif appliqué sur n8n:

- création d'une credential n8n `SIGA Google Drive Service Account` de type `googleApi`,
  basée sur le compte de service déjà utilisé par l'API SIGA;
- repointage des workflows SIGA Drive vers cette credential service account;
- mise à jour des versions publiées actives (`activeVersionId`) puis redémarrage n8n.

Ne pas revenir à la credential OAuth utilisateur `Google Drive account` pour les flux SIGA: elle
peut expirer ou être révoquée et bloquer l'activation du batch processor.

## Prochaine validation

Faire un test réel Telegram:

1. envoyer un brief avec plusieurs photos;
2. vérifier qu'un seul dossier final est créé dans Drive;
3. vérifier que toutes les photos sont présentes;
4. vérifier que `brief.json` contient le texte, les photos, les métadonnées Telegram et `status: "pending"`;
5. traiter manuellement ce dossier avec l'agent SIGA.
