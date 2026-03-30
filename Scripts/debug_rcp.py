import psycopg2

DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = "SmarterRAG99!"

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

# Chercher tous les chunks liés au RCP
cur.execute("""
    SELECT chunk_id, doc_type, chunk_index, total_chunks, nb_caracteres,
           themes, LEFT(text, 100) as apercu,
           embedding IS NOT NULL as has_embedding
    FROM chunks 
    WHERE source_file ILIKE '%RCP 2000%' OR nom_fichier ILIKE '%RCP 2000%'
    ORDER BY chunk_index
""")

rows = cur.fetchall()
print(f"\n{'='*60}")
print(f"  Chunks RCP 2000 dans PostgreSQL : {len(rows)}")
print(f"{'='*60}")

for r in rows:
    print(f"\n  [{r[2]+1}/{r[3]}] {r[0]} | type={r[1]} | {r[4]} chars | embedding={'✅' if r[7] else '❌'}")
    print(f"  Thèmes: {r[5]}")
    print(f"  Aperçu: {r[6]}...")

if not rows:
    print("\n  ❌ AUCUN CHUNK TROUVÉ — le RCP n'est pas dans la base")

    # Vérifier ce qui est dans la base
    cur.execute("SELECT DISTINCT source_file FROM chunks WHERE source_file ILIKE '%rcp%' OR doc_type = 'RCP'")
    rcp_files = cur.fetchall()
    print(f"\n  Fichiers RCP existants dans la base :")
    for f in rcp_files:
        print(f"    - {f[0]}")

cur.close()
conn.close()