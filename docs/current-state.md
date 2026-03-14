# Etat actuel SIGA

## Verdict synthetique

Le projet SIGA est bien cadre et deja partiellement implemente. Le risque principal n'est plus conceptuel: il est dans la stabilisation operationnelle du pipeline live.

La consigne de travail retenue pour ce depot est simple:

1. ne pas repartir en design abstrait,
2. prouver le run reel WhatsApp -> fin de traitement,
3. corriger les ruptures observees,
4. ensuite seulement reduire la dette n8n.

## Snapshot du workflow versionne

- Nom du workflow: `SIGA - Ingestion Atelier V1`
- Statut exporte: `active = true`
- Webhook d'entree: `POST siga-ingestion-v1`
- Nombre de noeuds: `71`
- Principales briques: `Webhook`, `Code`, `If`, `HTTP Request`, `Google Drive`, `OpenAI`
- Stockage local reference dans les scripts: `/files/duckdb/siga_v1.duckdb`

Le flux couvre deja les etapes suivantes:

1. reception et normalisation du message entrant,
2. journalisation initiale dans DuckDB,
3. resolution ou creation du dossier temporaire Google Drive,
4. decoupage des images et OCR inline via OpenAI,
5. construction d'un draft equipement v2,
6. resolution ou creation de l'arborescence finale Drive,
7. deplacement et renommage des medias finaux,
8. ecriture des tables `equipment`, `equipment_media` et mise a jour du log,
9. preparation du message de completion WhatsApp.

## Decision front-end a jour

La cible front mobile ne repose plus sur Glide.

Hypothese de travail retenue:

- n8n pousse les donnees dans Google Sheet,
- AppSheet lit ce Google Sheet et genere l'application mobile,
- la phase actuelle du depot reste concentree sur le pipeline live d'ingestion, pas sur une refonte UX.

L'export n8n versionne ici ne contient pas encore une preuve suffisante du maillon Google Sheet -> AppSheet. Ce point reste a confirmer dans les prochaines validations.

## Risques operationnels a verifier en priorite

- conformite du payload WhatsApp reel recu par le webhook,
- droits Google Drive pour creer, retrouver, deplacer et renommer les fichiers,
- comportement du noeud OCR OpenAI en cas de fichier non lisible ou volumineux,
- robustesse du verrouillage DuckDB lors des ecritures successives,
- qualite du message de completion retourne en fin de run.
