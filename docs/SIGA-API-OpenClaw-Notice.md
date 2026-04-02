# Notice d'utilisation de l'API SIGA pour OpenClaw

**Version :** 4.6 — Avril 2026
**Audience :** skill OpenClaw (chat principal + WhatsApp)
**Base URL :** variable d'environnement `SIGA_API_BASE_URL`
**Auth :** header `Authorization: Bearer $SIGA_API_TOKEN`

---

## 0. Modèle mental — Comprendre SIGA avant d'agir

### 0.1 Ce qu'est SIGA

SIGA est un **système d'inventaire d'atelier**. Il gère des équipements (outils), des accessoires (batteries, chargeurs, adaptateurs) et des consommables (forets, lames, abrasifs) appartenant à un atelier physique. Chaque objet physique de l'atelier doit avoir exactement **une fiche** dans SIGA et exactement **un dossier** sur Google Drive contenant ses photos.

Le système est mono-utilisateur (Nicolas) et ses données servent à :
- Savoir ce qui existe dans l'atelier
- Savoir ce qui est sorti / disponible
- Retrouver rapidement un outil et ses accessoires associés
- Afficher une fiche sur l'écran kiosque de l'atelier

### 0.2 Les deux piliers synchronisés : Drive + DuckDB

SIGA repose sur **deux systèmes qui doivent toujours être cohérents** :

```
Google Drive (stockage physique des fichiers)
    └── dossier par fiche
        └── photos de l'objet physique

DuckDB (base de données — source de vérité des métadonnées)
    └── table equipment / accessories / consumables
        └── champs label, brand, model, category…
    └── table equipment_media
        └── final_drive_file_id → pointe vers la photo dans Drive
```

**Règle fondamentale :** toute modification de la base DuckDB qui concerne une photo DOIT être accompagnée du déplacement physique du fichier sur Drive. Une mise à jour de pointeur sans déplacement physique crée une incohérence.

### 0.3 Structure des dossiers Drive

```
My Drive/
  SIGA_TEMP/
    {ingestion_id}/          ← dossier temporaire pendant l'ingestion n8n
  {drive_root}/
    SIGA/
      onboarding/
        {année}/
          {mois}/
            {ingestion_id}/  ← dossier final d'un équipement ingéré par n8n
      accessories/
        {accessory_id}/      ← dossier d'un accessoire (créé par OpenClaw)
      consumables/
        {consumable_id}/     ← dossier d'un consommable (créé par OpenClaw)
```

**Important :** Le `{drive_root}` est la valeur configurée dans l'environnement (`SIGA_DRIVE_ROOT_ID`). Ne jamais créer de dossier à la racine de My Drive sauf `SIGA_TEMP`.

### 0.4 Les trois types d'entités et leur cycle de vie

| Entité | Table DB | Dossier Drive | Photos |
|---|---|---|---|
| Équipement | `equipment` | `onboarding/{année}/{mois}/{ingestion_id}/` | Obligatoires |
| Accessoire | `accessories` | `accessories/{accessory_id}/` | Optionnelles |
| Consommable | `consumables` | `consumables/{consumable_id}/` | Optionnelles |

**Règle :** un équipement est un objet unique (une perceuse particulière). Un accessoire ou consommable peut exister en plusieurs exemplaires (stock_qty). Si un accessoire est "la batterie 18V Makita" en stock = 3, c'est une seule fiche avec `stock_qty: 3`, pas trois fiches.

### 0.5 Ce qu'OpenClaw peut et doit faire

OpenClaw est l'opérateur intelligent de SIGA. Il dispose de tous les outils pour :
- Lire et modifier les fiches (via l'API SIGA)
- Créer et organiser les dossiers Drive (via le bridge `/api/drive/`)
- Déplacer physiquement les photos d'un dossier Drive à un autre
- Lier des entités entre elles (liaisons accessoires/consommables)
- Archiver et nettoyer les données obsolètes

OpenClaw **ne doit jamais** laisser la base DuckDB et Google Drive dans un état incohérent.

> **Architecture v4.6 — Single-writer :** Le serveur FastAPI (`api_server.py`) est le **seul processus autorisé à écrire** dans la base DuckDB. Toutes les opérations de modification (POST, PUT, PATCH, DELETE) passent exclusivement par l'API. Le dashboard Streamlit accède à la base en lecture seule. OpenClaw doit donc toujours utiliser les endpoints API pour modifier les données — **jamais accéder directement à DuckDB**.

---

## RÈGLES ABSOLUES — Lire avant toute opération

### ✅ CE QU'IL FAUT TOUJOURS FAIRE

1. **Créer le dossier Drive AVANT la fiche SIGA.** La fiche doit référencer un dossier Drive réel.

2. **Déplacer physiquement les photos** quand on les réaffecte. Utiliser `/api/drive/files/{file_id}/move` + mettre à jour la fiche via `/api/equipment/{id}/photos` ou `/api/media/reassign`.

3. **Vérifier visuellement chaque photo** avant de l'affecter à une fiche. Une photo de batterie ne doit jamais aller sur une fiche de perceuse. Une photo de Kärcher ne doit jamais aller sur une fiche d'AEG.

4. **Faire un `dry_run=true`** avant tout appel à `/api/admin/migrations/reclassify`. Analyser le plan retourné avant de l'exécuter.

5. **Archiver les anciennes fiches** (pas supprimer brutalement) après migration. Utiliser `POST /api/equipment/{id}/archive`.

6. **Vérifier la cohérence Drive** après chaque opération : le dossier Drive de la fiche cible doit contenir exactement les photos listées dans `equipment_media`.

7. **Confirmer avec l'utilisateur** avant toute opération destructive ou toute réaffectation de photo si le doute existe.

### ❌ CE QU'IL NE FAUT JAMAIS FAIRE

1. **Ne jamais réaffecter un `final_drive_file_id` sans déplacer le fichier** sur Drive. Mettre à jour un pointeur sans déplacer le fichier crée une incohérence permanente.

2. **Ne jamais créer une fiche sans créer son dossier Drive.** Une fiche sans dossier Drive associé est une fiche orpheline qui ne peut pas héberger de photos proprement.

3. **Ne jamais affecter une photo d'un objet A à la fiche d'un objet B.** Même si les photos semblent proches (deux batteries similaires, deux outils de même marque), chaque photo appartient à l'objet physique qu'elle représente.

4. **Ne jamais supprimer physiquement une fiche** (hard delete) sans avoir d'abord migré ou supprimé ses photos sur Drive. Préférer l'archivage (`archived=true`).

5. **Ne jamais lancer une migration en masse sans avoir vérifié chaque fiche individuellement.** Les migrations automatiques sans supervision visuelle sont la source des erreurs d'affectation de photos.

6. **Ne jamais ignorer les fiches fantômes.** Une fiche avec `archived=false` mais sans photos et sans dossier Drive correspondant doit être traitée, pas ignorée.

7. **Ne jamais créer plusieurs fiches pour le même objet physique.** Utiliser `/api/admin/duplicates` pour détecter les doublons avant création.

---

## Vue d'ensemble

L'API SIGA est l'interface entre OpenClaw et la base de données d'inventaire d'atelier.
Elle couvre **sept domaines** :

| Domaine | Ce que tu peux faire |
|---|---|
| **Équipements** | Chercher un outil, vérifier s'il est disponible, consulter son écosystème complet |
| **Mouvements** | Enregistrer les sorties et les retours d'outils |
| **Kits** | Créer, composer, sortir et rentrer des caisses à outils |
| **Kiosque** | Afficher un outil sur l'écran de l'atelier |
| **Réservations** | Réserver un outil sur une plage de dates, vérifier les conflits, annuler |
| **Relationnel v4.0** | Gérer le catalogue accessoires/consommables et leurs liaisons avec les équipements |
| **Migration & gouvernance v4.1** | Listing complet, CRUD photos, bridge Drive, migration atomique, audit trail, export, doublons |
| **Déduplication v4.5** | `POST /api/accessories` et `/consumables` idempotents (évitent les doublons automatiquement) ; `GET /api/admin/duplicates` ; `POST /api/admin/archive-by-label` |
| **Système** | Vérifier que l'API fonctionne |

Toutes les réponses sont en JSON. Les erreurs ont toujours la forme :
```json
{ "ok": false, "error": "code_erreur", "detail": "..." }
```

---

## 1. Équipements

### 1.1 Recherche d'un outil
```
GET /api/equipment/search?q=<texte>
```
**Quand l'utiliser :** L'utilisateur demande « tu as une meuleuse ? », « cherche les perceuses Bosch », « quel équipement j'ai pour la plomberie ? »

**Paramètre :** `q` — texte libre (marque, modèle, nom, catégorie)

**Réponse :**
```json
{
  "query": "meuleuse",
  "count": 3,
  "results": [
    {
      "equipment_id": "uuid-...",
      "label": "Meuleuse d'angle 125mm",
      "brand": "Bosch",
      "model": "GWS 7-125",
      "category": "Outillage électroportatif",
      "condition": "Bon état",
      "location": "Étagère A3",
      "score": 0.92
    }
  ]
}
```

**Points clés :**
- Le champ `score` va de 0 à 1 — plus c'est proche de 1, plus c'est pertinent
- Maximum 20 résultats retournés
- La recherche est insensible à la casse et cherche dans label, brand, model, subtype
- Retenir l'`equipment_id` pour toutes les opérations suivantes

---

### 1.2 Vérifier la disponibilité d'un outil
```
GET /api/equipment/{equipment_id}/status
```
**Quand l'utiliser :** Avant de confirmer une sortie — vérifier qu'un outil n'est pas déjà sorti. L'utilisateur demande « est-ce que la perceuse est disponible ? »

**Réponse si disponible :**
```json
{
  "equipment_id": "uuid-...",
  "label": "Perceuse visseuse 18V",
  "available": true
}
```

**Réponse si sorti :**
```json
{
  "equipment_id": "uuid-...",
  "label": "Perceuse visseuse 18V",
  "available": false,
  "movement_type": "LOAN",
  "borrower_name": "Entreprise Martin",
  "out_date": "2025-03-15 09:30",
  "expected_return_date": "2025-03-22 18:00"
}
```

**Points clés :**
- Toujours faire ce check avant une sortie si le contexte suggère un doute
- `movement_type` : LOAN = prêt, RENTAL = location, MAINTENANCE = entretien

---

## 2. Mouvements (sorties & retours)

### 2.1 Enregistrer une sortie
```
POST /api/movements/checkout
```
**Quand l'utiliser :** L'utilisateur dit « je sors la perceuse pour Martin », « location du compresseur à Entreprise Dupont jusqu'au 30 mars », « mise en maintenance de la scie »

**Corps de la requête :**
```json
{
  "equipment_ids": ["uuid-1", "uuid-2"],
  "borrower_name": "Entreprise Martin",
  "movement_type": "LOAN",
  "borrower_contact": "06 12 34 56 78",
  "expected_return_date": "2025-03-30",
  "notes": "Chantier rue des Lilas"
}
```

| Champ | Obligatoire | Valeurs |
|---|---|---|
| `equipment_ids` | Oui | Liste d'au moins 1 UUID |
| `borrower_name` | Oui | Nom libre |
| `movement_type` | Non (défaut : LOAN) | `LOAN` · `RENTAL` · `MAINTENANCE` |
| `borrower_contact` | Non | Téléphone, email... |
| `expected_return_date` | Non | Format `YYYY-MM-DD` |
| `notes` | Non | Texte libre |

**Réponse :**
```json
{
  "ok": true,
  "batch_id": "uuid-batch-...",
  "movement_ids": ["uuid-mv-1", "uuid-mv-2"],
  "count": 2,
  "message": "2 équipement(s) sorti(s) pour 'Entreprise Martin' (type : LOAN, retour prévu : 2025-03-30)."
}
```

**Points clés :**
- **1 seul outil** → `batch_id` = `movement_id` (identiques), pas vraiment de lot
- **Plusieurs outils** → tous partagent le même `batch_id` → permet le retour groupé
- Toujours garder le `batch_id` pour le retour
- Vérifier la disponibilité avant si nécessaire (endpoint 1.2)

---

### 2.2 Enregistrer un retour
```
POST /api/movements/checkin
```
**Quand l'utiliser :** L'utilisateur dit « Martin a rendu les outils », « retour de la perceuse », « le lot du chantier Dupont est rentré »

**Corps — retour par lot (le plus courant) :**
```json
{
  "batch_id": "uuid-batch-..."
}
```

**Corps — retour ciblé par movement_id :**
```json
{
  "movement_ids": ["uuid-mv-1", "uuid-mv-2"]
}
```

**Corps — combinaison des deux :**
```json
{
  "batch_id": "uuid-batch-...",
  "movement_ids": ["uuid-mv-3"]
}
```

**Réponse :**
```json
{
  "ok": true,
  "returned_count": 2,
  "message": "2 équipement(s) enregistré(s) comme rendu(s)."
}
```

**Points clés :**
- Fournir `batch_id` OU `movement_ids` (ou les deux)
- Le `batch_id` solde tous les outils du lot non encore rentrés
- Si l'utilisateur ne connaît pas le `batch_id`, utiliser d'abord `/api/movements/active` pour le retrouver

---

### 2.3 Voir tous les outils sortis
```
GET /api/movements/active
```
**Quand l'utiliser :** « Qu'est-ce qui est sorti en ce moment ? », « qui a des outils chez lui ? », « y a-t-il des retards ? », l'utilisateur veut un état des lieux avant de prendre une décision

**Réponse :**
```json
{
  "count": 4,
  "items": [
    {
      "movement_id": "uuid-mv-...",
      "equipment_id": "uuid-eq-...",
      "label": "Perceuse visseuse 18V",
      "borrower_name": "Entreprise Martin",
      "movement_type": "LOAN",
      "out_date": "2025-03-15 09:30",
      "expected_return_date": "2025-03-20 18:00",
      "is_late": true,
      "batch_id": "uuid-batch-...",
      "kit_id": null,
      "kit_name": null
    }
  ]
}
```

**Points clés :**
- `is_late: true` → le retour prévu est dépassé — signaler à l'utilisateur
- `kit_id` non nul → l'outil fait partie d'un kit sorti en lot
- Utiliser `batch_id` pour regrouper les outils d'un même lot

---

## 3. Kits (caisses à outils / paniers chantier)

Un **kit** est un ensemble pré-configuré d'équipements destiné à un type de chantier ou d'intervention. On peut le sortir entièrement d'un coup et le rentrer en un seul geste.

### 3.1 Voir tous les kits
```
GET /api/kits
```
**Quand l'utiliser :** « Quels kits tu as ? », « montre-moi les caisses disponibles », l'utilisateur veut préparer un chantier

**Réponse :**
```json
{
  "count": 3,
  "kits": [
    {
      "kit_id": "uuid-kit-...",
      "name": "Caisse Plomberie Urgence",
      "description": "Kit intervention fuite standard",
      "item_count": 8
    }
  ]
}
```

---

### 3.2 Voir le contenu d'un kit
```
GET /api/kits/{kit_id}
```
**Quand l'utiliser :** L'utilisateur veut vérifier ce qu'il y a dans un kit avant de le sortir, ou pour vérifier qu'il est complet

**Réponse :**
```json
{
  "kit_id": "uuid-kit-...",
  "name": "Caisse Plomberie Urgence",
  "description": "Kit intervention fuite standard",
  "item_count": 3,
  "items": [
    {
      "equipment_id": "uuid-eq-...",
      "label": "Coupe-tube 15mm",
      "brand": "Virax",
      "model": "V220215",
      "condition": "Bon état",
      "location": "Caisse rouge étagère B2"
    }
  ]
}
```

---

### 3.3 Créer un kit
```
POST /api/kits
```
**Quand l'utiliser :** L'utilisateur veut préparer une nouvelle caisse à outils, créer un panier pour un type de chantier récurrent

**Corps :**
```json
{
  "name": "Caisse Électricité Appartement",
  "description": "Kit rénovation électrique logement standard",
  "equipment_ids": ["uuid-1", "uuid-2", "uuid-3"]
}
```

| Champ | Obligatoire | Description |
|---|---|---|
| `name` | Oui | Nom du kit |
| `description` | Non | Description libre |
| `equipment_ids` | Non | Peupler immédiatement (peut se faire après) |

**Réponse :**
```json
{
  "ok": true,
  "kit_id": "uuid-kit-...",
  "message": "Kit 'Caisse Électricité Appartement' créé avec 3 équipement(s)."
}
```

**Points clés :**
- Si `equipment_ids` n'est pas fourni, le kit est créé vide et on le peuple ensuite
- Retenir le `kit_id` pour toutes les opérations suivantes

---

### 3.4 Modifier nom / description d'un kit
```
PUT /api/kits/{kit_id}
```
**Quand l'utiliser :** L'utilisateur veut renommer un kit ou modifier sa description

**Corps :**
```json
{
  "name": "Caisse Électricité Type A",
  "description": "Version révisée mars 2025"
}
```
Les deux champs sont optionnels — envoyer uniquement ce qui doit changer.

---

### 3.5 Ajouter des équipements à un kit
```
POST /api/kits/{kit_id}/items
```
**Quand l'utiliser :** L'utilisateur veut compléter un kit existant avec des outils supplémentaires

**Corps :**
```json
{
  "equipment_ids": ["uuid-eq-4", "uuid-eq-5"]
}
```

**Réponse :**
```json
{
  "ok": true,
  "kit_id": "uuid-kit-...",
  "message": "2 équipement(s) ajouté(s) au kit."
}
```

---

### 3.6 Retirer des équipements d'un kit
```
DELETE /api/kits/{kit_id}/items
```
**Quand l'utiliser :** L'utilisateur veut enlever un outil d'un kit sans le supprimer du catalogue

**Corps :**
```json
{
  "equipment_ids": ["uuid-eq-2"]
}
```

---

### 3.7 Redéfinir entièrement le contenu d'un kit
```
PUT /api/kits/{kit_id}/content
```
**Quand l'utiliser :** L'utilisateur veut recomposer complètement un kit — plus efficace que de faire plusieurs add/remove. Remplace atomiquement tout le contenu.

**Corps :**
```json
{
  "equipment_ids": ["uuid-1", "uuid-2", "uuid-3", "uuid-4"]
}
```

Passer une liste vide pour vider le kit sans le supprimer :
```json
{ "equipment_ids": [] }
```

---

### 3.8 Supprimer un kit
```
DELETE /api/kits/{kit_id}
```
**Quand l'utiliser :** L'utilisateur veut supprimer définitivement un kit. **Demander confirmation avant d'appeler cet endpoint.**

**Réponse :**
```json
{
  "ok": true,
  "kit_id": "uuid-kit-...",
  "message": "Kit 'Caisse Plomberie Urgence' supprimé."
}
```

**Points clés :**
- Supprime le kit ET toutes ses lignes `kit_items`
- Les mouvements historiques référençant ce kit sont conservés (traçabilité)
- Action irréversible — toujours confirmer avec l'utilisateur avant

---

### 3.9 Sortir un kit complet
```
POST /api/kits/{kit_id}/checkout
```
**Quand l'utiliser :** Un chantier démarre et on sort toute la caisse d'un coup. L'utilisateur dit « je sors le kit plomberie pour Entreprise Martin »

**Corps :**
```json
{
  "borrower_name": "Entreprise Martin",
  "movement_type": "LOAN",
  "borrower_contact": "06 12 34 56 78",
  "expected_return_date": "2025-04-15",
  "notes": "Chantier avenue Foch - rénovation salle de bain"
}
```

**Réponse :**
```json
{
  "ok": true,
  "batch_id": "uuid-batch-...",
  "movement_ids": ["uuid-mv-1", "uuid-mv-2", "uuid-mv-3"],
  "count": 3,
  "message": "Kit 'Caisse Plomberie Urgence' sorti (3 outil(s)) pour 'Entreprise Martin' — retour prévu le 2025-04-15."
}
```

**Points clés :**
- Tous les outils du kit sortent en un seul appel
- Tous partagent le même `batch_id` → retour groupé possible
- Garder le `batch_id` pour le retour

---

### 3.10 Rentrer un kit
```
POST /api/kits/{kit_id}/checkin
```
**Quand l'utiliser :** Le chantier est terminé et les outils reviennent. Retour total ou partiel.

**Corps — retour total :**
```json
{
  "batch_id": "uuid-batch-..."
}
```

**Corps — retour partiel (certains outils manquants) :**
```json
{
  "batch_id": "uuid-batch-...",
  "returned_equipment_ids": ["uuid-eq-1", "uuid-eq-2"]
}
```

**Réponse :**
```json
{
  "ok": true,
  "returned_count": 2,
  "message": "2 outil(s) du kit enregistré(s) comme rendu(s)."
}
```

**Points clés :**
- Retour partiel : les outils non listés restent « sortis » dans la base
- Utile quand un outil est endommagé ou manquant — signaler à l'utilisateur

---

## 4. Kiosque atelier

Le kiosque (Raspberry Pi 5, écran plein écran en atelier) reçoit les commandes via un fichier JSON partagé écrit par l'API. **Il ne touche plus du tout à DuckDB** pendant l'affichage — ce qui supprime le verrou qui rendait la base inutilisable.

L'écran bascule en **≤ 2 secondes** après chaque appel. Toutes ces commandes sont réservées aux conversations **en atelier** (pas WhatsApp).

---

### 4.1 Afficher un outil
```
POST /api/display/show
```
**Quand l'utiliser :** L'utilisateur veut voir la fiche d'un outil sur le grand écran. « Montre-moi la meuleuse », « affiche la fiche de la perceuse »

**Corps :**
```json
{
  "equipment_id": "uuid-eq-..."
}
```

**Réponse :**
```json
{
  "ok": true,
  "equipment_id": "uuid-eq-...",
  "display_status": "sent",
  "screen": "atelier-main",
  "message": "Meuleuse d'angle 125mm sera visible dans ≤ 2 s."
}
```

**Ce qui s'affiche :** fiche complète identique à la vue dashboard :
- Photos (galerie)
- Titre, marque / modèle, état, catégorie
- Spécifications techniques (grille clé/valeur)
- Infos pratiques : N° série, emplacement, mode d'acquisition, prix d'achat
- Accessoires livrés (business_context_json — rétrocompat) + **Accessoires compatibles (liaisons v4.0, avec stock)**
- Consommables (business_context_json — rétrocompat) + **Consommables à prévoir (liaisons v4.0, avec état stock)**
- Notes
- Statut de disponibilité : « Disponible » ou « En cours d'utilisation (emprunteur, retour prévu) »

> **v4.0** : la réponse du kiosque embarque deux nouveaux champs `accessories_rel` et `consumables_rel` issus de la base relationnelle. Chaque consommable porte le champ `stock_ok: bool` pour afficher instantanément si le stock est suffisant.

---

### 4.2 Afficher un kit
```
POST /api/display/show-kit
```
**Quand l'utiliser :** L'utilisateur veut voir le contenu d'un kit sur l'écran. « Affiche le kit plomberie », « montre ce qu'il y a dans la caisse »

**Corps :**
```json
{
  "kit_id": "uuid-kit-..."
}
```

**Réponse :**
```json
{
  "ok": true,
  "command_type": "SHOW_KIT",
  "display_status": "sent",
  "screen": "atelier-main",
  "message": "Kit 'Caisse Plomberie Urgence' (8 outil(s)) affiché sur l'écran atelier."
}
```

**Ce qui s'affiche :** nom du kit, description, liste de tous les outils (label, marque/modèle, état, emplacement) en grille 2 colonnes.

---

### 4.3 Afficher les sorties en cours
```
POST /api/display/show-movements
```
**Quand l'utiliser :** L'utilisateur veut voir en un coup d'œil ce qui est sorti. « Montre les sorties en cours sur l'écran », « affiche l'état des prêts »

**Pas de corps** (aucun paramètre requis).

**Réponse :**
```json
{
  "ok": true,
  "command_type": "SHOW_MOVEMENTS_ACTIVE",
  "display_status": "sent",
  "screen": "atelier-main",
  "message": "4 sortie(s) en cours affichée(s) (1 en retard)."
}
```

**Ce qui s'affiche :** tableau de toutes les sorties actives — outil, emprunteur, type, date de sortie, retour prévu, badge « EN RETARD » si la date est dépassée.

---

### 4.4 Afficher une confirmation d'action
```
POST /api/display/show-confirmation
```
**Quand l'utiliser :** Après une action importante (sortie enregistrée, retour confirmé, kit créé) pour que l'utilisateur en atelier voie la confirmation sur le grand écran.

**Corps :**
```json
{
  "title":    "Sortie enregistrée",
  "subtitle": "2 outil(s) pour Entreprise Martin",
  "details":  ["Perceuse visseuse 18V", "Meuleuse d'angle 125mm"],
  "batch_id": "uuid-batch-...",
  "color":    "green"
}
```

| Champ | Obligatoire | Valeurs |
|---|---|---|
| `title` | Oui | Titre principal affiché en grand |
| `subtitle` | Non | Sous-titre (emprunteur, quantité…) |
| `details` | Non | Liste d'outils ou de détails |
| `batch_id` | Non | Affiché en bas pour référence |
| `color` | Non (défaut : `green`) | `green` · `red` · `blue` |

**Couleurs :** `green` = succès, `red` = alerte / problème, `blue` = information

---

### 4.5 Repasser en veille
```
POST /api/display/clear
```
**Quand l'utiliser :** Effacer l'écran et revenir au screensaver SIGA. « Efface l'écran », « repasse en veille »

**Pas de corps.**

**Réponse :**
```json
{
  "ok": true,
  "command_type": "CLEAR_SCREEN",
  "display_status": "sent",
  "screen": "atelier-main",
  "message": "Kiosque repassé en mode veille."
}
```

---

## 5. Réservations

Une **réservation** bloque un outil sur une plage de dates future sans le sortir physiquement. Elle permet de planifier l'utilisation et d'éviter les conflits.

Statuts possibles : `PENDING` (réservation future), `ACTIVE` (en cours), `CANCELLED` (annulée).

---

### 5.1 Vérifier les conflits avant de réserver
```
GET /api/reservations/conflicts?equipment_id=<uuid>&start=<date>&end=<date>
```
**Quand l'utiliser :** Avant de créer une réservation — vérifier que la plage est libre. L'utilisateur dit « je veux réserver la perceuse du 5 au 10 avril, est-ce possible ? »

**Paramètres (query string) :**

| Paramètre | Obligatoire | Format |
|---|---|---|
| `equipment_id` | Oui | UUID de l'équipement |
| `start` | Oui | `YYYY-MM-DD` ou `YYYY-MM-DDTHH:MM` |
| `end` | Oui | `YYYY-MM-DD` ou `YYYY-MM-DDTHH:MM` |

**Réponse si aucun conflit :**
```json
{
  "equipment_id": "uuid-eq-...",
  "has_conflict": false,
  "conflicts": []
}
```

**Réponse si conflit détecté :**
```json
{
  "equipment_id": "uuid-eq-...",
  "has_conflict": true,
  "conflicts": [
    {
      "type": "reservation",
      "user_name": "Entreprise Martin",
      "start_date": "2025-04-03 00:00:00",
      "end_date": "2025-04-08 00:00:00",
      "movement_type": null
    }
  ]
}
```

**Types de conflit possibles :**
- `reservation` — chevauchement avec une réservation existante (`PENDING` ou `ACTIVE`)
- `maintenance` — l'équipement est actuellement en maintenance (mouvement `MAINTENANCE` non clôturé)

**Points clés :**
- Appeler cet endpoint avant `POST /api/reservations` si l'utilisateur demande d'abord une vérification
- Si `has_conflict: true`, expliquer le conflit à l'utilisateur avant de proposer une autre date
- `POST /api/reservations` fait aussi cette vérification et renvoie une 409 si conflit — les deux approches sont valides

---

### 5.2 Créer une réservation
```
POST /api/reservations
```
**Quand l'utiliser :** L'utilisateur veut bloquer un outil sur une plage de dates. « Je veux réserver la meuleuse pour la semaine du 14 avril », « bloque la perceuse pour Martin du 20 au 25 »

**Corps :**
```json
{
  "equipment_id": "uuid-eq-...",
  "user_name": "Entreprise Martin",
  "start_date": "2025-04-14",
  "end_date": "2025-04-18"
}
```

| Champ | Obligatoire | Format |
|---|---|---|
| `equipment_id` | Oui | UUID de l'équipement |
| `user_name` | Oui | Nom libre (personne ou entreprise) |
| `start_date` | Oui | `YYYY-MM-DD` ou `YYYY-MM-DDTHH:MM` |
| `end_date` | Oui | `YYYY-MM-DD` ou `YYYY-MM-DDTHH:MM` (doit être > `start_date`) |

**Réponse :**
```json
{
  "ok": true,
  "res_id": "uuid-res-...",
  "message": "C'est noté, 'Meuleuse d'angle 125mm' est bloqué pour Entreprise Martin du 2025-04-14 au 2025-04-18 !"
}
```

**Erreur 409 si conflit :**
```json
{
  "ok": false,
  "error": "conflict",
  "detail": "Impossible : déjà réservé par Entreprise Dupont de 2025-04-12 00:00:00 à 2025-04-16 00:00:00"
}
```

**Points clés :**
- Retenir le `res_id` pour une éventuelle annulation
- La réservation est créée avec le statut `PENDING`
- Un conflit avec une maintenance active bloque aussi la réservation (409)
- Vérifie automatiquement les conflits — pas besoin d'appeler `/conflicts` avant si on veut juste créer directement

---

### 5.3 Lister les réservations à venir
```
GET /api/reservations/active
```
**Quand l'utiliser :** « Quels outils sont réservés ? », « qui a réservé quelque chose cette semaine ? », « est-ce que Martin a des réservations ? »

**Paramètres optionnels (query string) :**

| Paramètre | Description |
|---|---|
| `equipment_id` | Filtrer par équipement |
| `user_name` | Filtrer par nom (insensible à la casse) |

**Réponse :**
```json
{
  "count": 2,
  "reservations": [
    {
      "res_id": "uuid-res-...",
      "equipment_id": "uuid-eq-...",
      "equipment_label": "Meuleuse d'angle 125mm",
      "user_name": "Entreprise Martin",
      "start_date": "2025-04-14 00:00:00",
      "end_date": "2025-04-18 00:00:00",
      "status": "PENDING"
    }
  ]
}
```

**Points clés :**
- Retourne uniquement les réservations non terminées (`PENDING` ou `ACTIVE`) dont la date de fin est dans le futur
- Triées par `start_date` croissante
- Sans paramètre = toutes les réservations à venir, tous équipements confondus

---

### 5.4 Annuler une réservation
```
DELETE /api/reservations/{res_id}
```
**Quand l'utiliser :** L'utilisateur veut annuler une réservation. « Annule la réservation de Martin », « on n'a plus besoin de la perceuse la semaine prochaine »

**Pas de corps** — le `res_id` est dans l'URL.

**Réponse :**
```json
{
  "ok": true,
  "res_id": "uuid-res-...",
  "message": "Réservation annulée avec succès."
}
```

**Erreur 404 si introuvable :**
```json
{
  "ok": false,
  "error": "reservation_not_found"
}
```

**Points clés :**
- Passe le statut à `CANCELLED` (non-destructif — la réservation reste dans la base pour traçabilité)
- Si la réservation est déjà annulée, renvoie `ok: true` sans erreur
- Si le `res_id` est inconnu, utiliser `/api/reservations/active` pour le retrouver

---

## 6. Système

### 6.1 Vérifier que l'API fonctionne
```
GET /api/health
```
**Sans authentification.** Retourne l'état du serveur et de la base de données.

```json
{
  "status": "ok",
  "db": "reachable"
}
```

---

## 7. Accessoires & Consommables (v4.0)

Le modèle relationnel v4.0 introduit deux catalogues indépendants — **accessoires** et **consommables** — liés aux équipements par des tables de jointure Many-to-Many. Un accessoire (ex: batterie 18V) peut être lié à 10 outils différents sans duplication. Un consommable (ex: foret SDS-Plus Ø10) peut être lié à plusieurs machines.

### 7.1 Catalogue des accessoires
```
GET /api/accessories
```
**Quand l'utiliser :** « Quels accessoires tu as en stock ? », « y a-t-il des batteries disponibles ? »

**Paramètre optionnel :** `q` — filtre texte libre (label, marque, modèle)

**Réponse :**
```json
{
  "count": 4,
  "accessories": [
    {
      "accessory_id": "uuid-acc-...",
      "label": "Batterie 18V 5Ah",
      "brand": "Makita",
      "model": "BL1850B",
      "category": "Batterie",
      "stock_qty": 3,
      "location_hint": "Armoire chargeurs A1"
    }
  ]
}
```

---

### 7.2 Créer un accessoire
```
POST /api/accessories
POST /api/accessories?force_create=true
```
**Quand l'utiliser :** L'utilisateur ajoute une nouvelle batterie, un chargeur, un adaptateur… au catalogue.

**Déduplication automatique :** avant d'insérer, l'API vérifie si un accessoire non-archivé avec le même `label` + `brand` + `model` (insensible à la casse) existe déjà. Si oui, retourne l'existant sans créer de doublon. Utilisez `?force_create=true` pour forcer la création d'un nouvel enregistrement même en cas de correspondance.

**Corps :**
```json
{
  "label": "Batterie 18V 5Ah",
  "brand": "Makita",
  "model": "BL1850B",
  "category": "Batterie",
  "stock_qty": 3,
  "location_hint": "Armoire chargeurs A1",
  "notes": "Compatible tous outils Makita LXT"
}
```

| Champ | Obligatoire | Description |
|---|---|---|
| `label` | Oui | Désignation de l'accessoire |
| `brand` | Non | Marque |
| `model` | Non | Référence modèle |
| `category` | Non | Ex: Batterie, Chargeur, Lame, Adaptateur… |
| `stock_qty` | Non (défaut: 0) | Quantité disponible en stock |
| `location_hint` | Non | Emplacement dans l'atelier |
| `notes` | Non | Notes libres |

**Réponse (création) :**
```json
{
  "ok": true,
  "link_id": "uuid-acc-...",
  "message": "Accessoire 'Batterie 18V 5Ah' créé (id=uuid-acc-...)."
}
```

**Réponse (doublon détecté — existant retourné) :**
```json
{
  "ok": true,
  "link_id": "uuid-acc-existant-...",
  "message": "Accessoire existant retourné (doublon évité) : Batterie 18V 5Ah"
}
```

> **Note :** le champ `link_id` contient ici l'`accessory_id` créé ou existant (convention de réponse unifiée).

---

### 7.3 Catalogue des consommables
```
GET /api/consumables
```
**Quand l'utiliser :** « Combien de forets Ø8 il reste ? », « montre les consommables en rupture », « quel papier de verre on a ? »

**Paramètres optionnels :**

| Paramètre | Description |
|---|---|
| `q` | Filtre texte libre (label, marque, référence) |
| `low_stock=true` | Ne retourne que les consommables dont `stock_qty <= stock_min_alert` |

**Réponse :**
```json
{
  "count": 6,
  "consumables": [
    {
      "consumable_id": "uuid-con-...",
      "label": "Foret SDS-Plus Ø10 béton",
      "brand": "Bosch",
      "reference": "2608833800",
      "category": "Foret",
      "unit": "pcs",
      "stock_qty": 2.0,
      "stock_min_alert": 5.0,
      "location_hint": "Tiroir forets B3",
      "stock_ok": false
    }
  ]
}
```

**Points clés :**
- `stock_ok: false` → `stock_qty <= stock_min_alert` → signaler à l'utilisateur
- `unit` : `pcs` (pièces), `ml`, `L`, `g`, `kg`, `m`, `feuilles`…
- Toujours vérifier `stock_ok` avant de valider une préparation chantier

---

### 7.4 Créer un consommable
```
POST /api/consumables
POST /api/consumables?force_create=true
```
**Quand l'utiliser :** L'utilisateur ajoute des forets, des lames de scie, du papier abrasif, de la visserie… au catalogue.

**Déduplication automatique :** avant d'insérer, l'API vérifie si un consommable non-archivé avec le même `label` + `brand` + `reference` (insensible à la casse) existe déjà. Si oui, retourne l'existant sans créer de doublon. Utilisez `?force_create=true` pour forcer la création.

**Corps :**
```json
{
  "label": "Foret SDS-Plus Ø10 béton",
  "brand": "Bosch",
  "reference": "2608833800",
  "category": "Foret",
  "unit": "pcs",
  "stock_qty": 10,
  "stock_min_alert": 5,
  "location_hint": "Tiroir forets B3",
  "notes": "Pour perforateurs SDS-Plus"
}
```

| Champ | Obligatoire | Description |
|---|---|---|
| `label` | Oui | Désignation du consommable |
| `brand` | Non | Marque |
| `reference` | Non | Référence fabricant |
| `category` | Non | Ex: Foret, Abrasif, Lame, Visserie, Filtre… |
| `unit` | Non (défaut: `pcs`) | Unité de mesure |
| `stock_qty` | Non (défaut: 0) | Stock actuel |
| `stock_min_alert` | Non (défaut: 0) | Seuil d'alerte (stock_ok devient false en dessous) |
| `location_hint` | Non | Emplacement dans l'atelier |
| `notes` | Non | Notes libres |

**Réponse (création) :**
```json
{
  "ok": true,
  "link_id": "uuid-con-...",
  "message": "Consommable 'Foret SDS-Plus Ø10 béton' créé (id=uuid-con-...)."
}
```

**Réponse (doublon détecté — existant retourné) :**
```json
{
  "ok": true,
  "link_id": "uuid-con-existant-...",
  "message": "Consommable existant retourné (doublon évité) : Foret SDS-Plus Ø10 béton"
}

---

## 8. Liaisons équipements ↔ accessoires / consommables (v4.0)

Les liaisons sont des relations Many-to-Many entre les équipements et les accessoires/consommables. Elles permettent de savoir instantanément, pour n'importe quel outil, ce qu'il faut pour l'utiliser.

### 8.1 Écosystème complet d'un équipement
```
GET /api/equipment/{equipment_id}/family
```
**Quand l'utiliser :** « Qu'est-ce qu'il me faut pour utiliser ce perforateur ? », « la ponceuse a-t-elle tous ses consommables en stock ? », l'utilisateur affiche une fiche outil et veut voir ce qui est lié.

**Réponse :**
```json
{
  "equipment_id": "uuid-eq-...",
  "label": "Perforateur Bosch GBH 2-26",
  "accessories": [
    {
      "accessory_id": "uuid-acc-...",
      "label": "Batterie 18V 5Ah",
      "brand": "Bosch",
      "model": "GBA 18V",
      "stock_qty": 3,
      "location_hint": "Armoire chargeurs A1",
      "link_id": "uuid-link-...",
      "note": null
    }
  ],
  "consumables": [
    {
      "consumable_id": "uuid-con-...",
      "label": "Foret SDS-Plus Ø10 béton",
      "brand": "Bosch",
      "reference": "2608833800",
      "unit": "pcs",
      "stock_qty": 2.0,
      "stock_min_alert": 5.0,
      "qty_per_use": 1.0,
      "stock_ok": false,
      "location_hint": "Tiroir forets B3",
      "link_id": "uuid-link-...",
      "note": "Forets pour béton uniquement"
    }
  ]
}
```

**Points clés :**
- `stock_ok: false` sur un consommable → signaler immédiatement à l'utilisateur
- `qty_per_use` → quantité typiquement consommée par session de travail
- `link_id` → à conserver pour supprimer une liaison si nécessaire
- Appeler cet endpoint après chaque `search_equipment` dès qu'une opération terrain est prévue

---

### 8.2 Lier un accessoire à un équipement
```
POST /api/links/compatibility
```
**Quand l'utiliser :** L'utilisateur dit « lie la batterie 5Ah au perforateur », « cette batterie 18V est compatible avec la visseuse aussi », ou suite à une suggestion automatique après ingestion.

**Corps :**
```json
{
  "equipment_id": "uuid-eq-...",
  "accessory_id": "uuid-acc-...",
  "note": "Compatible uniquement avec adaptateur ADP60F"
}
```

| Champ | Obligatoire | Description |
|---|---|---|
| `equipment_id` | Oui | UUID de l'équipement |
| `accessory_id` | Oui | UUID de l'accessoire |
| `note` | Non | Note de compatibilité libre |

**Réponse :**
```json
{
  "ok": true,
  "link_id": "uuid-link-...",
  "message": "'Batterie 18V 5Ah' lié à 'Perforateur Bosch GBH 2-26' comme accessoire compatible."
}
```

**Points clés :**
- Si la liaison existe déjà, elle est ignorée silencieusement (`ok: true` sans doublon)
- Conserver le `link_id` pour pouvoir supprimer la liaison

---

### 8.3 Supprimer une liaison accessoire
```
DELETE /api/links/compatibility/{link_id}
```
**Quand l'utiliser :** L'utilisateur veut dissocier un accessoire d'un outil. L'accessoire et l'équipement ne sont pas supprimés.

**Réponse :**
```json
{
  "ok": true,
  "link_id": "uuid-link-...",
  "message": "Liaison supprimée."
}
```

---

### 8.4 Lier un consommable à un équipement
```
POST /api/links/consumables
```
**Quand l'utiliser :** L'utilisateur dit « les forets SDS-Plus sont pour le perforateur », « lie le papier de verre grain 120 à la ponceuse orbitale », ou suite à une suggestion post-ingestion.

**Corps :**
```json
{
  "equipment_id": "uuid-eq-...",
  "consumable_id": "uuid-con-...",
  "qty_per_use": 2.0,
  "note": "Forets béton — remplacer toutes les 3 utilisations"
}
```

| Champ | Obligatoire | Description |
|---|---|---|
| `equipment_id` | Oui | UUID de l'équipement |
| `consumable_id` | Oui | UUID du consommable |
| `qty_per_use` | Non (défaut: 1) | Quantité typiquement utilisée par session |
| `note` | Non | Note sur l'usage |

**Réponse :**
```json
{
  "ok": true,
  "link_id": "uuid-link-...",
  "message": "'Foret SDS-Plus Ø10 béton' lié à 'Perforateur Bosch GBH 2-26' (qty/usage : 2.0)."
}
```

**Points clés :**
- `qty_per_use` permet à la checklist chantier de calculer si le stock total est suffisant pour tous les équipements prévus
- Si la liaison existe déjà, elle est ignorée silencieusement

---

### 8.5 Supprimer une liaison consommable
```
DELETE /api/links/consumables/{link_id}
```
**Quand l'utiliser :** L'utilisateur veut dissocier un consommable d'un outil.

**Réponse :**
```json
{
  "ok": true,
  "link_id": "uuid-link-...",
  "message": "Liaison consommable supprimée."
}
```

---

## Référence complète des endpoints

| Méthode | Endpoint | Auth | Usage |
|---|---|---|---|
| GET | `/api/health` | Non | État du serveur |
| **Équipements** | | | |
| GET | `/api/equipment/search?q=` | Oui | Recherche d'outil |
| GET | `/api/equipment/{id}/status` | Oui | Disponibilité |
| GET | `/api/equipment/{id}/family` | Oui | **v4.0** Écosystème complet (accessoires + consommables) |
| **Mouvements** | | | |
| POST | `/api/movements/checkout` | Oui | Sortie d'outil(s) |
| POST | `/api/movements/checkin` | Oui | Retour d'outil(s) |
| GET | `/api/movements/active` | Oui | Sorties en cours |
| **Kits** | | | |
| GET | `/api/kits` | Oui | Liste des kits |
| GET | `/api/kits/{id}` | Oui | Contenu d'un kit |
| POST | `/api/kits` | Oui | Créer un kit |
| PUT | `/api/kits/{id}` | Oui | Renommer / décrire |
| DELETE | `/api/kits/{id}` | Oui | Supprimer un kit |
| POST | `/api/kits/{id}/items` | Oui | Ajouter des outils |
| DELETE | `/api/kits/{id}/items` | Oui | Retirer des outils |
| PUT | `/api/kits/{id}/content` | Oui | Redéfinir le contenu |
| POST | `/api/kits/{id}/checkout` | Oui | Sortir un kit |
| POST | `/api/kits/{id}/checkin` | Oui | Rentrer un kit |
| **Kiosque** | | | |
| POST | `/api/display/show` | Oui | Afficher fiche outil sur kiosque |
| POST | `/api/display/show-kit` | Oui | Afficher fiche kit sur kiosque |
| POST | `/api/display/show-movements` | Oui | Afficher sorties en cours sur kiosque |
| POST | `/api/display/show-confirmation` | Oui | Afficher écran de confirmation |
| POST | `/api/display/clear` | Oui | Repasser le kiosque en veille |
| **Réservations** | | | |
| GET | `/api/reservations/conflicts?equipment_id=&start=&end=` | Oui | Vérifier conflits avant réservation |
| POST | `/api/reservations` | Oui | Créer une réservation |
| GET | `/api/reservations/active` | Oui | Lister les réservations à venir |
| DELETE | `/api/reservations/{res_id}` | Oui | Annuler une réservation |
| **Relationnel v4.0 — Catalogue** | | | |
| GET | `/api/accessories?q=` | Oui | Catalogue accessoires (filtre optionnel) |
| POST | `/api/accessories` | Oui | Créer un accessoire |
| GET | `/api/consumables?q=&low_stock=` | Oui | Catalogue consommables (filtres optionnels) |
| POST | `/api/consumables` | Oui | Créer un consommable |
| **Relationnel v4.0 — Liaisons** | | | |
| POST | `/api/links/compatibility` | Oui | Lier un accessoire ↔ équipement |
| DELETE | `/api/links/compatibility/{link_id}` | Oui | Supprimer une liaison accessoire |
| POST | `/api/links/consumables` | Oui | Lier un consommable ↔ équipement |
| DELETE | `/api/links/consumables/{link_id}` | Oui | Supprimer une liaison consommable |
| **Photos orphelines v4.3** | | | |
| GET | `/api/drive/orphan-photos?equipment_id=\|folder_id=` | Oui | Fichiers Drive non référencés dans toutes les tables media |
| POST | `/api/equipment/{id}/photos/attach` | Oui | Attacher une photo orpheline à un équipement |
| **Multi-photos accessoires & consommables v4.4** | | | |
| GET | `/api/accessories/{id}/photos` | Oui | Lister les photos d'un accessoire (accessory_media) |
| PUT | `/api/accessories/{id}/photos` | Oui | Remplacer la galerie photos d'un accessoire |
| POST | `/api/accessories/{id}/photos/attach` | Oui | Attacher une photo orpheline à un accessoire (sans effacer) |
| GET | `/api/consumables/{id}/photos` | Oui | Lister les photos d'un consommable (consumable_media) |
| PUT | `/api/consumables/{id}/photos` | Oui | Remplacer la galerie photos d'un consommable |
| POST | `/api/consumables/{id}/photos/attach` | Oui | Attacher une photo orpheline à un consommable (sans effacer) |
| **Suppression & audit v4.6** | | | |
| DELETE | `/api/equipment/{id}` | Oui | Suppression physique définitive d'un équipement (+ ses entrées media) |
| POST | `/api/equipment/{id}/audit` | Oui | Ajouter une entrée dans l'audit trail de l'équipement |
| DELETE | `/api/equipment/{id}/photos/{media_id}` | Oui | Supprimer une photo d'équipement par son media_id |
| DELETE | `/api/accessories/{id}/photos/{media_id}` | Oui | Supprimer une photo d'accessoire par son media_id |
| DELETE | `/api/consumables/{id}/photos/{media_id}` | Oui | Supprimer une photo de consommable par son media_id |

---

## Flux typiques par situation

### Situation A — Un client vient chercher un outil

```
1. GET /api/equipment/search?q=perceuse          → trouver l'outil
2. GET /api/equipment/{id}/status                → vérifier qu'il est disponible
3. POST /api/movements/checkout                  → enregistrer la sortie
   → garder le batch_id / movement_id
```

### Situation B — Un outil est rendu

```
1. GET /api/movements/active                     → retrouver le mouvement si besoin
2. POST /api/movements/checkin  { batch_id }     → enregistrer le retour
```

### Situation C — Préparer un chantier avec un kit existant

```
1. GET /api/kits                                 → choisir le kit
2. GET /api/kits/{id}                            → vérifier la composition
3. POST /api/kits/{id}/checkout                  → sortir le kit entier
   → garder le batch_id
```

### Situation D — Créer un nouveau kit pour un chantier récurrent

```
1. GET /api/equipment/search?q=<outil>           → trouver chaque outil (répéter)
2. POST /api/kits  { name, equipment_ids:[...] } → créer et peupler en une passe
   — ou —
   POST /api/kits  { name }                      → créer vide
   POST /api/kits/{id}/items  { equipment_ids }  → ajouter les outils
3. POST /api/kits/{id}/checkout                  → sortir si chantier immédiat
```

### Situation E — Retour d'un kit (chantier terminé)

```
1. POST /api/kits/{id}/checkin  { batch_id }              → retour total
   — ou —
   POST /api/kits/{id}/checkin  { batch_id, returned_equipment_ids:[...] }  → partiel
```

### Situation F — État des lieux en début de journée

```
1. GET /api/movements/active    → voir tout ce qui est sorti
   → identifier les is_late: true et alerter l'utilisateur
```

### Situation G — Afficher un outil à l'atelier

```
1. GET /api/equipment/search?q=<outil>    → trouver l'equipment_id
2. POST /api/display/show                 → afficher sur l'écran (fiche + disponibilité)
```

### Situation H — Afficher un kit avant de le sortir

```
1. GET /api/kits                          → choisir le kit
2. POST /api/display/show-kit             → montrer le contenu sur l'écran
3. POST /api/kits/{id}/checkout           → valider la sortie
4. POST /api/display/show-confirmation    → confirmer sur l'écran
   { title: "Kit sorti", subtitle: "...", color: "green" }
```

### Situation I — Confirmer une action sur l'écran

```
→ Après tout checkout / checkin important, enchaîner avec :
POST /api/display/show-confirmation  { title, subtitle, details, batch_id, color }
```

### Situation J — État des lieux visuel en atelier

```
1. POST /api/display/show-movements       → afficher le tableau des sorties en cours
   → les retards sont mis en évidence automatiquement (badge rouge)
```

### Situation K — Réserver un outil sur une plage de dates

```
1. GET /api/equipment/search?q=<outil>           → trouver l'equipment_id
2. GET /api/reservations/conflicts               → vérifier la disponibilité sur la plage
   ?equipment_id=<id>&start=<date>&end=<date>
   → si has_conflict: true, proposer une autre date à l'utilisateur
3. POST /api/reservations                        → créer la réservation
   { equipment_id, user_name, start_date, end_date }
   → conserver le res_id pour une éventuelle annulation
```

### Situation L — Annuler une réservation existante

```
1. GET /api/reservations/active                  → retrouver la réservation
   ?user_name=<nom>  — ou —  ?equipment_id=<id>
   → noter le res_id
2. DELETE /api/reservations/{res_id}             → annuler
```

---

### Situation M — Ingestion intelligente : lier après ajout d'un outil *(v4.0)*

À déclencher **immédiatement** après l'ajout d'un nouvel équipement via n8n.

```
1. [n8n] Nouvel équipement ajouté → equipment_id disponible
2. GET /api/accessories                          → lister les accessoires en stock
   GET /api/consumables                          → lister les consommables en stock
3. [OpenClaw] Analyse label/subtype/category de l'équipement
   → proposer à l'utilisateur : "J'ai ajouté ce perforateur.
     Il y a des forets SDS-Plus en stock. Je les lie ?"
4. Si l'utilisateur dit oui :
   POST /api/links/consumables                   → lier chaque consommable pertinent
   POST /api/links/compatibility                 → lier chaque accessoire pertinent
```

**Règles de suggestion :**
- Perforateur / Perceuse → proposer les forets correspondants (SDS-Plus, SDS-Max, standard…)
- Outil 18V Makita → proposer les batteries et chargeurs Makita 18V
- Ponceuse orbitale → proposer les papiers abrasifs et disques
- Scie circulaire → proposer les lames de scie
- Meuleuse → proposer les disques (coupe, meulage, lamelles)
- Ne jamais lier automatiquement sans confirmation explicite

---

### Situation N — Préparation chantier : checklist accessoires & consommables *(v4.0)*

```
1. GET /api/equipment/search?q=<outil>            → trouver chaque outil prévu (répéter)
   → collecter les equipment_ids
2. GET /api/equipment/{id}/family                 → pour chaque outil :
   → vérifier les accessoires (stock_qty > 0 ?)
   → vérifier les consommables (stock_ok: true ?)
3. Annoncer les alertes à l'utilisateur :
   - Accessoires en rupture : "La batterie 5Ah n'est pas en stock"
   - Consommables insuffisants : "Il reste 2 forets Ø10, seuil min = 5"
4. Optionnel : POST /api/kits                     → créer un kit si le chantier est récurrent
```

**Message type OpenClaw :**
> "Kit SDB prêt à 80%. Attention : la batterie 5Ah est en stock mais en dessous du seuil. Il te manque du papier de verre grain 120 (rupture de stock). Veux-tu que je note une commande ?"

---

### Situation O — Ajouter un accessoire/consommable et le lier *(v4.0)*

```
1. POST /api/accessories  { label, brand, stock_qty }   → créer l'accessoire
   — ou —
   POST /api/consumables  { label, brand, stock_qty, stock_min_alert }
   → noter l'accessory_id / consumable_id retourné dans link_id

2. GET /api/equipment/search?q=<outil>            → trouver l'équipement cible
   → noter l'equipment_id

3. POST /api/links/compatibility  { equipment_id, accessory_id }
   — ou —
   POST /api/links/consumables  { equipment_id, consumable_id, qty_per_use }

4. Confirmer : "Batterie 18V 5Ah ajoutée et liée au perforateur et à la visseuse."
```

---

### Situation P — Vérifier les stocks en alerte *(v4.0)*

```
1. GET /api/consumables?low_stock=true           → tous les consommables en dessous du seuil
   → lister à l'utilisateur avec location_hint pour qu'il sache où regarder
```

**Message type OpenClaw :**
> "3 consommables en alerte stock :
> ⚠ Foret SDS-Plus Ø10 — 2 pcs (seuil : 5) — Tiroir forets B3
> ⚠ Papier abrasif grain 120 — 0 feuilles — Étagère consommables C2
> ⚠ Lame scie circulaire 165mm — 1 pcs (seuil : 2) — Caisse lames"

---

### Situation Q — Créer une nouvelle fiche complète avec dossier Drive *(processus complet)*

Ce processus s'applique quand l'utilisateur veut créer manuellement une nouvelle fiche (sans passer par n8n). Chaque étape est obligatoire et doit être exécutée dans l'ordre.

```
ÉTAPE 1 — Vérifier qu'il n'existe pas déjà une fiche similaire
  GET /api/equipment/search?q=<label>
  GET /api/admin/duplicates?threshold=0.85
  → Si doublon trouvé : proposer de mettre à jour la fiche existante plutôt que d'en créer une nouvelle

ÉTAPE 2 — Créer le dossier Drive AVANT la fiche
  POST /api/drive/folder  { "name": "<label_normalisé>", "parent_id": "<dossier_parent_drive_id>" }
  → Structure attendue selon le type :
    Équipement  : SIGA/onboarding/{année}/{mois}/{nouvel_id}/
    Accessoire  : SIGA/accessories/{accessory_id}/   (utiliser l'ID provisoire ou créer le dossier après)
    Consommable : SIGA/consumables/{consumable_id}/
  → Noter le folder_id retourné — il sera passé à la fiche

ÉTAPE 3 — Déplacer physiquement les photos dans le dossier Drive créé
  Pour chaque photo à affecter à cette fiche :
    a. Identifier la photo sur Drive (via GET /api/drive/folder/{dossier_source})
    b. VÉRIFIER VISUELLEMENT que la photo correspond bien à l'objet (pas une photo d'un autre outil)
    c. POST /api/drive/files/{file_id}/move  { "new_parent_id": "<folder_id_étape_2>" }
    d. Optionnel : PATCH /api/drive/files/{file_id}/rename  { "new_name": "<label>_overview.jpg" }

ÉTAPE 4 — Créer la fiche SIGA avec les références Drive
  POST /api/accessories  { label, brand, model, stock_qty, location_hint, notes }
  — ou —
  POST /api/consumables  { label, brand, reference, stock_qty, stock_min_alert, ... }
  → Pour les équipements, ils sont créés par n8n (ingestion) — OpenClaw les met à jour via PATCH

ÉTAPE 5 — Adresser les photos dans la fiche SIGA
  PUT /api/equipment/{id}/photos  { "photos": [{ "final_drive_file_id": "...", "image_role": "overview", "image_index": 0 }] }
  → Utiliser les file_id des fichiers déplacés à l'étape 3
  → Vérifier que chaque file_id pointe bien vers un fichier dans le dossier Drive de la fiche

ÉTAPE 6 — Construire les liaisons
  POST /api/links/compatibility  { equipment_id, accessory_id }   → pour chaque accessoire compatible
  POST /api/links/consumables  { equipment_id, consumable_id }    → pour chaque consommable lié

ÉTAPE 7 — Vérification finale
  GET /api/equipment/{id}         → vérifier que la fiche est complète
  GET /api/drive/folder/{folder_id}  → vérifier que le dossier Drive contient les photos attendues
  → Les deux doivent être cohérents
```

---

### Situation R — Scission d'une fiche existante (split_record) *(processus complet)*

Ce processus s'applique quand une fiche "équipement" contient en réalité plusieurs objets (ex: un kit perceuse + batterie + chargeur ingéré ensemble). Il faut scinder la fiche en plusieurs entités distinctes.

**IMPÉRATIF : analyser chaque photo individuellement avant de commencer.**

```
ÉTAPE 0 — Audit de la fiche source
  GET /api/equipment/{source_id}
  → Lister toutes les photos (champ "photos")
  GET /api/drive/folder/{drive_folder_id_source}
  → Vérifier que les photos listées dans la base correspondent aux fichiers Drive
  → Identifier à quel objet physique chaque photo appartient (regarder le contenu de chaque photo)

ÉTAPE 1 — Simulation (dry_run)
  POST /api/admin/migrations/reclassify?dry_run=true
  {
    "source_equipment_id": "uuid-source",
    "action": "split_record",
    "target_equipment": { "label": "...", "migration_status": "REVIEWED" },
    "new_accessories": [ { "label": "Batterie 18V", "brand": "...", "stock_qty": 1 } ],
    "new_consumables": [],
    "photo_mapping": [],   ← NE PAS remplir les photo_mapping ici, gérer les photos manuellement après
    "source_record_policy": "archive",
    "operator": "openclaw",
    "notes": "Scission fiche..."
  }
  → Lire le plan retourné : combien d'entités créées, combien de liens

ÉTAPE 2 — Présenter le plan à l'utilisateur et attendre confirmation
  "Je vais scinder cette fiche en :
  - 1 équipement : Perforateur Bosch GBH 2-26
  - 1 accessoire : Batterie 18V 4Ah (stock: 1)
  La fiche source sera archivée.
  Les photos devront être réaffectées manuellement.
  Confirmes-tu ?"

ÉTAPE 3 — Créer les dossiers Drive pour chaque nouvelle entité
  Pour chaque entité à créer (accessoires, consommables) :
    POST /api/drive/folder  { "name": "<label>", "parent_id": "<dossier_accessories|consumables>" }
    → noter le folder_id

ÉTAPE 4 — Exécuter la migration (sans photo_mapping)
  POST /api/admin/migrations/reclassify?dry_run=false
  { ...même body que dry_run... }
  → noter les IDs créés : created_accessory_ids, created_consumable_ids

ÉTAPE 5 — Réaffecter les photos manuellement (une par une)
  Pour chaque photo de la fiche source :
    a. Identifier à quelle entité elle appartient (vérification visuelle)
    b. POST /api/drive/files/{file_id}/move  { "new_parent_id": "<folder_id_entité_cible>" }
    c. Mettre à jour la fiche cible :
       PUT /api/equipment/{id}/photos  { "photos": [...] }
       — ou utiliser PATCH /api/accessories/{id}  avec la photo
  → Chaque photo ne peut appartenir qu'à UNE SEULE entité après la migration

ÉTAPE 6 — Vider le dossier Drive source (si toutes les photos ont été déplacées)
  → Vérifier que le dossier source Drive est vide
  → Ne pas supprimer le dossier source (le laisser vide pour traçabilité)

ÉTAPE 7 — Construire les liaisons
  POST /api/links/compatibility  { equipment_id: uuid-new-equip, accessory_id: uuid-new-acc }

ÉTAPE 8 — Marquer la fiche source comme archivée (si pas fait automatiquement)
  POST /api/equipment/{source_id}/archive

ÉTAPE 9 — Vérification de cohérence
  GET /api/equipment/{new_id}        → fiche équipement avec photos ✓
  GET /api/accessories/{acc_id}      → fiche accessoire avec photos ✓
  GET /api/drive/folder/{folder_source}  → vide ✓
  GET /api/drive/folder/{folder_equip}   → contient les photos de l'équipement ✓
  GET /api/drive/folder/{folder_acc}     → contient les photos de l'accessoire ✓
```

---

### Situation S — Nettoyage des fiches fantômes *(processus complet)*

Une fiche fantôme est une fiche `archived=false` qui correspond à une entité supprimée, remplacée ou mal ingérée. Elle pollue le catalogue et les recherches.

```
ÉTAPE 1 — Identifier les fiches fantômes
  GET /api/equipment?migration_status=NOT_REVIEWED&page_size=100
  → Lister toutes les fiches non revues
  GET /api/admin/duplicates?threshold=0.80
  → Identifier les doublons potentiels

ÉTAPE 2 — Pour chaque fiche suspecte
  GET /api/equipment/{id}
  → Vérifier : a-t-elle des photos ? Des liaisons ? Des mouvements actifs ?
  GET /api/drive/folder/{drive_folder_id}
  → Vérifier : le dossier Drive existe-t-il ? Contient-il des fichiers ?

ÉTAPE 3 — Prendre la décision pour chaque fiche
  CAS A — Fiche en doublon d'une autre fiche valide
    → Transférer les photos vers la fiche canonique (Étape 4)
    → Archiver la fiche fantôme : POST /api/equipment/{id}/archive

  CAS B — Fiche sans équivalent dans l'atelier réel
    → Archiver la fiche : POST /api/equipment/{id}/archive
    → Si le dossier Drive est vide : le laisser (ne pas supprimer)
    → Si le dossier Drive contient des photos : vérifier si elles appartiennent à une autre fiche

  CAS C — Fiche avec des données valides mais mal classée
    → PATCH /api/equipment/{id}  { "label": ..., "category": ..., "migration_status": "REVIEWED" }
    → Garder la fiche, corriger les données

ÉTAPE 4 — Transfert de photos d'une fiche fantôme vers une fiche canonique
  a. GET /api/equipment/{fantome_id}  → lister les photos
  b. Pour chaque photo :
     - Vérifier visuellement qu'elle correspond bien à l'entité canonique
     - POST /api/drive/files/{file_id}/move  { "new_parent_id": "<folder_canonique>" }
     - Ajouter la photo à la fiche canonique :
       PUT /api/equipment/{canonique_id}/photos  { "photos": [...photos existantes + nouvelle...] }
  c. Supprimer la photo de la fiche fantôme :
     PUT /api/equipment/{fantome_id}/photos  { "photos": [] }

ÉTAPE 5 — Marquer le statut de migration
  PATCH /api/equipment/{id}  { "migration_status": "ARCHIVED", "migrated_by": "openclaw" }
  POST /api/equipment/{id}/archive

ÉTAPE 6 — Rapport final à l'utilisateur
  "Nettoyage terminé :
  - X fiches archivées
  - Y photos déplacées
  - Z doublons résolus
  Fiches actives restantes : N"
```

---

### Situation T — Audit de cohérence Drive ↔ DuckDB

À exécuter avant toute opération de migration en masse pour connaître l'état réel du système.

```
ÉTAPE 1 — Export complet de la base
  GET /api/admin/export?include_archived=false
  → Récupérer la liste de tous les équipements actifs avec leurs photos

ÉTAPE 2 — Pour chaque équipement avec photos
  GET /api/drive/folder/{drive_folder_id}  → lister les fichiers Drive
  → Comparer :
    - Photos dans la base (equipment_media) : liste des final_drive_file_id
    - Fichiers dans le dossier Drive : liste des file_id présents
  → Signaler les incohérences :
    - Photo dans la base mais absente du Drive → lien mort
    - Fichier dans Drive mais absent de la base → photo orpheline non référencée

ÉTAPE 3 — Identifier les dossiers Drive sans fiche associée
  → Parcourir les dossiers SIGA/onboarding/{année}/{mois}/
  → Pour chaque dossier, vérifier si un équipement avec ce ingestion_id existe dans la base

ÉTAPE 4 — Rapport d'audit
  "Audit Drive ↔ DuckDB :
  - X fiches cohérentes
  - Y liens morts (photo référencée mais absente Drive)
  - Z photos orphelines (présentes Drive mais non référencées)
  - W dossiers Drive sans fiche associée"
```

---

### Situation U — Corriger une affectation de photo erronée

Ce processus s'applique quand une photo est affectée à la mauvaise fiche (ex: photo d'une batterie dans la fiche perceuse).

```
ÉTAPE 1 — Identifier la photo mal affectée
  GET /api/equipment/{id_fiche_incorrecte}
  → Identifier le media_id et final_drive_file_id de la photo erronée

ÉTAPE 2 — Identifier la fiche correcte
  GET /api/equipment/search?q=<label_correct>
  → Trouver l'equipment_id / accessory_id de la fiche qui devrait avoir cette photo

ÉTAPE 3 — Vérifier que la fiche correcte a un dossier Drive
  GET /api/equipment/{id_fiche_correcte}
  → Récupérer le drive_folder_id de la fiche correcte
  → Si pas de dossier : créer d'abord le dossier (Situation Q, Étape 2)

ÉTAPE 4 — Déplacer physiquement la photo sur Drive
  POST /api/drive/files/{file_id_photo}/move  { "new_parent_id": "<folder_id_fiche_correcte>" }

ÉTAPE 5 — Retirer la photo de la fiche incorrecte
  GET /api/equipment/{id_fiche_incorrecte}
  → Photos actuelles : [photo_A, photo_erronée, photo_C]
  PUT /api/equipment/{id_fiche_incorrecte}/photos  { "photos": [photo_A, photo_C] }
  → Supprimer la photo_erronée de la liste

ÉTAPE 6 — Ajouter la photo à la fiche correcte
  GET /api/equipment/{id_fiche_correcte}
  → Photos actuelles : [photo_X, photo_Y]
  PUT /api/equipment/{id_fiche_correcte}/photos  { "photos": [photo_X, photo_Y, photo_déplacée] }

ÉTAPE 7 — Vérification
  GET /api/equipment/{id_fiche_incorrecte}  → photo absente ✓
  GET /api/equipment/{id_fiche_correcte}   → photo présente ✓
  GET /api/drive/folder/{folder_correcte}  → fichier présent dans le bon dossier ✓
```

---

### Situation V — Reclasser un équipement comme accessoire ou consommable

Ce processus s'applique quand une fiche ingérée comme "équipement" doit être reclassée (ex: une batterie ingérée comme équipement alors qu'elle est un accessoire).

```
ÉTAPE 1 — Analyser la fiche source
  GET /api/equipment/{source_id}
  → Vérifier les photos, le label, la catégorie actuelle
  → Vérifier si d'autres équipements pourraient être liés à cet accessoire/consommable

ÉTAPE 2 — Vérifier si l'accessoire/consommable n'existe pas déjà
  GET /api/accessories?q=<label>   — ou —   GET /api/consumables?q=<label>
  GET /api/admin/duplicates?threshold=0.80
  → Si doublon : ne pas créer un nouveau, mettre à jour le stock de l'existant

ÉTAPE 3 — Dry run
  POST /api/admin/migrations/reclassify?dry_run=true
  {
    "source_equipment_id": "uuid-source",
    "action": "reclassify_as_accessory",   ← ou "reclassify_as_consumable"
    "new_accessories": [ { "label": "...", "brand": "...", "stock_qty": 1 } ],
    "source_record_policy": "archive",
    "operator": "openclaw"
  }

ÉTAPE 4 — Créer le dossier Drive pour le nouvel accessoire/consommable
  POST /api/drive/folder  { "name": "<label>", "parent_id": "<dossier_accessories>" }

ÉTAPE 5 — Exécuter la migration
  POST /api/admin/migrations/reclassify?dry_run=false  { ...même body... }

ÉTAPE 6 — Déplacer les photos (obligatoire)
  Pour chaque photo de la fiche source :
    POST /api/drive/files/{file_id}/move  { "new_parent_id": "<folder_id_nouvel_accessoire>" }
  PUT /api/accessories/{new_acc_id}/photos  { "photos": [...] }
  → Ou utiliser PATCH /api/accessories/{new_acc_id} selon l'endpoint disponible

ÉTAPE 7 — Créer les liaisons vers les équipements qui utilisent cet accessoire
  Pour chaque équipement compatible :
    POST /api/links/compatibility  { equipment_id, accessory_id: new_acc_id }

ÉTAPE 8 — Vérification
  GET /api/accessories/{new_acc_id}   → fiche complète avec photos ✓
  GET /api/equipment/{source_id}      → archived=true ✓
```

---

### Situation W — Vérification avant toute opération en masse

**À exécuter SYSTÉMATIQUEMENT avant de lancer plusieurs migrations d'affilée.**

```
1. GET /api/admin/export?include_archived=false
   → Compter les entités actives (équipements, accessoires, consommables)
   → Mémoriser ce chiffre de référence

2. GET /api/admin/duplicates?threshold=0.80
   → Signaler les doublons à l'utilisateur avant de commencer

3. Pour un échantillon de 5 fiches :
   GET /api/equipment/{id}
   GET /api/drive/folder/{folder_id}
   → Vérifier que Drive et DuckDB sont cohérents

4. Annoncer à l'utilisateur :
   "Avant de commencer la migration :
   - X équipements actifs, Y accessoires, Z consommables
   - N doublons potentiels détectés : [liste]
   - Cohérence Drive/DB vérifiée sur 5 fiches : OK/PROBLÈMES DÉTECTÉS
   Procéder ?"
```

---

## Types de mouvement

| Code | Signification | Usage typique |
|---|---|---|
| `LOAN` | Prêt gratuit | Outil prêté à un client ou collaborateur |
| `RENTAL` | Location payante | Outil loué avec contrat |
| `MAINTENANCE` | Entretien / réparation | Outil envoyé en SAV ou réparation interne |

---

## Format des dates

Toujours utiliser le format **`YYYY-MM-DD`** pour `expected_return_date`.
Exemples valides : `2025-04-30` · `2025-12-01`
Format étendu accepté : `2025-04-30T14:00`

---

## Codes d'erreur courants

| Code HTTP | `error` | Signification |
|---|---|---|
| 400 | — | Paramètre manquant ou invalide (voir `detail`) |
| 401 | — | Token absent ou incorrect |
| 404 | `equipment_not_found` | L'`equipment_id` n'existe pas dans la base |
| 404 | `kit_not_found` | Le `kit_id` n'existe pas |
| 404 | `reservation_not_found` | Le `res_id` n'existe pas |
| 404 | `link_not_found` | Le `link_id` (liaison) n'existe pas |
| 404 | *(message inline)* | L'`accessory_id` ou `consumable_id` n'existe pas |
| 409 | `conflict` | La plage demandée est déjà réservée ou l'outil est en maintenance |
| 503 | `screen_unavailable` | L'écran kiosque ou la base est inaccessible |

En cas d'erreur 503, la base DuckDB est temporairement verrouillée par n8n. Réessayer dans quelques secondes.

---

## Modèle de données v4.0 — Vue relationnelle

```
equipment (catalogue outils)
    │
    ├── links_compatibility ──→ accessories (batteries, adaptateurs, chargeurs…)
    │       equipment_id FK         accessory_id PK
    │       accessory_id FK         label, brand, model
    │       note                    stock_qty
    │       [UNIQUE equipment+accessory]
    │
    └── links_consumables   ──→ consumables (forets, abrasifs, visserie…)
            equipment_id FK         consumable_id PK
            consumable_id FK        label, brand, reference
            qty_per_use             unit, stock_qty, stock_min_alert
            note                    → stock_ok = stock_qty > stock_min_alert
            [UNIQUE equipment+consumable]
```

**Règle de mutualisation :** une batterie 18V peut être liée à 10 outils. Elle n'existe qu'une fois dans `accessories`. Les 10 liaisons sont dans `links_compatibility`.

**Règle Many-to-Many :** un perforateur peut avoir des forets Ø8, Ø10, Ø12 liés. Chaque foret peut aussi être lié à une autre perceuse. La relation est bidirectionnelle.

---

## Champs `ai_metadata` dans equipment *(v4.0)*

Chaque fiche équipement dispose d'un champ `ai_metadata` (JSON) permettant de stocker les capacités sémantiques de l'outil pour les suggestions automatiques :

```json
{
  "domaines": ["maçonnerie", "béton", "perçage bois"],
  "technologie_batterie": "SDS-Plus 18V",
  "force_impact": "2.9 J",
  "compatible_marque": "Bosch"
}
```

Ce champ est libre — OpenClaw peut le lire pour affiner ses suggestions de liaisons lors de l'ingestion.

---

## 9. Migration & gouvernance *(v4.1)*

### 9.1 Listing équipements avec filtres

```
GET /api/equipment
```

**Paramètres query :**

| Paramètre | Type | Description |
|---|---|---|
| `q` | string | Recherche texte libre (label, brand, model) |
| `category` | string | Filtrer par catégorie (exact, insensible à la casse) |
| `brand` | string | Filtrer par marque |
| `status` | string | Filtrer par statut (disponible / sorti / maintenance…) |
| `archived` | bool | `true` pour voir les archivés. Par défaut : exclus |
| `migration_status` | string | `NOT_REVIEWED` / `REVIEWED` / `MIGRATED` / `ARCHIVED` |
| `page` | int | Page (défaut : 1) |
| `page_size` | int | Taille de page (défaut : 50) |

**Réponse :**
```json
{
  "total": 142,
  "page": 1,
  "page_size": 50,
  "items": [
    {
      "equipment_id": "uuid-...",
      "label": "Perforateur SDS-Plus",
      "brand": "Bosch",
      "model": "GBH 2-26 DRE",
      "category": "Outillage électroportatif",
      "status": "disponible",
      "archived": false,
      "migration_status": "NOT_REVIEWED"
    }
  ]
}
```

### 9.2 Fiche complète équipement

```
GET /api/equipment/{equipment_id}
```

Retourne tous les champs y compris photos (`equipment_media`), gouvernance, ai_metadata.

**Réponse :**
```json
{
  "equipment_id": "uuid-...",
  "label": "Perforateur SDS-Plus",
  "brand": "Bosch",
  "archived": false,
  "migration_status": "NOT_REVIEWED",
  "legacy_source_id": null,
  "migrated_at": null,
  "migrated_by": null,
  "classification_confidence": null,
  "photos": [
    {
      "media_id": "uuid-photo-...",
      "final_drive_file_id": "1AbCdEfGh...",
      "image_role": "overview",
      "image_index": 0
    }
  ],
  "ai_metadata": { "domaines": ["béton", "maçonnerie"] }
}
```

### 9.3 Mettre à jour un équipement (PATCH)

```
PATCH /api/equipment/{equipment_id}
```

Seuls les champs fournis sont modifiés (sémantique PATCH).

**Body :**
```json
{
  "label": "Nouveau libellé",
  "brand": "Bosch",
  "migration_status": "REVIEWED",
  "ai_metadata": { "domaines": ["perçage", "vissage"] }
}
```

### 9.4 Archiver / désarchiver un équipement

```
POST /api/equipment/{equipment_id}/archive
POST /api/equipment/{equipment_id}/unarchive
```

Soft-delete : met `archived=true/false` et `migration_status=ARCHIVED/REVIEWED`. Aucune donnée n'est supprimée.

### 9.4bis Suppression définitive d'un équipement *(v4.6)*

```
DELETE /api/equipment/{equipment_id}
```

**Suppression physique irréversible** : supprime toutes les entrées `equipment_media` puis la fiche équipement dans une seule transaction atomique.

> ⚠️ **Opération destructive.** Toujours archiver d'abord (`POST /api/equipment/{id}/archive`) sauf si la fiche est clairement erronée (doublon, test). Confirmer avec l'utilisateur avant d'exécuter.

**Corps de la requête :** aucun (DELETE sans body)

**Réponse 200 :**
```json
{ "deleted": true, "equipment_id": "uuid-eq-..." }
```

**Ce que cet endpoint ne fait PAS :**
- Il ne supprime pas les fichiers physiques sur Google Drive — c'est à OpenClaw de le faire manuellement si nécessaire
- Il ne supprime pas les liaisons accessoires/consommables — les désarchiver avant si besoin

**Séquence recommandée avant suppression définitive :**
```
1. Vérifier que l'équipement est bien archivé :
   GET /api/admin/migrations?status=ARCHIVED&id={id}

2. Vérifier les photos liées :
   GET /api/equipment/{id}/photos  → noter les final_drive_file_id

3. (Optionnel) Supprimer les fichiers Drive correspondants

4. Supprimer la fiche :
   DELETE /api/equipment/{id}
```

### 9.5 CRUD Accessoires *(v4.1)*

```
GET    /api/accessories/{accessory_id}    → fiche complète
PATCH  /api/accessories/{accessory_id}    → mise à jour partielle
DELETE /api/accessories/{accessory_id}    → soft-delete (archived=true)
DELETE /api/accessories/{accessory_id}?hard=true  → suppression physique
```

### 9.6 CRUD Consommables *(v4.1)*

```
GET    /api/consumables/{consumable_id}   → fiche complète
PATCH  /api/consumables/{consumable_id}   → mise à jour partielle
DELETE /api/consumables/{consumable_id}   → soft-delete
DELETE /api/consumables/{consumable_id}?hard=true  → suppression physique
```

### 9.7 Gestion des photos *(v4.4 — multi-photos pour toutes les entités)*

> **PRINCIPE FONDAMENTAL :** Toute modification de photo implique DEUX opérations indissociables :
> 1. Déplacer le fichier physiquement sur Drive (`POST /api/drive/files/{id}/move`)
> 2. Mettre à jour la référence dans la base SIGA (`PUT /api/{entity}/{id}/photos`)
> Ces deux opérations doivent toujours être effectuées ensemble, dans cet ordre.
> **Ne jamais faire l'une sans l'autre.**

> **Depuis v4.4 :** Les **accessoires** et **consommables** supportent plusieurs photos, stockées dans les tables `accessory_media` et `consumable_media`. Les endpoints photo sont symétriques pour les 3 types d'entités.

#### Endpoints photo par type d'entité

| Endpoint | Entité | Table | Description |
|---|---|---|---|
| `GET /api/equipment/{id}/photos` | Équipement | `equipment_media` | Lister les photos |
| `PUT /api/equipment/{id}/photos` | Équipement | `equipment_media` | Remplacer toute la galerie |
| `POST /api/equipment/{id}/photos/attach` | Équipement | `equipment_media` | Ajouter une photo orpheline |
| `DELETE /api/equipment/{id}/photos/{media_id}` | Équipement | `equipment_media` | **v4.6** Supprimer une photo par son media_id |
| `GET /api/accessories/{id}/photos` | Accessoire | `accessory_media` | Lister les photos |
| `PUT /api/accessories/{id}/photos` | Accessoire | `accessory_media` | Remplacer toute la galerie |
| `POST /api/accessories/{id}/photos/attach` | Accessoire | `accessory_media` | Ajouter une photo orpheline |
| `DELETE /api/accessories/{id}/photos/{media_id}` | Accessoire | `accessory_media` | **v4.6** Supprimer une photo par son media_id |
| `GET /api/consumables/{id}/photos` | Consommable | `consumable_media` | Lister les photos |
| `PUT /api/consumables/{id}/photos` | Consommable | `consumable_media` | Remplacer toute la galerie |
| `POST /api/consumables/{id}/photos/attach` | Consommable | `consumable_media` | Ajouter une photo orpheline |
| `DELETE /api/consumables/{id}/photos/{media_id}` | Consommable | `consumable_media` | **v4.6** Supprimer une photo par son media_id |

#### GET /api/{entity}/{id}/photos

Liste toutes les photos d'une entité. Retourne `media_id`, `final_drive_file_id`, `image_role`, `image_index`, `is_primary`.

**Utilisation typique :** toujours appeler cet endpoint avant toute opération sur les photos pour connaître l'état actuel.

#### PUT /api/{entity}/{id}/photos

Remplace entièrement la galerie. **Opération atomique** : la liste fournie remplace l'intégralité de la table `*_media` pour cette entité.

Body (identique pour equipment, accessory, consumable) :
```json
{
  "photos": [
    {
      "final_drive_file_id": "1AbCdEfGh...",
      "image_role": "overview",
      "image_index": 0
    },
    {
      "final_drive_file_id": "2XyZaBcDe...",
      "image_role": "nameplate",
      "image_index": 1
    }
  ]
}
```

`image_role` : `overview` | `nameplate` | `detail`

**Règles pour les rôles :**
- `overview` : photo générale de l'objet entier — la photo principale affichée dans le catalogue
- `nameplate` : photo de la plaque signalétique (marque, modèle, numéro de série)
- `detail` : photo d'un détail spécifique (connecteur, état d'usure, accessoire monté)

**Séquence obligatoire pour ajouter/déplacer une photo :**
```
1. Déplacer le fichier Drive dans le dossier de la fiche cible :
   POST /api/drive/files/{file_id}/move  { "new_parent_id": "<folder_id_fiche_cible>" }

2. Lire les photos existantes :
   GET /api/{entity}/{id}/photos

3. Mettre à jour la liste avec la nouvelle photo :
   PUT /api/{entity}/{id}/photos  { "photos": [...existantes + { final_drive_file_id: file_id, image_role, image_index }] }
```

**Séquence obligatoire pour retirer une photo :**
```
1. Lire les photos existantes :
   GET /api/{entity}/{id}/photos

2. Mettre à jour la liste sans la photo à retirer :
   PUT /api/{entity}/{id}/photos  { "photos": [...existantes SAUF la photo retirée] }

3. Si la photo doit être supprimée définitivement de Drive :
   (action manuelle — ne pas supprimer sans confirmation utilisateur)
   Si la photo doit être déplacée vers une autre fiche : suivre Situation U
```

#### DELETE /api/{entity}/{id}/photos/{media_id} *(v4.6)*

Supprime une seule entrée de la table `*_media` à partir de son `media_id` (UUID). **Ne supprime pas le fichier sur Google Drive.**

```
DELETE /api/equipment/{equipment_id}/photos/{media_id}
DELETE /api/accessories/{accessory_id}/photos/{media_id}
DELETE /api/consumables/{consumable_id}/photos/{media_id}
```

**Corps de la requête :** aucun

**Réponse 200 :**
```json
{ "deleted": true, "media_id": "uuid-med-..." }
```

**Quand utiliser cet endpoint vs `PUT /api/{entity}/{id}/photos` :**
- `DELETE /photos/{media_id}` → supprimer **une seule photo** de manière ciblée (plus simple, moins risqué)
- `PUT /photos` → **remplacer toute la galerie** (à préférer quand on réorganise plusieurs photos en même temps)

> ⚠️ Après un DELETE photo, le fichier Drive reste en place. Si la photo doit aussi être effacée de Drive, le faire manuellement avec confirmation utilisateur.

### 9.8 Bridge Google Drive

> Ces endpoints nécessitent un compte de service configuré via `GOOGLE_APPLICATION_CREDENTIALS`. Si Drive n'est pas disponible, ils retournent HTTP 503.

```
GET  /api/drive/folder/{folder_id}         → liste les fichiers d'un dossier
GET  /api/drive/files/{file_id}            → métadonnées d'un fichier (nom, taille, mimeType…)
POST /api/drive/folder                     → créer un dossier
POST /api/drive/files/{file_id}/move       → déplacer vers un autre dossier
POST /api/drive/files/{file_id}/copy       → copier dans un dossier
PATCH /api/drive/files/{file_id}/rename    → renommer
```

**Quand utiliser chaque opération Drive :**

| Opération | Quand l'utiliser |
|---|---|
| `GET /api/drive/folder/{id}` | Avant toute migration : auditer ce qu'il y a dans un dossier |
| `GET /api/drive/files/{id}` | Vérifier qu'un fichier existe avant de le déplacer |
| `POST /api/drive/folder` | TOUJOURS avant de créer une nouvelle fiche SIGA |
| `POST /api/drive/files/{id}/move` | TOUJOURS avant de mettre à jour une référence photo dans SIGA |
| `POST /api/drive/files/{id}/copy` | Uniquement si on veut garder une copie dans le dossier source |
| `PATCH /api/drive/files/{id}/rename` | Pour normaliser les noms de fichiers après déplacement |

**Body `POST /api/drive/folder` :**
```json
{ "name": "Perforateur_Bosch_GBH", "parent_id": "1FolderDriveId..." }
```

> Le `parent_id` doit être l'ID du dossier parent existant sur Drive, jamais `"root"` sauf pour `SIGA_TEMP`.

**Body `POST /api/drive/files/{id}/move` :**
```json
{ "new_parent_id": "1FolderDriveId..." }
```

> Cette opération déplace le fichier — il disparaît du dossier source et apparaît dans le dossier cible. Irréversible sans un nouveau déplacement.

**Body `POST /api/drive/files/{id}/copy` :**
```json
{ "new_parent_id": "1FolderDriveId...", "new_name": "Copie_photo.jpg" }
```

**Body `PATCH /api/drive/files/{id}/rename` :**
```json
{ "new_name": "Bosch_GBH_overview.jpg" }
```

### 9.9 Réassignation photo *(v4.4 — toutes entités)*

```
POST /api/media/reassign
```

Déplace ou copie une photo entre entités. Supporte toutes les combinaisons : equipment, accessory, consumable → equipment, accessory, consumable.

**Body :**
```json
{
  "source_entity_type": "equipment",
  "source_entity_id": "uuid-equip-...",
  "target_entity_type": "accessory",
  "target_entity_id": "uuid-acc-...",
  "photo_id": "uuid-media-...",
  "mode": "move"
}
```

`source_entity_type` : `equipment` (défaut) | `accessory` | `consumable`
`target_entity_type` : `equipment` | `accessory` | `consumable`
`mode` : `move` (supprime la source) | `copy` (garde les deux)

> **Note :** `photo_id` est le `media_id` dans la table `*_media` correspondant au `source_entity_type`. Pour une source accessoire, c'est le `media_id` dans `accessory_media`.

### 9.10 Migration atomique (reclassification)

```
POST /api/admin/migrations/reclassify?dry_run=true|false
```

Opération de migration complète en une seule transaction. Avec `?dry_run=true` retourne un plan sans modifier la base.

**Body :**
```json
{
  "source_equipment_id": "uuid-old-...",
  "action": "split_record",
  "target_equipment": {
    "label": "Perforateur SDS-Plus 18V",
    "migration_status": "REVIEWED"
  },
  "new_accessories": [
    { "label": "Batterie 18V 4Ah", "brand": "Bosch", "stock_qty": 2 }
  ],
  "new_consumables": [
    { "label": "Foret SDS-Plus Ø10mm", "unit": "pcs", "stock_qty": 5 }
  ],
  "link_existing_accessories": [
    { "accessory_id": "uuid-bat-existing-..." }
  ],
  "photo_mapping": [],
  "source_record_policy": "archive",
  "operator": "openclaw",
  "notes": "Scission fiche outil + accessoires"
}
```

**Actions disponibles :**

| `action` | Description |
|---|---|
| `split_record` | Conserve l'équipement, crée les accessoires/consommables associés et les lie |
| `reclassify_as_accessory` | Archive l'équipement et crée un accessoire équivalent |
| `reclassify_as_consumable` | Archive l'équipement et crée un consommable équivalent |

**Réponse (exécution réelle) :**
```json
{
  "ok": true,
  "dry_run": false,
  "log_id": "uuid-log-...",
  "created_accessory_ids": ["uuid-new-acc-..."],
  "created_consumable_ids": ["uuid-new-con-..."],
  "links_created": 2,
  "source_archived": true,
  "legacy_mapping_id": "uuid-map-...",
  "message": "Migration 'split_record' effectuée avec succès."
}
```

**Réponse `dry_run=true` :**
```json
{
  "ok": true,
  "dry_run": true,
  "plan": {
    "source_equipment_id": "uuid-old-...",
    "action": "split_record",
    "accessories_to_create": 1,
    "consumables_to_create": 1,
    "links_to_create": 2,
    "source_will_be_archived": true
  },
  "message": "Plan de migration calculé (dry_run=true — aucune modification effectuée)."
}
```

### 9.11 Journal d'audit

```
GET  /api/admin/migrations/logs
POST /api/equipment/{equipment_id}/audit   ← v4.6
```

#### GET /api/admin/migrations/logs

**Paramètres query :** `operator`, `operation`, `source_entity_id`, `limit` (défaut 100)

Retourne les entrées du journal de migration/audit triées par date décroissante.

#### POST /api/equipment/{equipment_id}/audit *(v4.6)*

Insère manuellement une entrée dans la table `equipment_audit` pour tracer une action effectuée par OpenClaw ou un opérateur humain.

**Body :**
```json
{
  "action": "VALIDATED",
  "changed_fields": "review_required → false",
  "operator": "OpenClaw"
}
```

| Champ | Type | Requis | Description |
|---|---|---|---|
| `action` | string | Oui | Libellé de l'action (ex. `VALIDATED`, `ARCHIVED`, `PHOTO_DELETED`, `SPECS_UPDATED`) |
| `changed_fields` | string | Non | Description lisible des champs modifiés |
| `operator` | string | Non | Qui a déclenché l'action (`OpenClaw`, `UI`, `n8n`, etc.) |

**Réponse 200 :**
```json
{ "audit_id": "uuid-aud-...", "equipment_id": "uuid-eq-...", "action": "VALIDATED" }
```

**Utilisation typique :** appeler cet endpoint après toute modification significative d'un équipement (validation de fiche, suppression de photo, correction de specs) pour garder une trace dans l'audit trail.

### 9.12 Traçabilité legacy → canonical

```
GET /api/admin/migrations/legacy-mappings/{equipment_id}
```

Retourne le mapping legacy pour un équipement source : quels accessoires/consommables en ont été extraits.

**Réponse :**
```json
{
  "mapping_id": "uuid-map-...",
  "legacy_equipment_id": "uuid-old-...",
  "canonical_equipment_id": "uuid-old-...",
  "derived_accessory_ids": ["uuid-acc-..."],
  "derived_consumable_ids": ["uuid-con-..."],
  "notes": "Scission Mars 2026"
}
```

### 9.13 Export bulk

```
GET /api/admin/export?include_archived=false
```

Exporte l'intégralité de l'inventaire (équipements, accessoires, consommables, liens). `include_archived=true` pour inclure les archivés.

**Réponse :**
```json
{
  "exported_at": "2026-03-22T14:00:00",
  "counts": { "equipment": 142, "accessories": 38, "consumables": 67, ... },
  "equipment": [...],
  "accessories": [...],
  "consumables": [...],
  "links_compatibility": [...],
  "links_consumables": [...]
}
```

### 9.14 Détection et nettoyage de doublons *(v4.5)*

#### Lister les doublons existants

```
GET /api/admin/duplicates
```

Retourne tous les groupes d'accessoires et consommables **non-archivés** dont le `label + brand` apparaît plus d'une fois en base (correspondance exacte, insensible à la casse). Ne couvre pas les équipements.

**Réponse :**
```json
{
  "ok": true,
  "accessories_duplicates": [
    {
      "label": "Jeu de lames pour outil multifonction AEG",
      "brand": "AEG",
      "count": 3,
      "ids": "uuid-a-..., uuid-b-..., uuid-c-..."
    }
  ],
  "consumables_duplicates": [],
  "total_accessory_groups": 1,
  "total_consumable_groups": 0
}
```

> **À utiliser avant toute création** d'accessoire ou consommable pour vérifier l'absence de doublon. Les endpoints `POST /api/accessories` et `POST /api/consumables` font cette vérification automatiquement depuis la v4.5, mais cet endpoint permet un audit manuel.

#### Archiver tous les enregistrements d'un même label

```
POST /api/admin/archive-by-label?entity_type=accessory&label=Jeu+de+lames+pour+outil+multifonction+AEG
POST /api/admin/archive-by-label?entity_type=consumable&label=Foret+SDS-Plus+Ø10+béton
```

Archive **tous** les enregistrements non-archivés du type donné dont le label correspond (insensible à la casse). Utile pour nettoyer tous les doublons d'une fiche en une seule opération.

**Paramètres :**

| Paramètre | Obligatoire | Description |
|---|---|---|
| `entity_type` | Oui | `accessory` ou `consumable` |
| `label` | Oui | Label exact à archiver (insensible à la casse) |

**Réponse :**
```json
{
  "ok": true,
  "archived_count": 3,
  "entity_type": "accessory",
  "label": "Jeu de lames pour outil multifonction AEG"
}
```

### 9.15 Photos orphelines & multi-photos *(v4.3 → v4.4)*

> **v4.3 :** `PUT /api/equipment/{id}/photos` corrigé (ingestion_id rendu nullable) + endpoints `attach` ajoutés.
> **v4.4 :** Les accessoires et consommables supportent désormais plusieurs photos via `accessory_media` / `consumable_media`. Les endpoints `attach` insèrent dans ces tables (au lieu de simplement écraser `drive_file_id`).

#### Détecter les photos orphelines dans un dossier Drive

```
GET /api/drive/orphan-photos?equipment_id=<uuid>
GET /api/drive/orphan-photos?accessory_id=<uuid>
GET /api/drive/orphan-photos?consumable_id=<uuid>
GET /api/drive/orphan-photos?folder_id=<drive_folder_id>
```

Retourne les fichiers présents dans un dossier Drive mais absents de **toutes** les tables media (`equipment_media`, `accessory_media`, `consumable_media`).

**Paramètres (au moins un obligatoire) :**
- `equipment_id` : utilise le `final_drive_folder_id` de l'équipement
- `accessory_id` : utilise le `final_drive_folder_id` de l'accessoire (depuis `accessory_media`)
- `consumable_id` : utilise le `final_drive_folder_id` du consommable (depuis `consumable_media`)
- `folder_id` : ID Drive direct du dossier (si l'entité n'a pas encore de `final_drive_folder_id`)

**Réponse :**
```json
{
  "folder_id": "1FolderDriveId...",
  "entity": "equipment/uuid-...",
  "equipment_id": "uuid-...",
  "total_files_in_folder": 4,
  "already_linked": 1,
  "orphan_count": 3,
  "orphans": [
    {
      "file_id": "1HGGLwGlmVY...",
      "name": "overview.jpg",
      "mime_type": "image/jpeg",
      "size": 245000,
      "web_view_link": "https://drive.google.com/file/d/..."
    }
  ]
}
```

**Points clés :**
- Appeler cet endpoint AVANT d'attacher des photos pour connaître exactement ce qui est orphelin
- `already_linked` inclut les fichiers référencés dans **n'importe laquelle** des 3 tables media — ne pas les ré-attacher

---

#### Attacher une photo orpheline à une fiche (sans effacer les existantes)

```
POST /api/equipment/{equipment_id}/photos/attach
POST /api/accessories/{accessory_id}/photos/attach
POST /api/consumables/{consumable_id}/photos/attach
```

Insère une nouvelle entrée dans la table `*_media` correspondante **sans effacer les photos existantes** (contrairement à `PUT /photos` qui remplace tout).

**Corps (identique pour les 3 types d'entité) :**
```json
{
  "file_id": "1HGGLwGlmVY...",
  "role": "overview",
  "folder_id": "1FolderDriveId...",
  "filename": "dewalt_dc540_overview.jpg",
  "mime_type": "image/jpeg",
  "is_primary": true,
  "attached_by": "openclaw"
}
```

| Champ | Obligatoire | Valeurs |
|---|---|---|
| `file_id` | Oui | Drive file_id de la photo |
| `role` | Non (défaut: `overview`) | `overview` · `nameplate` · `detail` |
| `folder_id` | Non | Drive folder_id parent (traçabilité) |
| `filename` | Non | Nom du fichier |
| `mime_type` | Non | `image/jpeg` · `image/png` · … |
| `is_primary` | Non (défaut: auto) | Calculé automatiquement si première photo |
| `attached_by` | Non (défaut: `openclaw`) | Traçabilité — qui a fait l'attachement |

**Réponse (pour accessoire) :**
```json
{
  "ok": true,
  "media_id": "uuid-media-...",
  "accessory_id": "uuid-acc-...",
  "file_id": "1HGGLwGlmVY...",
  "role": "overview",
  "image_index": 0,
  "is_primary": true,
  "message": "Photo attachée à l'accessoire (role=overview, index=0)."
}
```

**Erreur 409 si doublon :**
```json
{
  "detail": "Le fichier 1HGGLwGlmVY... est déjà lié à l'accessoire uuid-acc-..."
}
```

**Points clés :**
- `image_index` est calculé automatiquement (max existant + 1)
- La première photo ajoutée (`image_index=0`) devient automatiquement `is_primary=true` et met à jour `drive_file_id` pour la rétrocompatibilité
- 409 = déjà lié : traiter comme succès, passer à la suivante
- Ne requiert PAS d'`ingestion_id`

---

### Situation X — Rattacher des photos orphelines à une fiche *(processus complet v4.3/v4.4)*

Ce processus s'applique quand des photos existent physiquement dans un dossier Drive mais ne sont pas référencées dans la base SIGA (photos créées par n8n ou OpenClaw mais jamais liées, ou liées à la mauvaise fiche). Fonctionne pour les 3 types d'entité : équipement, accessoire, consommable.

```
PRÉREQUIS : s'assurer que migrate_to_v4_3.py et migrate_to_v4_4.py ont été exécutés.

ÉTAPE 1 — Identifier les photos orphelines
  Pour un équipement :    GET /api/drive/orphan-photos?equipment_id=<uuid>
  Pour un accessoire :    GET /api/drive/orphan-photos?accessory_id=<uuid>
  Pour un consommable :   GET /api/drive/orphan-photos?consumable_id=<uuid>
  → Lire le champ "orphans" : liste des file_ids non référencés
  → Si orphan_count = 0 : rien à faire

ÉTAPE 2 — Analyser visuellement chaque photo orpheline
  GET /api/drive/files/{file_id}
  → Voir le nom, la taille, le lien Drive
  → VÉRIFIER que la photo correspond bien à la fiche cible (pas une photo d'un autre objet)
  → Déterminer le rôle : overview / nameplate / detail

ÉTAPE 3 — Attacher chaque photo individuellement
  Pour chaque photo orpheline confirmée visuellement :

  Équipement :
    POST /api/equipment/{equipment_id}/photos/attach
    { "file_id": "<file_id>", "role": "<overview|nameplate|detail>", "folder_id": "<folder_id>" }

  Accessoire :
    POST /api/accessories/{accessory_id}/photos/attach
    { "file_id": "<file_id>", "role": "<overview|nameplate|detail>" }

  Consommable :
    POST /api/consumables/{consumable_id}/photos/attach
    { "file_id": "<file_id>", "role": "<overview|nameplate|detail>" }

  → En cas de 409 : la photo est déjà liée — passer à la suivante (succès)
  → En cas d'erreur 503 : réessayer après quelques secondes (DuckDB busy)

ÉTAPE 4 — Vérification
  GET /api/{entity}/{id}/photos
  → Vérifier que toutes les photos attendues sont maintenant listées
  GET /api/drive/orphan-photos?{entity}_id=<uuid>
  → Vérifier que orphan_count = 0

ÉTAPE 5 — Marquer la fiche comme revue (équipements uniquement)
  PATCH /api/equipment/{equipment_id}
  { "migration_status": "REVIEWED", "migrated_by": "openclaw" }
```

**Message type OpenClaw en fin de processus :**
> "Fiche DEWALT DC540 complétée :
> - 3 photos orphelines rattachées (overview, nameplate, detail)
> - Statut mis à jour : REVIEWED
> - Dossier Drive cohérent : 4 fichiers, 4 référencés, 0 orphelins"

---

## Champs de gouvernance *(v4.1)*

Tous les champs suivants sont disponibles sur `equipment`, `accessories` et `consumables` :

| Champ | Type | Valeurs | Description |
|---|---|---|---|
| `archived` | bool | `true` / `false` | Soft-delete — entité masquée par défaut dans les listings |
| `migration_status` | string | `NOT_REVIEWED` / `REVIEWED` / `MIGRATED` / `ARCHIVED` | Avancement dans le workflow de migration |
| `legacy_source_id` | string | ID libre | Référence vers la fiche d'origine avant migration |
| `migrated_at` | timestamp | ISO 8601 | Date/heure de migration |
| `migrated_by` | string | Nom opérateur | Qui a effectué la migration |
| `classification_confidence` | float | 0.0–1.0 | Score de confiance de la classification IA |

**Workflow de migration recommandé :**

```
NOT_REVIEWED  →  REVIEWED  →  MIGRATED  →  ARCHIVED (si doublon/obsolète)
```

---

## Modèle de données v4.1 — Tables supplémentaires

```
legacy_mappings
    mapping_id PK
    legacy_equipment_id    → ID de la fiche d'origine
    canonical_equipment_id → ID de l'équipement résultant (si split)
    derived_accessory_ids  → JSON array des accessoires créés depuis cette fiche
    derived_consumable_ids → JSON array des consommables créés depuis cette fiche
    notes

migration_logs
    log_id PK
    operation              → split_record / reclassify_as_accessory / ...
    operator               → openclaw / jarvis / admin
    source_entity_type     → equipment / accessory / consumable
    source_entity_id       → ID de l'entité source
    target_entities        → JSON { accessory_ids, consumable_ids, links_created }
    details                → JSON libre
    dry_run                → true si simulation
    status                 → COMPLETED / FAILED
    created_at
```

---

## 10. Checklist opérationnelle — Avant / Pendant / Après chaque opération

### Avant de commencer (pour toute opération modifiant des fiches ou des photos)

```
□ J'ai vérifié qu'aucune fiche en doublon n'existe déjà (GET /api/admin/duplicates)
□ J'ai vérifié la cohérence Drive de la fiche source (GET /api/drive/folder/{folder_id})
□ J'ai analysé chaque photo individuellement avant de l'affecter
□ Pour une migration : j'ai fait un dry_run et présenté le plan à l'utilisateur
```

### Pendant l'opération (pour chaque photo traitée)

```
□ J'ai déplacé le fichier physiquement sur Drive AVANT de mettre à jour la base
□ J'ai vérifié que le fichier est bien arrivé dans le dossier cible (GET /api/drive/files/{id})
□ J'ai mis à jour la fiche SIGA avec le nouveau file_id
□ Je n'ai pas affecté une photo d'un objet A à la fiche d'un objet B
```

### Après l'opération

```
□ J'ai vérifié la fiche résultante (GET /api/equipment/{id} ou GET /api/accessories/{id})
□ J'ai vérifié le dossier Drive de la fiche (GET /api/drive/folder/{folder_id})
□ Les deux listes sont cohérentes (même file_ids dans la base et dans Drive)
□ J'ai archivé les fiches sources si nécessaire
□ J'ai rapporté le résultat à l'utilisateur avec un résumé des actions effectuées
```

---

## 11. Erreurs courantes et comment les éviter

### Erreur : "Photo de l'équipement X affectée à l'accessoire Y"

**Cause :** Affectation en masse sans vérification visuelle de chaque photo.

**Solution :** Traiter une photo à la fois. Pour chaque photo, identifier son contenu avant de l'affecter. En cas de doute, demander confirmation à l'utilisateur.

---

### Erreur : "Dossier Drive non créé pour la nouvelle fiche"

**Cause :** Création de la fiche SIGA sans avoir créé le dossier Drive correspondant en amont.

**Solution :** Toujours respecter l'ordre : (1) créer le dossier Drive → (2) créer la fiche SIGA → (3) affecter les photos.

---

### Erreur : "Photos non déplacées physiquement — juste réadressées"

**Cause :** Mise à jour du `final_drive_file_id` dans la base sans appel à `/api/drive/files/{id}/move`.

**Conséquence :** Les photos restent dans le dossier d'origine sur Drive mais la base pointe ailleurs. L'affichage semble correct mais la structure Drive est incohérente.

**Solution :** Toujours appeler `POST /api/drive/files/{file_id}/move` avant de mettre à jour la référence dans SIGA.

---

### Erreur : "Fiches fantômes non nettoyées"

**Cause :** Nouvelles fiches créées sans archiver les anciennes.

**Conséquence :** Le catalogue contient des doublons, les recherches retournent des résultats parasites.

**Solution :** Après chaque migration, archiver systématiquement les fiches sources. Utiliser le workflow Situation S pour le nettoyage en lot.

---

### Erreur : "Migration lancée sans dry_run"

**Cause :** Appel direct à `/api/admin/migrations/reclassify?dry_run=false` sans avoir analysé le plan.

**Conséquence :** Des entités sont créées, des liens sont posés, la fiche source est archivée — irréversible sans intervention manuelle.

**Solution :** Toujours passer par `dry_run=true` d'abord, lire le plan, le présenter à l'utilisateur, attendre confirmation.
