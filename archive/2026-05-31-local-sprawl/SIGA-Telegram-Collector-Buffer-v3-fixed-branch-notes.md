# SIGA Telegram Collector Buffer v3 — correction branche buffer

## Problème corrigé
Le workflow v2 contenait des **connexions vers des nœuds inexistants** :
- Search Telegram Buffer Folder
- Resolve Telegram Buffer Folder
- If Telegram Buffer Folder Exists
- Create Telegram Buffer Folder
- Adopt Telegram Buffer Folder

Conséquence :
- la branche `If Temp Root Folder Exists` apparaissait cassée dans n8n
- le collector ne créait/utilisait pas correctement `SIGA_TEMP/SIGA_TELEGRAM_BUFFER`
- les fichiers tampon pouvaient tomber au mauvais endroit

## Correctifs appliqués
Ajout des 5 nœuds manquants :
1. Search Telegram Buffer Folder
2. Resolve Telegram Buffer Folder
3. If Telegram Buffer Folder Exists
4. Create Telegram Buffer Folder
5. Adopt Telegram Buffer Folder

## Nouveau flux
- Resolve Temp Root Folder
- If Temp Root Folder Exists
  - true -> Search Telegram Buffer Folder
  - false -> Create Temp Root Folder -> Adopt Temp Root Folder -> Search Telegram Buffer Folder
- Search Telegram Buffer Folder -> Resolve Telegram Buffer Folder -> If Telegram Buffer Folder Exists
  - true -> Build Buffer Context
  - false -> Create Telegram Buffer Folder -> Adopt Telegram Buffer Folder -> Build Buffer Context

## Effet attendu
Le collector doit maintenant écrire dans :
`SIGA_TEMP / SIGA_TELEGRAM_BUFFER`

avec :
- `tg_<group>__part_<message_id>.json`
- `tg_<group>__part_<message_id>__<filename>`

## Point de vérification
Après import :
- le nœud `If Temp Root Folder Exists` doit avoir sa branche true reliée
- `SIGA_TELEGRAM_BUFFER` doit être créé dans `SIGA_TEMP` s'il n'existe pas
- les JSON et les médias ne doivent plus être déposés à la racine
