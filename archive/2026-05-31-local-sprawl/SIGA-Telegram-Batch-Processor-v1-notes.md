# SIGA Telegram Batch Processor v1

Objectif : agréger les parts Telegram déjà tamponnées dans `SIGA_TEMP/SIGA_TELEGRAM_BUFFER`, reconstruire un payload unique, puis appeler le webhook principal SIGA une seule fois.

## Hypothèses
- Le collector validé écrit, pour chaque part Telegram :
  - un JSON `tg_<group>__part_<message_id>.json`
  - un média `tg_<group>__part_<message_id>__<filename>`
- Le dossier tampon unique est `SIGA_TEMP/SIGA_TELEGRAM_BUFFER`
- Son ID conservé est `1tbyhFwLBWm5jh605vngOqpHN-Gpb-9yf`

## Variables d'environnement lues si disponibles
- `SIGA_TELEGRAM_BUFFER_FOLDER_ID`
- `SIGA_MAIN_WEBHOOK_URL`
- `SIGA_WEBHOOK_SECRET` / `SIGA_SHARED_SECRET` / `N8N_SIGA_WEBHOOK_SECRET` / `N8N_SIGA_SHARED_SECRET`

## Fonctionnement
1. Cherche tous les JSON tampon dans le dossier buffer
2. Télécharge et parse les JSON part
3. Sélectionne le plus ancien groupe prêt (`quiet_until <= now`)
4. Vérifie l'absence de marqueur `__processed.json`
5. Cherche les fichiers du groupe
6. Télécharge les médias du groupe
7. Construit un payload canonique compatible avec le webhook principal :
   - `source = telegram_buffered`
   - `message.text`
   - `images[]` avec `content_base64`
   - `context.telegram`
8. Appelle le webhook principal SIGA une seule fois
9. Écrit un marqueur `processed.json` pour éviter les retraitements

## Remarque importante
Cette v1 traite **un seul groupe prêt par exécution** pour limiter les régressions et éviter les collisions.
