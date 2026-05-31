# Accès technique à l'API SIGA

## Topologie (vérifiée le 31/05/2026)

- Le domaine public `https://siga.nlhconsulting.fr` est routé par **Traefik** vers
  **Streamlit (port 8501)**, derrière une **basic-auth** (`nicolas:...`). Ce n'est PAS l'API.
- L'**API SIGA (FastAPI)** écoute sur le **port 8001**, **uniquement en local** dans le
  conteneur Docker `siga-dashboard`. Elle n'est pas exposée publiquement.
- Conséquence : un `curl https://siga.nlhconsulting.fr/api/...` depuis l'extérieur renvoie
  **401** (basic-auth Traefik), même avec le bon Bearer. **Ce n'est pas le bon chemin.**

## Seul chemin d'accès depuis Cowork

```
Cowork  ──SSH──▶  VPS (100.104.236.78)  ──docker exec──▶  conteneur siga-dashboard
                                                              │
                                                  http://127.0.0.1:8001/api/...
                                                  Authorization: Bearer $SIGA_API_TOKEN
```

- **Clé SSH** (dans la session Cowork) : `/sessions/<session>/mnt/.ssh/codex_vps_tailscale_ed25519`
  (le chemin de session change à chaque session — le résoudre dynamiquement, voir le script).
- **VPS** : `root@100.104.236.78`
- **Conteneur** : `siga-dashboard`
- **Token** : variable d'environnement `SIGA_API_TOKEN` **du conteneur**. Le script le lit
  à la volée via `printenv` dans le conteneur — il n'est jamais écrit en dur ni stocké.

## Outils disponibles dans le conteneur

- `curl` n'est **pas** installé dans `siga-dashboard`. Utiliser **`python3`** (présent) avec
  `urllib.request`. Le script `scripts/siga_api.sh` le fait déjà.

## Vérifier que tout marche

```bash
scripts/siga_api.sh GET "/api/health"
# attendu : {"status":"ok","db":"reachable"}
```

## Codes d'erreur

| Code | Sens | Action |
|---|---|---|
| 200 | OK | — |
| 401 (depuis le script) | Token interne invalide | vérifier `SIGA_API_TOKEN` dans le conteneur |
| 401 (via domaine public) | basic-auth Traefik | mauvais chemin : passer par le script SSH |
| 409 | Photo déjà liée / doublon | traiter comme un succès, continuer |
| 503 | Drive indisponible | réessayer plus tard ; ne pas créer de fiche sans dossier Drive |

## Dépannage

- **« curl: not found »** : normal dans le conteneur → le script utilise python3, pas curl.
- **SSH timeout** : le VPS Tailscale peut être lent à répondre ; relancer. Vérifier que la clé
  existe (`ls /sessions/*/mnt/.ssh/codex_vps_tailscale_ed25519`).
- **Conteneur introuvable** : `docker ps | grep siga` sur le VPS pour confirmer le nom
  (`siga-dashboard`).
- **Base DuckDB verrouillée** : si une migration/redémarrage est en cours, l'API peut renvoyer
  une erreur de lecture transitoire — réessayer.
