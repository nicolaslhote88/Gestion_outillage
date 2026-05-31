# Workflow n8n — Saisie terrain Telegram → Drive (SIGA)

Reçoit texte + photo(s) depuis Telegram, dépose un dossier dans Google Drive contenant
`brief.json` (statut `pending`) + les photos, puis envoie une confirmation Telegram avec le lien
du dossier. Claude Cowork traite ensuite les briefs `pending`.

## Fichiers

- `workflow_SIGA_telegram_to_drive.json` — workflow (déjà importé/actif dans n8n ; source de référence).
- `brief.schema.json` — contrat de données (schéma JSON du `brief.json`).
- `README_workflow_telegram.md` — ce document.

## Flux (18 nœuds)

```
Telegram (texte + photo)
   → Normalisation (Code : texte, photo en binaire 'data', media_group_id)
   → Rechercher dossier brief existant
   → Créer ou réutiliser dossier brief sous SIGA_ATELIER
   → A une photo ?
        ├─ [oui] Prep upload photo → Upload photo
        │        → Si album Telegram : buffer + attente 8 s → finalisation unique
        │        → Sinon : brief direct
        └─ [non] ─────────────────────────────────────────────────────→ │
   → Construire brief.json (statut pending)
   → Upload brief.json
   → Confirmation Telegram (lien du dossier)
```

## État : DÉPLOYÉ — bascule collector + batch processor (31/05/2026)

Le 1er test réel (envoi d'une photo) a échoué. J'ai lu les **logs d'exécution réels**
(table `execution_data` de la base n8n), trouvé la cause, et corrigé.

| Élément | Valeur (vérifiée) |
|---|---|
| Ancien workflow direct n8n | `8ktbbkVySbjkeaA9` — « SIGA - Telegram vers Drive (brief terrain) » — **supprimé le 31/05/2026** |
| Collector Telegram | `4clGC7a9OM8HiRRCowgIb` — « SIGA - Telegram Collector Buffer v2 » — **actif** |
| Processeur batch | `fNFsT1JoloWVTLa0Q8z_E` — « SIGA - Telegram Batch Processor v8 (Loop Clean) » — **actif** |
| Credential Telegram | `pVqYKOVuJrq3njUz` — « Telegram Jarvis Bot » |
| Credential Drive | `pfuF6ArTiTPmlpHG` — « Google Drive account » |
| Dossier d'arrivée des briefs | `1qWGmTUyJ3qCWWb02j1Ka9rsRGyJArh-N` — **`SIGA_ATELIER`** (sous My Drive) |
| Base de données n8n | **SQLite** (`/home/node/.n8n/database.sqlite`) |

Les dossiers de briefs finaux sont créés **directement sous `SIGA_ATELIER`** par le processeur batch.
Pour un message simple : `<YYYYMMDD-HHmmss>_msg<id>`.
Pour un album Telegram : `album_<chat_id>_<media_group_id>`.

Depuis le correctif album, le flux direct n'est plus présent dans n8n. Le collector écrit
d'abord les parts dans `SIGA_TEMP/SIGA_TELEGRAM_BUFFER`; le batch processor tourne chaque minute,
attend que la fenêtre calme soit passée, puis crée un seul dossier final avec toutes les photos et
un seul `brief.json`.

## Cause racine réelle du bug : le binaire de la photo était perdu

Erreur exacte renvoyée par le nœud **Upload photo** (lue dans `execution_data`) :

> `This operation expects the node's input data to contain a binary file 'data', but none was found`

Le nœud Google Drive « Créer dossier brief » **remplace l'item** par les métadonnées du dossier
créé et **abandonne le binaire** de la photo. Quand « Upload photo » s'exécutait juste après, le
fichier binaire `data` n'existait plus → erreur, et le workflow s'arrêtait avant d'écrire
`brief.json` ou d'envoyer la confirmation.

Important : la **création du dossier réussissait** à chaque essai. L'accès Drive n'a donc jamais
été le problème (une hypothèse antérieure de ce doc parlant de « scope `drive.file` / 404 » était
fausse — le scope de la credential est en réalité `…/auth/drive` complet).

**Correctif :** un nœud Code « **Prep upload photo** » est inséré entre l'IF (branche oui) et
« Upload photo ». Il reconstruit l'item en réattachant le binaire depuis `Normalisation`
(`$('Normalisation').item.binary`) et en fournissant l'`id` du dossier. « Upload photo » lit alors
un item contenant bien le binaire `data`. Workflow passé de 8 à 9 nœuds.

## Albums Telegram

Telegram envoie chaque photo d'un album comme un message séparé, mais avec le même
`media_group_id`. Le workflow utilise maintenant ce champ pour :

1. créer/réutiliser un seul dossier Drive par album ;
2. uploader chaque photo dans ce dossier ;
3. garder en mémoire les photos et la légende pendant une fenêtre calme de 8 secondes ;
4. écrire un seul `brief.json` avec toutes les photos et le texte de la légende ;
5. envoyer une seule confirmation Telegram.

## Effet de bord assumé (intentionnel)

« SIGA - Telegram Collector Buffer v2 » (`4clGC7a9OM8HiRRCowgIb`) a été **désactivé** : un bot
Telegram ne délivre ses updates qu'à **un seul** workflow trigger à la fois, et l'ancien collector
monopolisait « Telegram Jarvis Bot ». Le nouveau flux le remplace.
Retour arrière : désactiver `8ktbbkVySbjkeaA9`, réactiver `4clGC7a9OM8HiRRCowgIb`, `docker restart root-n8n-1`.

## Test de bout en bout (à refaire par toi)

Renvoie une photo + légende au bot **Telegram Jarvis**. Attendu :

1. réponse du bot « Brief SIGA enregistre… » avec le lien du dossier ;
2. nouveau dossier `<YYYYMMDD-HHmmss>_msg<id>` sous `SIGA_ATELIER` ;
3. dans ce dossier : `brief.json` (statut `pending`) + la photo.

## Diagnostic des exécutions (méthode, pour la suite)

Le conteneur n'a ni `sqlite3` ni `psql`, mais le module npm `sqlite3` est présent dans n8n :

```
docker exec root-n8n-1 node -e "const s=require('/usr/local/lib/node_modules/n8n/node_modules/sqlite3')..."
```

Tables utiles : `execution_entity` (id, workflowId, status), `execution_data` (payload + erreurs,
format compact à références numérotées). La base se verrouille pendant un `docker restart` → attendre.

## Nettoyage à faire à la main (optionnel)

Les essais ratés ont laissé des **dossiers vides** datés (ex. `20260530-210035_msg419`) sous
`04_BRIEFS_COWORK` (`186uAGZXT_5NV_t6VMjuzPdkpYC1SmQ3k`, sous `OpenClaw-Inbox`). Inoffensifs ;
supprime-les à la main si tu veux (je n'ai pas d'outil de suppression Drive).

## Procédure de (ré)déploiement / maintenance

1. `scp` le JSON sur le VPS, `docker cp` dans `root-n8n-1:/home/node/`,
   puis `docker exec -u root root-n8n-1 chown node:node <fichier>` (sinon `EACCES` à l'import).
2. `docker exec root-n8n-1 n8n import:workflow --input=/home/node/<fichier>`.
   Le JSON contient `"id": "8ktbbkVySbjkeaA9"` → l'import **met à jour en place** (pas de doublon).
3. `n8n update:workflow --id=8ktbbkVySbjkeaA9 --active=true`.
4. **`docker restart root-n8n-1`** (obligatoire : « Changes will not take effect if n8n is running »).

## Points de vigilance

- **Binaire entre nœuds** : tout nœud Google Drive remplace l'item courant ; pour réutiliser un
  binaire ensuite, le réattacher via un nœud Code (cf. « Prep upload photo »).
- **Versions de nœuds** : googleDrive v3, if v2, telegram v1.2, telegramTrigger v1.1.
- **Téléchargement photo** : *Réception Telegram* a `Download Images/Files` activé
  (`additionalFields.download = true`). *Normalisation* range le binaire sous la clé `data`.
- **Albums Telegram** : chaque photo d'un album est un message séparé, avec le même
  `media_group_id`. La version actuelle regroupe ces messages dans un dossier unique et attend
  8 secondes avant de finaliser le `brief.json`.
- **Fuseau horaire** : `created_at` et le nom de dossier sont en **UTC**.

## Contrat avec Claude Cowork

Chaque dossier déposé contient :

```
SIGA_ATELIER/20260530-141205_msg482/
   ├── brief.json        (statut: "pending")
   └── photo_482.jpg
```

`brief.json` suit `brief.schema.json`. Champs clés pour Cowork : `text` (description terrain),
`photos[]`, `status` (`pending` → à traiter), `siga` (zone réservée à Cowork, laissée vide par n8n).

### Boucle de traitement Cowork (recommandée)

1. lister les dossiers de `SIGA_ATELIER` dont `brief.json` a `status: "pending"` ;
2. lire `text` + photos, mettre à jour l'inventaire SIGA ;
3. écrire dans `siga` (action, items, `processed_at`, notes) et passer `status` à `processed` ;
4. (option) archiver le dossier traité.

Le passage `pending → processed` rend le traitement **idempotent** : relancer Cowork ne retraite
jamais deux fois le même brief.
