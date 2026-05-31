# Analyse fonctionnelle - SIGA

Version: 2026-05-31

## 1. Objectif

SIGA est le système de gestion d'inventaire de l'atelier. Le projet doit rester simple dans son usage quotidien:

- Nicolas capture rapidement une situation terrain depuis Telegram.
- Les photos et le texte sont déposés proprement dans Google Drive.
- L'analyse et les actions métier sont faites plus tard, au calme, par un agent SIGA dédié.

Le système ne doit plus essayer de transformer immédiatement chaque message terrain en fiche SIGA complète. L'envoi Telegram est une collecte, pas une décision métier.

## 2. Acteurs

| Acteur | Rôle |
|---|---|
| Nicolas | Envoie les briefs terrain, valide les décisions importantes |
| n8n | Collecte Telegram, regroupe les albums, dépose dans Drive |
| Google Drive | Stocke les dossiers de briefs et les photos |
| Agent SIGA | Lit les dossiers `pending`, analyse les photos, applique les règles métier |
| API SIGA | Point d'action sécurisé sur DuckDB et Drive |
| Portail SIGA | Interface desktop Streamlit pour consulter et corriger l'inventaire |

## 3. Flux cible

```text
Telegram
  -> Collector n8n
  -> Buffer Drive temporaire
  -> Batch processor n8n
  -> Dossier final Drive
       - brief.json
       - photos

Puis plus tard:

Agent SIGA
  -> lit brief.json + photos
  -> raisonne avec docs/SIGA-API-OpenClaw-Notice.md
  -> crée/modifie/archive dans SIGA via API
  -> déplace/attache les photos correctement dans Drive
  -> marque le brief comme processed
```

## 4. Collecte Telegram

### 4.1 Entrée

Nicolas envoie dans Telegram:

- un texte libre;
- une ou plusieurs photos;
- éventuellement une légende sur un album.

Telegram transmet les albums photo en plusieurs messages séparés avec un même `media_group_id`. Le système doit donc regrouper ces messages avant de créer le dossier final.

### 4.2 Collector

Workflow: `SIGA - Telegram Collector Buffer v2`

Responsabilités:

- recevoir les updates Telegram;
- normaliser le texte, les métadonnées et les binaires;
- créer ou réutiliser `SIGA_TEMP/SIGA_TELEGRAM_BUFFER`;
- écrire une part JSON par message;
- uploader les médias reçus dans le buffer.

Le collector ne crée pas le brief final. Il capture uniquement les parts.

### 4.3 Batch processor

Workflow: `SIGA - Telegram Batch Processor v8 (Loop Clean)`

Responsabilités:

- tourner chaque minute;
- lister les parts en attente;
- attendre une fenêtre calme pour les albums;
- regrouper les messages ayant le même `media_group_id`;
- créer un seul dossier final sous `SIGA_ATELIER`;
- uploader toutes les photos;
- écrire un seul `brief.json`;
- envoyer une confirmation Telegram.

## 5. Contrat du dossier Drive

Chaque brief final est un dossier autonome:

```text
SIGA_ATELIER/<brief_id>/
  brief.json
  photo_1.jpg
  photo_2.jpg
```

`brief.json` est le contrat entre n8n et l'agent SIGA. Son schéma est versionné dans `docs/brief.schema.json`.

Champs essentiels:

| Champ | Sens |
|---|---|
| `brief_id` | Identifiant stable du brief |
| `status` | `pending`, `processing`, `processed` ou `error` |
| `text` | Texte ou légende terrain |
| `photos[]` | Photos présentes dans le même dossier |
| `drive` | ID et URL du dossier Drive |
| `telegram` | Métadonnées de provenance |
| `siga` | Zone remplie par l'agent après traitement |

## 6. Traitement par agent SIGA

L'agent SIGA intervient uniquement quand Nicolas décide de traiter un dossier.

Il doit:

1. lire `brief.json`;
2. inspecter les photos;
3. identifier l'intention métier: nouvelle fiche, correction, ajout d'accessoire, consommable, sortie, retour, archive;
4. utiliser `docs/SIGA-API-OpenClaw-Notice.md` comme règle opérationnelle;
5. agir via l'API SIGA;
6. garantir la cohérence entre DuckDB et Drive;
7. écrire le résultat dans `brief.json`;
8. passer le brief à `processed` ou `error`.

## 7. Règles métier non négociables

La notice API SIGA reste la référence. Les règles centrales sont:

- une fiche SIGA doit avoir un dossier Drive réel;
- une photo ne doit jamais être rattachée à une mauvaise fiche;
- déplacer une photo dans la base implique aussi de déplacer le fichier Drive;
- toute migration risquée doit passer par un dry run;
- les suppressions réelles sont évitées, l'archivage est préféré;
- l'agent doit demander confirmation si une photo ou une action est ambiguë.

## 8. Composants conservés

| Composant | Statut | Justification |
|---|---|---|
| `SIGA - Telegram Collector Buffer v2` | actif | Entrée terrain |
| `SIGA - Telegram Batch Processor v8 (Loop Clean)` | actif | Regroupement et dossier final |
| `SIGA - Claude Media Upload` | actif | Utilitaire pour agent qui doit pousser un média vers Drive |
| `SIGA - Global Error Handler` | actif | Supervision n8n |
| `SIGA - Delete Equipment Drive Folder` | actif | Support portail/API |
| `SIGA - Ingestion Atelier V1` | archivé | Ancien pipeline automatique, hors cible |

## 9. Ce qui sort du flux cible

Sont hors flux officiel:

- les variantes anciennes de collector et batch processor;
- l'ancien workflow direct Telegram vers Drive;
- l'ancien workflow automatique WhatsApp/OpenClaw vers OCR + DuckDB;
- les exports intermédiaires non validés;
- les notes de débogage liées à des versions abandonnées.

Ces éléments sont conservés dans `archive/` pour mémoire, mais ne doivent pas être réimportés sans décision explicite.

## 10. Validation attendue

Le flux est considéré sain quand ce test passe:

1. envoyer depuis Telegram un message avec plusieurs photos;
2. obtenir une seule confirmation Telegram;
3. constater un seul dossier final dans `SIGA_ATELIER`;
4. vérifier que toutes les photos sont dans ce dossier;
5. vérifier que `brief.json` contient le texte et `status: "pending"`;
6. traiter ce dossier avec l'agent SIGA;
7. vérifier que SIGA et Drive restent cohérents;
8. vérifier que `brief.json` passe à `processed`.
