---
name: siga-saisie-atelier
description: Saisir des outils, accessoires et consommables dans l'inventaire d'atelier SIGA via son API. Utilise ce skill DÈS QUE l'utilisateur veut ajouter, enregistrer, créer, inventorier ou lier un outil/équipement/accessoire/consommable dans SIGA, qu'il fournisse des photos, un texte, ou qu'il demande de traiter les "briefs" déposés depuis Telegram dans le Drive. Déclenche aussi sur "ajoute ça dans SIGA", "enregistre cette perceuse", "inventorie ces forets", "traite les briefs en attente", "lie cet accessoire à tel outil". Toutes les écritures passent EXCLUSIVEMENT par l'API SIGA — jamais d'écriture directe en base.
---

# SIGA — Saisie atelier (via API)

Ce skill enregistre des éléments d'atelier (équipements, accessoires, consommables), leurs
photos et leurs liaisons dans **SIGA**, en utilisant **uniquement l'API SIGA**. Aucune écriture
directe dans DuckDB ni manipulation Drive hors API n'est autorisée.

## Règle d'or

**Toute écriture passe par l'API SIGA. Jamais de SQL direct, jamais de modification Drive
hors des endpoints `/api/drive/*`.** Si une action n'a pas d'endpoint API, ne pas l'improviser :
le signaler à l'utilisateur.

## Accès à l'API (important — lire en premier)

L'API SIGA **n'est pas exposée sur Internet**. Le domaine `https://siga.nlhconsulting.fr`
route vers l'interface Streamlit derrière une basic-auth ; l'API (port 8001) n'écoute qu'en
local sur le VPS, dans le conteneur Docker `siga-dashboard`.

**Le seul accès depuis Cowork est : SSH vers le VPS → `docker exec siga-dashboard` → appel HTTP
local `http://127.0.0.1:8001`.** Le token d'auth est lu depuis la variable d'environnement
`SIGA_API_TOKEN` **du conteneur** — ne jamais l'écrire en dur, ne jamais le copier ailleurs.

Utilise le script fourni `scripts/siga_api.sh` qui encapsule tout ça :

```bash
# GET
scripts/siga_api.sh GET "/api/equipment/search?q=meuleuse"
# POST avec body JSON
scripts/siga_api.sh POST "/api/accessories" '{"label":"Batterie 18V 5Ah","brand":"Makita"}'
```

Le script gère : connexion SSH, `docker exec`, lecture du token depuis l'env du conteneur,
encodage du body, et renvoie la réponse JSON brute. Voir `references/access.md` pour le détail
et le dépannage (chemin de la clé SSH, nom du conteneur, codes d'erreur).

## Ce que l'API permet de créer

| Entité | Création via API ? | Endpoint |
|---|---|---|
| **Accessoire** | ✅ Oui | `POST /api/accessories` |
| **Consommable** | ✅ Oui | `POST /api/consumables` |
| **Équipement (outil)** | ❌ NON via API | créé par le pipeline n8n ; l'API permet seulement `PATCH`, photos, liaisons, archivage |

Conséquence : pour un **accessoire ou consommable**, le skill fait tout (création + photos +
liaisons). Pour un **équipement**, le skill ne peut pas le créer ; il faut soit qu'il existe
déjà (issu de n8n), soit prévenir l'utilisateur. Une fois l'équipement présent, le skill peut
le compléter (`PATCH`), lui attacher des photos et le lier à des accessoires/consommables.

Détail complet des endpoints, champs et exemples : **`references/api-reference.md`**.

## Workflow de saisie

### Étape 1 — Comprendre la demande et analyser les entrées

Identifier le **type** d'objet (équipement / accessoire / consommable) et extraire les
informations utiles :

- Depuis les **photos** : type d'objet, marque/modèle sur la plaque, état, références.
  Rôles de photo possibles : `overview` (vue d'ensemble), `nameplate` (plaque), `detail`.
- Depuis le **texte** : nom, marque, quantité, emplacement, notes.

Si la demande concerne des **briefs Telegram déjà déposés dans Drive** (dossiers `pending`),
voir la section « Traiter les briefs en attente » plus bas.

### Étape 2 — Vérifier les doublons AVANT toute création

Toujours chercher si l'objet existe déjà :

```bash
scripts/siga_api.sh GET "/api/equipment/search?q=<nom ou marque>"
scripts/siga_api.sh GET "/api/accessories?q=<nom>"
scripts/siga_api.sh GET "/api/consumables?q=<nom>"
```

L'API déduplique aussi automatiquement les accessoires/consommables (même label+marque+modèle/réf)
et renvoie l'existant. Forcer un doublon seulement si nécessaire avec `?force_create=true`.

### Étape 3 — Présenter un plan et demander confirmation

**Ne jamais créer ni lier sans confirmation explicite de l'utilisateur.** Présenter un plan
structuré : entité(s) à créer, champs renseignés, photos à attacher, liaisons proposées.

### Étape 4 — Créer

Accessoire :

```bash
scripts/siga_api.sh POST "/api/accessories" '{"label":"Batterie 18V 5Ah","brand":"Makita","model":"BL1850B","category":"Batterie","stock_qty":3,"location_hint":"Armoire A1","notes":"LXT"}'
```

Consommable :

```bash
scripts/siga_api.sh POST "/api/consumables" '{"label":"Foret SDS-Plus Ø10","brand":"Bosch","reference":"2608833800","category":"Foret","unit":"pcs","stock_qty":10,"stock_min_alert":5}'
```

Seul `label` est obligatoire. L'ID créé revient dans le champ **`link_id`** de la réponse
(convention API) — le réutiliser comme `accessory_id`/`consumable_id` pour les liaisons.

### Étape 5 — Photos (si l'objet en a)

Les photos doivent **déjà exister sur Drive** (l'API n'upload pas de binaire). Le mécanisme :
créer/identifier le dossier Drive de la fiche, y déplacer la photo, puis l'attacher par `file_id`.
Détail et exemples : `references/api-reference.md` § Photos. Règle absolue : **déplacer le fichier
ET mettre à jour la fiche** — jamais l'un sans l'autre.

### Étape 6 — Liaisons (écosystème de l'outil)

Lier un accessoire ou un consommable à un équipement :

```bash
scripts/siga_api.sh POST "/api/links/compatibility" '{"equipment_id":"<eq>","accessory_id":"<acc>","note":"..."}'
scripts/siga_api.sh POST "/api/links/consumables"  '{"equipment_id":"<eq>","consumable_id":"<con>","qty_per_use":2}'
```

Suggérer les liaisons pertinentes (ex. perforateur → forets SDS-Plus ; outil 18V Makita →
batteries/chargeurs Makita) mais **toujours confirmer avant de lier**.

### Étape 7 — Vérifier

Relire la fiche et confirmer à l'utilisateur :

```bash
scripts/siga_api.sh GET "/api/accessories/<id>"
scripts/siga_api.sh GET "/api/equipment/<id>"
```

## Traiter les briefs en attente (déposés depuis Telegram)

Le workflow n8n « brief terrain » dépose dans Drive des dossiers contenant `brief.json`
(`status: "pending"`) + photos. Pour les traiter :

1. lister les dossiers de briefs et lire les `brief.json` en `pending` ;
2. pour chacun : analyser `text` + photos, puis suivre le workflow de saisie ci-dessus
   (doublons → plan → confirmation → création/liaison via API) ;
3. après traitement, marquer le brief `processed` (réécriture du `brief.json`) pour rendre
   l'opération **idempotente** — ne jamais retraiter deux fois le même brief.

Le contrat `brief.json` et l'emplacement Drive sont décrits dans le projet
(`docs/brief.schema.json`, `docs/current-state.md`, `README.md`).

## Règles absolues (rappel)

- Écrire **uniquement** via l'API SIGA.
- **Confirmer** avant toute création, liaison ou réaffectation de photo.
- **Vérifier visuellement** chaque photo avant de l'attacher (ne jamais mettre la photo d'un
  objet A sur la fiche d'un objet B).
- Ne **jamais** créer une fiche sans son dossier Drive ; ne jamais réaffecter un `file_id`
  sans déplacer physiquement le fichier sur Drive.
- Ne **jamais** créer deux fiches pour le même objet — vérifier les doublons d'abord.
- Ne **jamais** écrire le token `SIGA_API_TOKEN` en dur ni le divulguer.

Pour le détail des endpoints, champs, codes d'erreur et processus complets (situations Q/M/O) :
**`references/api-reference.md`**. Pour l'accès technique et le dépannage : **`references/access.md`**.
