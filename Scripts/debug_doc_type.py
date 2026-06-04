"""verify_doc_types.py — Vérifie la cohérence des doc_type assignés"""
import os
import psycopg2

DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                        user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

# 1. Répartition globale
print("=" * 60)
print("RÉPARTITION PAR DOC_TYPE")
print("=" * 60)
cur.execute("""
    SELECT doc_type, COUNT(DISTINCT source_file) as nb_fichiers, COUNT(*) as nb_chunks
    FROM chunks GROUP BY doc_type ORDER BY nb_fichiers DESC;
""")
for dt, nf, nc in cur.fetchall():
    print(f"  {dt:20s} : {nf:4d} fichiers, {nc:5d} chunks")

# 2. Suspects : fichiers dont le chemin contient "assembl" ou "pv" mais pas typés PV_AG
print("\n" + "=" * 60)
print("SUSPECTS : chemin contient 'assembl' ou '/pv/' mais doc_type ≠ PV_AG")
print("=" * 60)
cur.execute("""
    SELECT DISTINCT doc_type, source_file
    FROM chunks
    WHERE (source_file ILIKE '%%assembl%%' OR source_file ILIKE '%%/pv/%%' OR source_file ILIKE '%%\\pv\\%%')
      AND doc_type != 'PV_AG'
    ORDER BY source_file;
""")
rows = cur.fetchall()
if rows:
    for dt, sf in rows:
        print(f"  ⚠️  [{dt}] {sf}")
else:
    print("  ✅ Aucun suspect")

# 3. Suspects : fichiers dont le chemin contient "reglement" ou "rcp" mais pas typés RCP
print("\n" + "=" * 60)
print("SUSPECTS : chemin contient 'reglement'/'rcp' mais doc_type ≠ RCP")
print("=" * 60)
cur.execute("""
    SELECT DISTINCT doc_type, source_file
    FROM chunks
    WHERE (source_file ILIKE '%%reglement%%' OR source_file ILIKE '%%règlement%%' OR source_file ILIKE '%%/rcp/%%')
      AND doc_type != 'RCP'
    ORDER BY source_file;
""")
rows = cur.fetchall()
if rows:
    for dt, sf in rows:
        print(f"  ⚠️  [{dt}] {sf}")
else:
    print("  ✅ Aucun suspect")

# 4. Échantillon : 5 fichiers par doc_type pour vérification visuelle rapide
print("\n" + "=" * 60)
print("ÉCHANTILLON PAR DOC_TYPE (5 fichiers chacun)")
print("=" * 60)
cur.execute("SELECT DISTINCT doc_type FROM chunks ORDER BY doc_type;")
for (dt,) in cur.fetchall():
    print(f"\n  [{dt}]")
    cur.execute("""
        SELECT DISTINCT source_file FROM chunks
        WHERE doc_type = %s ORDER BY source_file LIMIT 5;
    """, (dt,))
    for (sf,) in cur.fetchall():
        print(f"    → {sf}")

cur.close()
conn.close()