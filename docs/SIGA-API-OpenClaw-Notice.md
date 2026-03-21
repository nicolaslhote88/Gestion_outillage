# Notice d'utilisation de l'API SIGA pour OpenClaw

**Version :** 3.0 — Mars 2026
**Audience :** skill OpenClaw (chat principal + WhatsApp)
**Base URL :** variable d'environnement `SIGA_API_BASE_URL`
**Auth :** header `Authorization: Bearer $SIGA_API_TOKEN`

---

## Vue d'ensemble

L'API SIGA est l'interface entre OpenClaw et la base de données d'inventaire d'atelier.
Elle couvre **six domaines** :

| Domaine | Ce que tu peux faire |
|---|---|
| **Équipements** | Chercher un outil, vérifier s'il est disponible |
| **Mouvements** | Enregistrer les sorties et les retours d'outils |
| **Kits** | Créer, composer, sortir et rentrer des caisses à outils |
| **Kiosque** | Afficher un outil sur l'écran de l'atelier |
| **Réservations** | Réserver un outil sur une plage de dates, vérifier les conflits, annuler |
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
- Accessoires livrés, consommables associés, éléments associés (si renseignés)
- Notes
- Statut de disponibilité : « Disponible » ou « En cours d'utilisation (emprunteur, retour prévu) »

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

## Référence complète des endpoints

| Méthode | Endpoint | Auth | Usage |
|---|---|---|---|
| GET | `/api/health` | Non | État du serveur |
| GET | `/api/equipment/search?q=` | Oui | Recherche d'outil |
| GET | `/api/equipment/{id}/status` | Oui | Disponibilité |
| POST | `/api/movements/checkout` | Oui | Sortie d'outil(s) |
| POST | `/api/movements/checkin` | Oui | Retour d'outil(s) |
| GET | `/api/movements/active` | Oui | Sorties en cours |
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
| POST | `/api/display/show` | Oui | Afficher fiche outil sur kiosque |
| POST | `/api/display/show-kit` | Oui | Afficher fiche kit sur kiosque |
| POST | `/api/display/show-movements` | Oui | Afficher sorties en cours sur kiosque |
| POST | `/api/display/show-confirmation` | Oui | Afficher écran de confirmation |
| POST | `/api/display/clear` | Oui | Repasser le kiosque en veille |
| GET | `/api/reservations/conflicts?equipment_id=&start=&end=` | Oui | Vérifier conflits avant réservation |
| POST | `/api/reservations` | Oui | Créer une réservation |
| GET | `/api/reservations/active` | Oui | Lister les réservations à venir |
| DELETE | `/api/reservations/{res_id}` | Oui | Annuler une réservation |

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
| 409 | `conflict` | La plage demandée est déjà réservée ou l'outil est en maintenance |
| 503 | `screen_unavailable` | L'écran kiosque ou la base est inaccessible |

En cas d'erreur 503, la base DuckDB est temporairement verrouillée par n8n. Réessayer dans quelques secondes.
