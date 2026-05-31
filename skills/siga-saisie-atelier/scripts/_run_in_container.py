#!/usr/bin/env python3
"""Exécuté DANS le conteneur siga-dashboard. Lit un blob base64(JSON) en argv[1]
contenant {method, url, body}, lit le token depuis l'env SIGA_API_TOKEN du conteneur,
et effectue la requête. Ne jamais passer le token en argument."""
import os, sys, json, base64, urllib.request, urllib.error

try:
    args = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))
except Exception as e:
    print(json.dumps({"ok": False, "error": "bad_args", "detail": str(e)}))
    sys.exit(0)

method = args["method"].upper()
url = args["url"]
body = args.get("body", "")

token = os.environ.get("SIGA_API_TOKEN")
if not token:
    print(json.dumps({"ok": False, "error": "no_token",
                      "detail": "SIGA_API_TOKEN absent de l'env du conteneur"}))
    sys.exit(0)

headers = {"Authorization": "Bearer " + token}
data = None
if body:
    data = body.encode("utf-8")
    headers["Content-Type"] = "application/json"

req = urllib.request.Request(url, data=data, headers=headers, method=method)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        sys.stdout.write(r.read().decode("utf-8", "replace"))
except urllib.error.HTTPError as e:
    print(json.dumps({"ok": False, "http_status": e.code,
                      "detail": e.read().decode("utf-8", "replace")}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"ok": False, "error": "request_failed", "detail": str(e)}, ensure_ascii=False))
