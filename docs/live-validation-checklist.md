# Checklist validation live

Objectif: produire une preuve de fonctionnement reel avant toute refactorisation.

## Test minimal a realiser

1. Envoyer un vrai message WhatsApp avec photos vers l'entree connectee a `Webhook In`.
2. Verifier la creation ou la reutilisation du dossier temporaire Drive.
3. Verifier l'upload des images et la reponse OCR pour chaque media.
4. Verifier la creation de l'arborescence finale Drive.
5. Verifier les ecritures finales dans `equipment_ingestion_log`, `equipment` et `equipment_media`.
6. Verifier la preparation du message de completion WhatsApp.

## Preuves a conserver

- payload d'entree reel anonymise,
- execution n8n exportee ou capturee,
- identifiants des dossiers et fichiers Drive crees,
- etat final DuckDB sur les trois tables cibles,
- message de completion genere,
- erreurs exactes si une etape echoue.

## Ordre d'intervention recommande

1. faire passer un cas reel de bout en bout,
2. corriger le premier point cassant observe,
3. rejouer le meme cas reel,
4. seulement apres stabilisation, factoriser les noeuds repetitifs et la dette d'orchestration.
