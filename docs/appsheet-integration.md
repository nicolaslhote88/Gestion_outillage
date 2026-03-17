# Integration AppSheet — SIGA

## Decision

La solution mobile retenue n'est plus Glide.

La cible est **AppSheet (Google)** en mode Prototype (gratuit, < 10 utilisateurs).

Raisons du choix :
- gratuit en mode prototype (moins de 10 utilisateurs, 1 seul utilisateur ici : Nicolas)
- genere une app mobile native a partir d'un Google Sheet
- supporte scan QR code, prise de photo, mode hors-ligne
- s'integre nativement avec Google Drive et Google Sheets
- aucun changement dans le pipeline n8n : on garde la projection vers Google Sheet

---

## Architecture ciblee

```
n8n (SIGA - Ingestion Atelier V1)
    |
    v
Google Sheet "SIGA - Equipements"
    |
    v
AppSheet (lecture automatique du Sheet)
    |
    v
Application mobile atelier
```

---

## Fonctionnalites AppSheet attendues

- Consultation de la liste des equipements
- Recherche par marque / modele / categorie / emplacement
- Affichage de la photo depuis Google Drive (URL webViewLink)
- Visualisation de l'etat / du statut
- Reperage des equipements flagges `review_required`
- Mode hors-ligne pour consultation sans connexion

---

## Configuration AppSheet

### Source de donnees

- Type : Google Sheets
- Fichier : `SIGA - Equipements` (a creer dans Google Drive)
- Onglet principal : `Equipements`

### Schema de l'onglet principal

Voir `docs/google-sheets-schema.md` pour le detail des colonnes.

### Configuration de l'image

AppSheet peut afficher des images depuis une URL.
Champ image dans AppSheet : colonne `image_url` du Sheet.

Important : les URLs de type `webContentLink` (telechargement direct) sont preferees aux `webViewLink` pour un rendu image propre dans AppSheet.

Format cible : `https://drive.google.com/uc?export=view&id=FILE_ID`

Le noeud n8n de projection doit donc calculer cette URL a partir de l'`id` Drive du fichier.

---

## Parametrage AppSheet recommande

### Vues a creer

1. **Vue Liste** : tous les equipements, tri par date ingestion desc
2. **Vue Detail** : fiche complete avec image, marque, modele, etat, emplacement
3. **Vue Galerie** : grille d'images
4. **Vue Filtree** : equipements avec `review_required = true`

### Filtres utiles

- Par `category` : Machine / Outillage Manuel / Consommable / Accessoire
- Par `status` : draft / review_required / validated
- Par `condition_label` : Bon / Moyen / Mauvais / Inconnu

### Colonnes a masquer en mobile

- `ingestion_id`
- `business_context_json`
- `equipment_id`

---

## Limites connues en mode Prototype AppSheet

- Application non publiable publiquement (acceptable ici, usage prive)
- Pas de notifications push natives sans abonnement payant
- Pas d'automatisations avancees (acceptable : les automatisations restent dans n8n)

---

## Etapes de mise en place

1. Creer le Google Sheet `SIGA - Equipements` avec le schema documente
2. Creer le noeud n8n de projection et le tester (voir `docs/google-sheets-schema.md`)
3. Connecter AppSheet sur le Google Sheet (New App > From Google Sheets)
4. Configurer les vues et les colonnes image
5. Tester le rendu photo sur smartphone reel
6. Valider la qualite des URLs image (preferer `export=view` plutot que `webViewLink`)

---

## Etat actuel

Non encore realise. Cette integration est en attente de la stabilisation du pipeline live d'ingestion.

Priorite : faire passer un run reel complet avant de brancher la projection Google Sheets / AppSheet.
