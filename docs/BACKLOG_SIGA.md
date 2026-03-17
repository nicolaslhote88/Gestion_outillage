# Backlog SIGA

## EPIC-01 — Onboarding Vision (MVP actif)

### E01-01 OCR plaque signaletique
- Recevoir image via WhatsApp
- Envoyer image a OpenAI Vision
- Extraire marque / modele / numero de serie / etat
- Retourner une proposition structuree

Statut : implemente dans le workflow live, a valider en reel.

### E01-02 Classification type outil
- Determiner la categorie principale : machine / outillage manuel / consommable / accessoire
- Proposer un sous-type
- Marquer `review_required` si confiance insuffisante

Statut : implemente dans `Build Draft v2`, a valider.

### E01-03 Validation admin avant ecriture finale
- Interface Streamlit : file de validation
- Afficher la proposition IA + image
- Permettre correction / acceptation
- Write-back DuckDB sur validation

Statut : front Streamlit cadre, a implementer / tester.

---

## EPIC-02 — Base de donnees (MVP actif)

### E02-01 Schema SQL mono-utilisateur
Tables cibles :
- `equipment`
- `equipment_media`
- `equipment_review_log`
- `equipment_ingestion_log`

Statut : schema initialise dans DuckDB via `DuckDB Init Schema`.

### E02-02 Silos metier
- Machines
- Consommables
- Outillage manuel
- Accessoires

Statut : gere par le champ `category` dans `equipment`.

### E02-03 Historisation maintenance et flux
- Log des modifications
- Traçabilite des validations

Statut : partiellement couvert par `equipment_review_log`, a enrichir.

---

## EPIC-03 — Prets & Locations (futur)

### E03-01 Fiche emprunteur flash
- Capture identite (photo CNI)
- Conservation max 2 semaines apres retour sans dommage
- Suppression manuelle si litige

Statut : non commence.

### E03-02 Contrat de confiance auto
- Generation automatique

Statut : non commence.

### E03-03 Relances WhatsApp automatiques
- Rappels avant echeance
- Relance si non retour

Statut : non commence.

---

## EPIC-04 — Maintenance preventive (futur)

### E04-01 Carnet d'entretien
- Log des interventions par equipement

Statut : non commence.

### E04-02 Alertes d'usure
- Seuils configures par equipement
- Alerte WhatsApp

Statut : non commence.

---

## EPIC-05 — Gouvernance des donnees (en cours)

### E05-01 Politique conservation scans CNI
- Conservation max 2 semaines apres retour sans dommage
- Suppression manuelle en cas de litige

Statut : decide, a implementer proceduralement.

### E05-02 Referentiel emplacements
- Liste modifiable des emplacements atelier
- Utilise dans `location_hint`

Statut : a creer comme table de reference.

---

## Dette technique n8n (prioritaire avant EPIC-03+)

### DT-01 Gestion d'erreur globale
- Workflow `SIGA - Global Error Handler`
- Log dans table DuckDB `workflow_error_log`
- Champs : `ingestion_id`, `execution_id`, `workflow_name`, `failed_node`, `error_message`, `error_stack`

### DT-02 Sous-workflow Drive
- `Drive - Ensure Folder Exists`
- Entrees : `folder_name`, `parent_id`, `context_label`
- Sorties : `folder_id`, `created`, `webViewLink`
- Remplace les blocs Search / If Exists / Create / Adopt dupliques

### DT-03 Decoupage noeud `Merge OCR + Drive Metadata`
- `Normalize OCR Output`
- `Merge Drive URLs Into Analysis`
- `Validate Merged Structure`

### DT-04 Decoupage noeud `Build Draft v2`
- `Assemble Draft Fields`
- `Apply Status / Review Rules`
- `Validate Draft Payload`

### DT-05 Projection Google Sheets
- Noeud n8n d'ecriture vers Google Sheet
- Schema cible documente dans `docs/google-sheets-schema.md`

### DT-06 AppSheet
- Lecture du Google Sheet par AppSheet
- Configuration application mobile
- Tests smartphone reel
- Qualite des URLs image

---

## Prochaine etape immediate

Executer un test reel documente :
- message WhatsApp `SIGA:` + photo
- capturer run n8n complet
- corriger le premier point cassant
- rejouer avant toute refactorisation
