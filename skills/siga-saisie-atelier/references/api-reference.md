# Référence API SIGA — endpoints de saisie

Base interne : `http://127.0.0.1:8001` (accès via `scripts/siga_api.sh`, voir `access.md`).
Auth : `Authorization: Bearer $SIGA_API_TOKEN` (gérée par le script). Tous les endpoints
commencent par `/api`. Seul `/api/health` est sans auth.

Format d'erreur générique : `{ "ok": false, "error": "code", "detail": "..." }`.

## Sommaire

- [Recherche / vérification de doublons](#recherche--verification-de-doublons)
- [Créer un accessoire](#creer-un-accessoire)
- [Créer un consommable](#creer-un-consommable)
- [Lier accessoire ↔ équipement](#lier-accessoire--equipement)
- [Lier consommable ↔ équipement](#lier-consommable--equipement)
- [Équipement (pas de création via API)](#equipement-pas-de-creation-via-api)
- [Photos](#photos)
- [Drive](#drive)
- [Processus complets (situations Q / M / O)](#processus-complets)
- [Catégories suggérées](#categories-suggerees)

---

## Recherche / vérification de doublons

```
GET /api/equipment/search?q=<texte>     # outils ; max 20 résultats, score de pertinence
GET /api/accessories?q=<texte>          # catalogue accessoires
GET /api/consumables?q=<texte>&low_stock=true   # catalogue consommables (low_stock optionnel)
GET /api/admin/duplicates?threshold=0.85        # doublons accessoires/consommables
```

Réponse `equipment/search` :

```json
{
  "query": "meuleuse",
  "count": 1,
  "results": [
    {"equipment_id":"SIGA-ING-...-EQ1","label":"Meuleuse angulaire Bosch WE 17-125 Quick",
     "brand":"Bosch","model":"WE 17-125 Quick","category":"Meuleuse angulaire",
     "condition":"usé","location":null,"score":0.85}
  ]
}
```

---

## Créer un accessoire

```
POST /api/accessories
POST /api/accessories?force_create=true   # force malgré un doublon détecté
```

Champs (modèle live `AccessoryCreateRequest`) :

| Champ | Obligatoire | Type / défaut |
|---|---|---|
| `label` | **Oui** | string |
| `brand` | Non | string |
| `model` | Non | string |
| `category` | Non | string (libre) |
| `description` | Non | string |
| `stock_qty` | Non | int (défaut 0) |
| `location_hint` | Non | string |
| `notes` | Non | string |

Body :

```json
{"label":"Batterie 18V 5Ah","brand":"Makita","model":"BL1850B","category":"Batterie","stock_qty":3,"location_hint":"Armoire chargeurs A1","notes":"Compatible Makita LXT"}
```

Réponse (création OU doublon retourné) — l'ID est dans `link_id` :

```json
{"ok":true,"link_id":"uuid-acc-...","message":"Accessoire 'Batterie 18V 5Ah' créé (id=uuid-acc-...)."}
```

Déduplication auto sur `label`+`brand`+`model` (insensible à la casse) si non archivé.

---

## Créer un consommable

```
POST /api/consumables
POST /api/consumables?force_create=true
```

Champs (modèle live `ConsumableCreateRequest`) :

| Champ | Obligatoire | Type / défaut |
|---|---|---|
| `label` | **Oui** | string |
| `brand` | Non | string |
| `reference` | Non | string |
| `category` | Non | string (libre) |
| `description` | Non | string |
| `unit` | Non | string (défaut `"pcs"`) |
| `stock_qty` | Non | float (défaut 0) |
| `stock_min_alert` | Non | float (défaut 0) |
| `location_hint` | Non | string |
| `notes` | Non | string |

Body :

```json
{"label":"Foret SDS-Plus Ø10 béton","brand":"Bosch","reference":"2608833800","category":"Foret","unit":"pcs","stock_qty":10,"stock_min_alert":5,"location_hint":"Tiroir forets B3"}
```

Réponse — l'ID est dans `link_id` :

```json
{"ok":true,"link_id":"uuid-con-...","message":"Consommable '...' créé (id=uuid-con-...)."}
```

Déduplication auto sur `label`+`brand`+`reference`.

---

## Lier accessoire ↔ équipement

```
POST /api/links/compatibility
DELETE /api/links/compatibility/{link_id}
```

```json
{"equipment_id":"uuid-eq-...","accessory_id":"uuid-acc-...","note":"Compatible avec adaptateur ADP60F"}
```

`equipment_id` et `accessory_id` obligatoires ; `note` optionnel. Liaison déjà existante →
ignorée silencieusement (`ok:true`). Réponse :

```json
{"ok":true,"link_id":"uuid-link-...","message":"'...' lié à '...' comme accessoire compatible."}
```

---

## Lier consommable ↔ équipement

```
POST /api/links/consumables
DELETE /api/links/consumables/{link_id}
```

```json
{"equipment_id":"uuid-eq-...","consumable_id":"uuid-con-...","qty_per_use":2.0,"note":"Remplacer toutes les 3 utilisations"}
```

`equipment_id` et `consumable_id` obligatoires ; `qty_per_use` (défaut 1) et `note` optionnels.

---

## Équipement (pas de création via API)

Il n'existe **aucun** `POST /api/equipment` (vérifié dans le code live). Les équipements sont
créés par le pipeline n8n. Via l'API, on peut seulement :

```
GET    /api/equipment                       # lister (filtres)
GET    /api/equipment/{id}                   # fiche complète
GET    /api/equipment/{id}/status            # disponibilité
GET    /api/equipment/{id}/family            # écosystème (accessoires + consommables liés)
PATCH  /api/equipment/{id}                    # mettre à jour des champs
POST   /api/equipment/{id}/archive | /unarchive
GET/PUT /api/equipment/{id}/photos           # voir / remplacer la galerie
POST   /api/equipment/{id}/photos/attach     # ajouter une photo sans effacer
```

Pour « ajouter un outil » : soit il existe déjà (issu de n8n) → le compléter via `PATCH` ;
soit prévenir l'utilisateur que la création d'équipement passe par le dépôt Telegram/n8n.

`PATCH /api/equipment/{id}` — corps = champs à modifier, ex. :

```json
{"label":"Perforateur Bosch GBH 2-26","brand":"Bosch","model":"GBH 2-26 DRE","category":"Perforateur","condition":"Bon état","location":"Étagère A3"}
```

---

## Photos

**L'API n'upload pas de binaire.** La photo doit déjà être sur Drive (déposée par n8n ou
manuellement). On travaille avec des `file_id` Drive existants.

Deux mécanismes :

1. **Ajouter une photo** (sans effacer les autres) :

```
POST /api/equipment/{id}/photos/attach
POST /api/accessories/{id}/photos/attach
POST /api/consumables/{id}/photos/attach
```

```json
{"file_id":"1HGG...","role":"overview","folder_id":"1Folder...","filename":"bosch_overview.jpg","mime_type":"image/jpeg","is_primary":true,"attached_by":"cowork"}
```

`file_id` obligatoire. `role` ∈ `overview | nameplate | detail` (défaut `overview`).
409 = déjà liée → traiter comme succès.

2. **Remplacer toute la galerie** (atomique) :

```
PUT /api/equipment/{id}/photos
PUT /api/accessories/{id}/photos
PUT /api/consumables/{id}/photos
```

```json
{"photos":[{"final_drive_file_id":"1AbC...","image_role":"overview","image_index":0},
            {"final_drive_file_id":"2XyZ...","image_role":"nameplate","image_index":1}]}
```

**Règle absolue** : toute modif de photo = déplacer le fichier sur Drive **ET** mettre à jour
la fiche, dans cet ordre. Jamais l'un sans l'autre.

Découverte des photos non rattachées :

```
GET /api/drive/orphan-photos?equipment_id=|accessory_id=|consumable_id=|folder_id=
```

---

## Drive

```
GET   /api/drive/folder/{folder_id}              # lister les fichiers
GET   /api/drive/files/{file_id}                 # métadonnées
POST  /api/drive/folder                          # créer un dossier
POST  /api/drive/files/{file_id}/move            # déplacer
POST  /api/drive/files/{file_id}/copy            # copier
PATCH /api/drive/files/{file_id}/rename          # renommer
```

Créer un dossier :

```json
{"name":"Perforateur_Bosch_GBH","parent_id":"1ParentFolderId..."}
```

Déplacer :

```json
{"new_parent_id":"1FolderId..."}
```

Nécessite un compte de service Drive configuré côté serveur. Indisponible → 503.

---

## Processus complets

### Situation O — Ajouter un accessoire/consommable et le lier (le cas le plus courant)

1. `POST /api/accessories` (ou `/consumables`) → récupérer l'ID dans `link_id`.
2. `GET /api/equipment/search?q=<outil>` → récupérer `equipment_id`.
3. `POST /api/links/compatibility` (ou `/links/consumables`).
4. Confirmer à l'utilisateur.

### Situation M — Lier après l'ajout d'un équipement (suggestions)

Après qu'un équipement apparaît (via n8n) : lister accessoires/consommables en stock, proposer
les liaisons pertinentes (perforateur→forets, 18V Makita→batteries…), puis lier **après
confirmation** seulement.

### Situation Q — Fiche complète avec dossier Drive (accessoire/consommable)

1. Vérifier doublon (`search` + `admin/duplicates`).
2. **Créer le dossier Drive AVANT la fiche** (`POST /api/drive/folder`).
3. Déplacer les photos dans ce dossier (`POST /api/drive/files/{id}/move`) après vérif visuelle.
4. Créer la fiche (`POST /api/accessories` ou `/consumables`).
5. Attacher les photos (`PUT .../photos` ou `.../photos/attach`) avec les `file_id` déplacés.
6. Construire les liaisons.
7. Vérifier la cohérence fiche ↔ dossier Drive.

(Pour un équipement, l'étape 4 « création » n'existe pas via API — l'équipement vient de n8n.)

---

## Catégories suggérées

Champs **libres** (non contraints par l'API), mais valeurs suggérées pour rester cohérent avec
la base existante :

- **equipment.category** : `Machines & Électroportatif`, `Outillage Manuel`,
  `Équipement de Chantier & Levage` (et sous-types observés : `Meuleuse angulaire`,
  `Perforateur`…).
- **accessories.category** : `Batterie`, `Chargeur`, `Coffret / ensemble d'accessoires`,
  `Réservoir / gobelet`, `Lance / buse`, `Flexible air`, `Flexible HP`, `Perche / support`,
  `Pistolet de pulvérisation`, `Accessoire nettoyeur HP`.
- **consumables.category** : `Disque diamant`, `Disque abrasif`, `Disque de coupe`,
  `Lame de scie`, `Lame`, `Foret`, `Abrasif`, `Visserie`, `Filtre`.
- **image_role** (liste fermée) : `overview`, `nameplate`, `detail`.
