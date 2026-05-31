# Skills SIGA

Ce dossier contient les instructions utilisables par un agent pour intervenir sur SIGA sans
revenir aux anciens workflows automatiques.

## `siga-saisie-atelier`

Skill de saisie atelier destiné à traiter les dossiers de briefs Telegram déposés dans Drive.
Il impose le chemin cible du projet :

1. lire un dossier Drive contenant `brief.json` et les photos ;
2. analyser le brief et les images ;
3. vérifier les doublons ;
4. proposer un plan et attendre confirmation ;
5. écrire uniquement via l'API SIGA ;
6. marquer le brief comme traité.

Règle centrale : aucune écriture directe dans DuckDB et aucune manipulation Drive hors des
endpoints API prévus. Si un endpoint manque, l'agent doit le signaler au lieu d'improviser.

Références importantes :

- `references/session-brief.md` : brief à coller au début d'une session de traitement SIGA ;
- `references/analysis-playbook.md` : méthode d'analyse riche des objets, photos, quantités,
  liens et recherches web ;
- `references/api-reference.md` : endpoints autorisés ;
- `references/access.md` : accès technique à l'API locale.
