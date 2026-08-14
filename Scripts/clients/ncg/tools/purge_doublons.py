"""
Script utilitaire — Purge des doublons et diagnostic DB
Lance : python purge_doublons.py
"""
import os
import psycopg2

# =====================================================
# CONFIGURATION
# =====================================================
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                        user=DB_USER, password=DB_PASSWORD)
conn.autocommit = True
cur = conn.cursor()

# =====================================================
# 1. Diagnostic : détecter les fichiers avec doublons
# =====================================================
print("=" * 60)
print("  DIAGNOSTIC DES DOUBLONS")
print("=" * 60)

cur.execute("""
    SELECT source_file, COUNT(DISTINCT total_chunks) as nb_versions, 
           COUNT(*) as nb_chunks,
           array_agg(DISTINCT total_chunks) as versions
    FROM chunks
    GROUP BY source_file
    HAVING COUNT(DISTINCT total_chunks) > 1
    ORDER BY COUNT(*) DESC
""")

doublons = cur.fetchall()
if doublons:
    print(f"\n⚠️  {len(doublons)} fichiers avec plusieurs versions de chunks :")
    for source, nb_ver, nb_chunks, versions in doublons:
        print(f"   {source[-60:]}")
        print(f"     → {nb_ver} versions (total_chunks={versions}), {nb_chunks} chunks au total")
else:
    print("\n✅ Aucun doublon détecté")

# =====================================================
# 2. Purge : supprimer les chunks du RCP pour réindexation
# =====================================================
print(f"\n{'=' * 60}")
print("  PURGE DES CHUNKS RCP")
print("=" * 60)

# Compter avant
cur.execute("SELECT COUNT(*) FROM chunks WHERE source_file ILIKE '%RCP 2000%'")
count_before = cur.fetchone()[0]
print(f"\n  Chunks RCP actuels : {count_before}")

if count_before > 0:
    cur.execute("DELETE FROM chunks WHERE source_file ILIKE '%RCP 2000%'")
    print(f"  ✅ Supprimés : {cur.rowcount} chunks")
    print(f"  → Relancer : python 03_chunking.py → 04 → 05 → 06b")
    print(f"  ⚠️  Supprimer aussi chunks_avec_embeddings.jsonl avant de relancer 05")
else:
    print("  Rien à purger.")

# =====================================================
# 3. Stats générales de la base
# =====================================================
print(f"\n{'=' * 60}")
print("  ÉTAT DE LA BASE")
print("=" * 60)

cur.execute("SELECT COUNT(*) FROM chunks")
total = cur.fetchone()[0]

cur.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NULL")
no_emb = cur.fetchone()[0]

cur.execute("""
    SELECT doc_type, COUNT(*), 
           ROUND(AVG(nb_caracteres)) as avg_chars,
           MIN(nb_caracteres) as min_chars,
           MAX(nb_caracteres) as max_chars
    FROM chunks 
    GROUP BY doc_type 
    ORDER BY COUNT(*) DESC
""")
types = cur.fetchall()

print(f"\n  Total chunks     : {total}")
print(f"  Sans embedding   : {no_emb}")
print(f"\n  Par type :")
for dt, cnt, avg, mn, mx in types:
    print(f"    {dt:15s} : {cnt:5d} chunks | moy={avg:.0f} | min={mn} | max={mx} chars")

# Chunks surdimensionnés (>5000 chars = risque token overflow)
cur.execute("SELECT COUNT(*) FROM chunks WHERE nb_caracteres > 5000")
oversized = cur.fetchone()[0]
if oversized:
    print(f"\n  ⚠️  {oversized} chunks > 5000 chars (risque token overflow Titan V2)")
else:
    print(f"\n  ✅ Aucun chunk surdimensionné")

cur.close()
conn.close()
print(f"\n{'=' * 60}")
