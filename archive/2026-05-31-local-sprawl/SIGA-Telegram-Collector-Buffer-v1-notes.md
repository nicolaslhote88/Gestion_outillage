# SIGA Telegram Collector Buffer v1

But du workflow:
- Recevoir les messages Telegram (photos, documents, texte)
- Regrouper logiquement les messages d’un même album via `media_group_id`
- Bufferiser les éléments dans `SIGA_TEMP` sans toucher au workflow principal SIGA
- Éviter les accès concurrents DuckDB en ne faisant ici ni OCR ni écriture BDD métier

## Stratégie de buffer
- Dossier cible: `SIGA_TEMP`
- Pas de sous-dossier par groupe pour éviter les courses de création en parallèle
- Chaque run écrit des fichiers à nom déterministe:
  - JSON part: `<group_key>__part_<message_id>.json`
  - média: `<group_key>__part_<message_id>__<filename>`
- `group_key`:
  - album Telegram: `tg_<chat_id>_<media_group_id>`
  - message seul: `tg_<chat_id>_msg_<message_id>`

## Ce que fait le workflow
1. Déclenchement Telegram
2. Normalisation du message Telegram:
   - récupère `chat_id`, `message_id`, `media_group_id`
   - récupère `caption` / `text`
   - détecte `photo` / `document` / autre
3. Résout `SIGA_TEMP` dans Drive (réutilise s’il existe, crée sinon)
4. Écrit un JSON part dans `SIGA_TEMP`
5. S’il y a un binaire, l’upload aussi dans `SIGA_TEMP`
6. Retourne un résultat `stage=buffered` avec:
   - `group_key`
   - `buffer_prefix`
   - `quiet_until`
   - ids Drive créés/réutilisés

## Pourquoi ce design
Telegram envoie un album comme plusieurs messages distincts qui partagent `media_group_id`.
Ce workflow ne tente pas d’agréger immédiatement.
Il bufferise chaque part de façon idempotente et sans DuckDB.

## Prochaine étape
Créer un second workflow “processor” qui:
- scanne `SIGA_TEMP`
- regroupe par `buffer_prefix`
- attend une fenêtre de silence (ex. 5 s après `last_seen`)
- reconstruit un payload canonique
- appelle ensuite le workflow principal SIGA une seule fois par groupe
