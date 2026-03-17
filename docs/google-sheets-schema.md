# Schema Google Sheets — Projection SIGA

## Fichier cible

Nom du fichier Google Sheets : `SIGA - Equipements`
Onglet principal : `Equipements`

---

## Colonnes de l'onglet Equipements

| Colonne             | Type       | Source DuckDB                   | Description                                          |
|---------------------|------------|---------------------------------|------------------------------------------------------|
| `equipment_id`      | Texte      | `equipment.equipment_id`        | Identifiant unique UUID                              |
| `ingestion_id`      | Texte      | `equipment.ingestion_id`        | Identifiant de session d'ingestion                   |
| `date_ingestion`    | Date/Heure | `equipment_ingestion_log.ts`    | Timestamp de la reception WhatsApp                   |
| `category`          | Texte      | `equipment.category`            | Machine / Outillage Manuel / Consommable / Accessoire|
| `subtype`           | Texte      | `equipment.subtype`             | Sous-type (ex: Perceuse, Ponceuse, Meuleuse...)      |
| `label`             | Texte      | `equipment.label`               | Libelle complet propose par l'IA                     |
| `brand`             | Texte      | `equipment.brand`               | Marque (ex: Bosch, DeWalt, Makita...)                |
| `model`             | Texte      | `equipment.model`               | Reference modele                                     |
| `serial_number`     | Texte      | `equipment.serial_number`       | Numero de serie si visible sur plaque                |
| `condition_label`   | Texte      | `equipment.condition_label`     | Bon / Moyen / Mauvais / Inconnu                      |
| `location_hint`     | Texte      | `equipment.location_hint`       | Emplacement dans l'atelier                           |
| `ownership_mode`    | Texte      | `equipment.ownership_mode`      | Propriete / Location / Pret                          |
| `status`            | Texte      | `equipment.status`              | draft / review_required / validated                  |
| `review_required`   | Booleen    | `equipment.review_required`     | TRUE si une revue admin est necessaire               |
| `image_url`         | URL        | calcule depuis `equipment_media`| URL image principale pour AppSheet                   |
| `drive_folder_url`  | URL        | `equipment.final_drive_folder_web_view_link` | Lien vers le dossier Drive             |
| `notes`             | Texte      | `equipment.business_context_json` (extrait) | Notes ou contexte metier           |

---

## Note sur l'URL image

AppSheet affiche les images depuis une URL directe.

Le format recommande pour Google Drive :
```
https://drive.google.com/uc?export=view&id=FILE_ID
```

Ce format est prefere a `webViewLink` (qui ouvre la page Drive, pas l'image directement).

Le noeud n8n de projection doit construire cette URL depuis l'ID du fichier image principal (`equipment_media` ou `final_drive_web_view_link`).

---

## Noeud n8n de projection

Le noeud a ajouter dans le workflow en fin de traitement :

- Type : `Google Sheets`
- Operation : `Append or Update`
- Spreadsheet : `SIGA - Equipements`
- Sheet : `Equipements`
- Matching column : `equipment_id`

### Mapping des champs

```json
{
  "equipment_id":    "{{ $json.equipment_id }}",
  "ingestion_id":    "{{ $json.ingestion_id }}",
  "date_ingestion":  "{{ $json.created_at }}",
  "category":        "{{ $json.category }}",
  "subtype":         "{{ $json.subtype }}",
  "label":           "{{ $json.label }}",
  "brand":           "{{ $json.brand }}",
  "model":           "{{ $json.model }}",
  "serial_number":   "{{ $json.serial_number }}",
  "condition_label": "{{ $json.condition_label }}",
  "location_hint":   "{{ $json.location_hint }}",
  "ownership_mode":  "{{ $json.ownership_mode }}",
  "status":          "{{ $json.status }}",
  "review_required": "{{ $json.review_required }}",
  "image_url":       "https://drive.google.com/uc?export=view&id={{ $json.primary_media_drive_id }}",
  "drive_folder_url":"{{ $json.final_drive_folder_web_view_link }}"
}
```

---

## Ordre de priorite

Ce noeud n8n de projection est a ajouter apres :
1. stabilisation du run reel WhatsApp end-to-end,
2. validation de l'ecriture DuckDB correcte.

Ne pas brancher AppSheet avant que le Google Sheet soit alimente par au moins un run reel valide.
