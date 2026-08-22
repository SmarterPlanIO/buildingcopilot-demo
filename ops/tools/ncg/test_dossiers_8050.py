"""Smoke test du fix search_dossiers (énumération scopée + n_total).

Rejoue les requêtes qui renvoyaient 0 dans les traces Langfuse du 04/06 sur 8050.
Lancer : DB_PASSWORD=... PYTHONIOENCODING=utf-8 python mcp_server/test_dossiers_8050.py

Critères de succès :
  - les 2 requêtes multi-mots renvoient maintenant des dossiers (>0)
  - n_total reflète le vrai volume (~130), pas 5
"""
import os
import sys

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PALIM_dossiers import search_dossiers

DB_PASSWORD = os.environ.get("DB_PASSWORD")
if not DB_PASSWORD:
    raise SystemExit("DB_PASSWORD manquant. DB_PASSWORD=... python test_dossiers_8050.py")

conn = psycopg2.connect(
    host="sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com",
    port=5432, dbname="postgres",
    user=os.environ.get("DB_USER", "mcp_ncg_reader"), password=DB_PASSWORD,
)

CASES = [
    "sinistre travaux contentieux dossier en cours",   # trace 17:34:42 -> 0 avant
    "sinistre degat des eaux travaux",                 # trace 17:44:47 -> 0 avant
    "sinistre",                                        # mon appel diag -> 5 (LIMIT) avant
    "",                                                # enumeration pure (pas de query)
]

ok = True
for q in CASES:
    results, n_total = search_dossiers(conn, q, copro_codes=["8050"], max_results=50)
    flag = "OK " if results else "KO "
    if not results:
        ok = False
    print(f"[{flag}] query={q!r:55} -> n_returned={len(results):3d}  n_total={n_total}")
    if results:
        print(f"        ex: {results[0]['type']} | {results[0]['lese']} | {results[0]['statut']}")

conn.close()
print("\n=> FIX VALIDE" if ok else "\n=> ECHEC : au moins une requete renvoie 0")
sys.exit(0 if ok else 1)
