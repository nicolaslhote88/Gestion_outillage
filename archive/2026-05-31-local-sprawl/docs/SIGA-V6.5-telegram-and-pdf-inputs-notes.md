# SIGA V6.5 — Telegram + PDF inputs

## Objectif
Ajouter un second canal d'entrée en parallèle du webhook existant et accepter aussi les documents PDF / fichiers Telegram sans casser la chaîne qui fonctionne déjà pour :
- ingestion webhook / multipart
- propagation binaire
- compression avant OCR
- OCR image
- Drive final + renommage

## Modifications appliquées

### 1) Nouveau trigger
- Ajout de **Telegram Trigger** connecté en parallèle à **Normalize Input**.

### 2) Normalize Input élargi
- Détection du contexte **Telegram** (`message`, `edited_message`, `channel_post`, etc.).
- Extraction du texte depuis `caption` ou `text`.
- Construction de deux tableaux distincts :
  - `images[]`
  - `documents[]`
- Catégorisation des binaires par MIME type / extension :
  - images → OCR
  - PDF et autres fichiers applicatifs → documents
- L'entrée est désormais acceptée s'il y a **au moins une image ou un document**.
- Le `source` devient `telegram` pour les exécutions Telegram.
- L'auth webhook n'est pas exigée pour Telegram.

### 3) OCR image conservé
- **Prepare Binary Inline** a été étendu pour savoir relire les binaires depuis :
  - `Webhook In`
  - `Telegram Trigger`
  - `Normalize Input`
- La boucle compression/OCR n'a pas été supprimée.

### 4) Documents PDF / fichiers
- Nouveau sous-graphe après résolution du dossier final :
  - `Split Documents`
  - `Prepare Document Binary`
  - `Document Binary Ready Gate`
  - `Upload Document to Final Drive`
  - `Merge Document Final Metadata`
- Les documents sont uploadés **directement dans le dossier final**.
- Nommage final :
  - `doc_01_<nom>.pdf`
  - `doc_02_<nom>.pdf`
  - etc.

### 5) Cas document-only
- Ajout de :
  - `Documents Only Gate`
  - `Build Draft Docs Only`
- Permet d'éviter le rejet si l'entrée contient uniquement un PDF / document.
- Le dossier final et le manifest peuvent donc être produits même sans images.

### 6) Finalisation sans régression sur les images
- Les images gardent leur chemin existant de finalisation.
- Les documents sont séquencés avant le démarrage de `Split Final Media` pour que le manifest voie aussi les PDF déjà uploadés.
- Ajout d'un placeholder **No Image Finalization Placeholder** pour le cas document-only.

### 7) Manifest enrichi
- `Build Manifest Text` inclut maintenant une section `documents`.
- `Apply Finalization To Draft` comptabilise aussi les documents dans le succès de finalisation et les rattache au draft final.

## Points d'attention
- Le support PDF ajouté ici est un **support de pièce jointe documentaire**, pas un OCR PDF / parsing de facture.
- Si tu veux extraire automatiquement les données d'une facture PDF, il faudra ajouter ensuite une branche dédiée PDF parsing / OCR document.
