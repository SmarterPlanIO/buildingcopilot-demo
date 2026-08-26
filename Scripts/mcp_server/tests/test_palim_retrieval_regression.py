"""
test_palim_retrieval_regression.py — Régression retrieval (porte bloquante, PLAN_ACTION §6).

DB-gated : SKIP si DB_HOST/DB_PASSWORD absents ou si psycopg2/boto3 manquent.
Quand actif :
  1. Vérifie les invariants structurels de hybrid_search (mono-copro).
  2. Imprime le top-5 (source_file) pour comparaison manuelle avec l'app Streamlit
     sur la même requête (le side-by-side exact nécessite de lancer Streamlit).

Usage :
  PYTHONIOENCODING=utf-8 DB_HOST=... DB_USER=mcp_ncg_reader DB_PASSWORD=... \
    AWS_REGION_EMBED=eu-west-1 python tests/test_palim_retrieval_regression.py [CODE_NCG] ["requête"]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CODE = sys.argv[1] if len(sys.argv) > 1 else "5390"
QUERY = sys.argv[2] if len(sys.argv) > 2 else "travaux de ravalement de façade"


def _skip(msg):
    print(f"SKIP regression: {msg}")
    sys.exit(0)


_has_pwd = os.environ.get("DB_PASSWORD") or os.environ.get("DB_SECRET_ARN")
if not _has_pwd:
    _skip("ni DB_PASSWORD ni DB_SECRET_ARN fourni")
# host peut venir du secret ; sinon requis en env
if not os.environ.get("DB_HOST") and not os.environ.get("DB_SECRET_ARN"):
    _skip("DB_HOST requis (ou via secret)")

try:
    import boto3  # noqa
    import psycopg2  # noqa
    from PALIM_db import get_conn
    from PALIM_retrieval import hybrid_search
    import PALIM_config as cfg
except Exception as e:
    _skip(f"dépendance manquante ({type(e).__name__}: {e})")

import boto3
from botocore.config import Config

bedrock = boto3.client("bedrock-runtime", region_name=cfg.AWS_REGION_EMBED,
                       config=Config(read_timeout=60, retries={"max_attempts": 3}))

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


results = hybrid_search(get_conn(), bedrock, QUERY, copro_codes=[CODE], max_chunks=12)

check("retourne des résultats", len(results) > 0)
check("tous de la copro demandée", all(r["code_ncg"] == CODE for r in results))
check("scores décroissants", all(results[i]["score"] >= results[i + 1]["score"] for i in range(len(results) - 1)))
check("pas de BORDEREAU_AR par défaut", all(r["doc_type"] != "BORDEREAU_AR" for r in results))
check("champs obligatoires présents",
      all({"chunk_id", "code_ncg", "source_file", "doc_type"} <= set(r) for r in results))

print(f"\n--- TOP 5 (copro {CODE} / '{QUERY}') — à comparer avec Streamlit ---")
for r in results[:5]:
    print(f"  {r['score']:.4f}  {r['doc_type']:12} {r['source_file']}")

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("REGRESSION INVARIANTS PASSED")
