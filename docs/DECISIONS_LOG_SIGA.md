# Journal des decisions — SIGA

---

## D001 — Deploiement Docker sur VPS dedie

**Date** : ante 2026-03-13
**Statut** : Confirme

Le systeme tourne sur un VPS dedie en Docker.
Pas de cloud managed, pas de serverless.

---

## D002 — Mode mono-utilisateur

**Date** : ante 2026-03-13
**Statut** : Confirme

Un seul utilisateur operationnel : Nicolas.
Pas de gestion de comptes multi-utilisateurs en MVP.

---

## D003 — Canal entree : WhatsApp avec prefixe SIGA:

**Date** : ante 2026-03-13
**Statut** : Confirme

Les messages destines a SIGA doivent commencer par le prefixe `SIGA:`.
Le routeur local detecte ce prefixe et bifurque vers le webhook n8n.

---

## D004 — OCR et Vision via API OpenAI

**Date** : ante 2026-03-13
**Statut** : Confirme

L'analyse des images (OCR plaque signaletique, classification) est confiee a OpenAI Vision.
L'IA produit une proposition, pas une verite finale.
La validation reste sous la responsabilite de l'operateur.

---

## D005 — Stockage images : Google Drive

**Date** : ante 2026-03-13
**Statut** : Confirme

Les images sont uploadees et organisees dans Google Drive.
Arborescence : base / onboarding / annee / mois / ingestion_id

---

## D006 — Base metier : DuckDB local

**Date** : ante 2026-03-13
**Statut** : Confirme (acceptable en MVP)

DuckDB est utilise comme base locale structuree.
Limitations connues : pas un serveur multi-writer.
Acceptable pour un usage mono-utilisateur MVP.

Chemin du fichier : `/files/duckdb/siga_v1.duckdb`

---

## D007 — Front admin : Streamlit

**Date** : ante 2026-03-13
**Statut** : Confirme, a implementer

Interface Streamlit pour :
- dashboard KPI
- inventaire filtrable
- galerie image
- file de validation
- write-back DuckDB

---

## D008 — Remplacement Glide par AppSheet

**Date** : 2026-03-14
**Statut** : Confirme

La solution mobile retenue n'est plus Glide.

Nouvelle cible : AppSheet (Google), mode Prototype gratuit.

Raisons :
- gratuit pour usage prive (< 10 utilisateurs)
- lit directement le Google Sheet
- genere une app mobile native
- pas de changement de pipeline n8n

Architecture : n8n -> Google Sheets -> AppSheet

Voir `docs/appsheet-integration.md` pour le detail.

---

## D009 — Mode de reponse webhook : ACK rapide asynchrone

**Date** : 2026-03-13
**Statut** : Implemente dans le workflow live

Probleme constate : l'utilisateur recevait un faux accuse d'echec si le traitement synchrone durait trop longtemps.

Solution retenue (option B) :
- ACK rapide immediat (`responseMode: onReceived` sur le webhook)
- traitement complet en asynchrone
- message final envoye en fin de run via HTTP OpenClaw `/tools/invoke`

Le noeud `Respond to Webhook` a ete supprime du workflow.

---

## D010 — Envoi message final via HTTP OpenClaw

**Date** : 2026-03-13
**Statut** : Implemente, a tester en reel

Le noeud `Execute Command` n8n n'est pas disponible dans l'instance.

Solution retenue : noeud `HTTP Request` vers `OpenClaw /tools/invoke` avec l'outil `message`.

Point a verifier : auth OpenClaw / variables d'environnement / connectivite n8n -> gateway.

---

## D011 — Conservation scans CNI

**Date** : ante 2026-03-13
**Statut** : Decide, a implementer proceduralement

Duree maximale de conservation : 2 semaines apres retour sans dommage.
Suppression manuelle en cas de litige.

---

## D012 — Emplacements atelier : referentiel modifiable

**Date** : ante 2026-03-13
**Statut** : A implementer

Les emplacements atelier doivent rester un referentiel modifiable.
A creer sous forme de table de reference dans DuckDB ou de liste dans le front Streamlit.
