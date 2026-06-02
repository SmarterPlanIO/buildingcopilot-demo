"""Rotation du mot de passe RDS ragadmin.

Lit l'ancien et le nouveau mot de passe depuis l'environnement (aucun secret
dans ce fichier), effectue l'ALTER ROLE, puis verifie que le nouveau fonctionne
et que l'ancien est bien rejete.

Usage (PowerShell) :
    $env:DB_PASSWORD = "ancien_mdp"
    $env:NEW_DB_PASSWORD = "nouveau_mdp"
    python rotate_ragadmin.py

Apres succes, mettre a jour les consommateurs : secrets.toml local,
Streamlit Cloud (Settings -> Secrets, db.password), env du pipeline.
"""
import os
import sys
import psycopg2
from psycopg2 import sql

HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
PORT = 5432
DBNAME = "postgres"
USER = "ragadmin"

old = os.environ.get("DB_PASSWORD")
new = os.environ.get("NEW_DB_PASSWORD")

if not old or not new:
    sys.exit("Definir DB_PASSWORD (ancien) ET NEW_DB_PASSWORD (nouveau) dans l'environnement.")
if old == new:
    sys.exit("Ancien et nouveau mot de passe identiques : rien a faire.")


def connect(pw):
    return psycopg2.connect(host=HOST, port=PORT, dbname=DBNAME, user=USER, password=pw)


# 1. Rotation (sql.Literal -> echappement correct du litteral, pas d'injection)
conn = connect(old)
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute(sql.SQL("ALTER ROLE ragadmin WITH PASSWORD {}").format(sql.Literal(new)))
conn.close()
print("[1/3] ALTER ROLE execute.")

# 2. Le nouveau mot de passe doit etre accepte
try:
    connect(new).close()
    print("[2/3] Nouveau mot de passe : OK.")
except Exception as e:
    sys.exit(f"[2/3] ECHEC connexion avec le nouveau mot de passe : {e}")

# 3. L'ancien mot de passe doit etre rejete
try:
    connect(old).close()
    print("[3/3] ATTENTION : l'ancien mot de passe est ENCORE accepte (probleme).")
except Exception:
    print("[3/3] Ancien mot de passe : rejete (comportement attendu).")

print("\nRotation terminee. Mets a jour maintenant :")
print("  - secrets.toml local (section [db], password)")
print("  - Streamlit Cloud : Settings -> Secrets -> db.password")
print("  - env du pipeline (DB_PASSWORD) pour 06b / 08 / load_dossiers_only")
