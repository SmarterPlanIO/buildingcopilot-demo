import os
import json, boto3, psycopg2

DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

bedrock = boto3.client("bedrock-runtime", region_name="eu-west-1")
conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

# 3 requêtes test couvrant différents aspects du RCP
queries = [
    "Que dit le règlement de copropriété du 2-6 bis Henri Tariel ?",
    "Quelles sont les parties communes de la copropriété Henri Tariel ?",
    "Quelle est la répartition des tantièmes de charges générales ?",
]

for query in queries:
    # Embedding de la question
    body = json.dumps({"inputText": query, "dimensions": 1024, "normalize": True})
    resp = bedrock.invoke_model(modelId="amazon.titan-embed-text-v2:0", body=body,
                                contentType="application/json", accept="application/json")
    qvec = json.loads(resp["body"].read())["embedding"]

    print(f"\n{'='*70}")
    print(f"  QUESTION : {query}")
    print(f"{'='*70}")

    # Top 10 résultats GLOBAL (tous documents confondus)
    cur.execute("""
        SELECT chunk_id, source_file, doc_type, themes,
               1 - (embedding <=> %s::vector) as similarity,
               LEFT(text, 120) as apercu
        FROM chunks
        ORDER BY embedding <=> %s::vector
        LIMIT 10
    """, (str(qvec), str(qvec)))

    for i, r in enumerate(cur.fetchall()):
        is_rcp = "🎯" if "RCP 2000" in (r[1] or "") else "  "
        print(f"  {is_rcp} #{i+1} sim={r[4]:.4f} | {r[2]:10s} | {r[1][-40:]}")
        print(f"       Thèmes: {r[3]}")
        print(f"       {r[5]}...")

    # Top 5 résultats FILTRÉS sur le RCP uniquement
    cur.execute("""
        SELECT chunk_id, 
               1 - (embedding <=> %s::vector) as similarity,
               LEFT(text, 120) as apercu
        FROM chunks
        WHERE source_file ILIKE '%%RCP 2000%%'
        ORDER BY embedding <=> %s::vector
        LIMIT 5
    """, (str(qvec), str(qvec)))

    print(f"\n  --- Top 5 dans le RCP uniquement ---")
    for i, r in enumerate(cur.fetchall()):
        print(f"  #{i+1} sim={r[1]:.4f} | {r[0]} | {r[2]}...")

cur.close()
conn.close()