"""diagnostic_ranking_sinistres.py — Simule le retrieval pour 'liste tous les sinistres' 
et trace les 5 dossiers manquants à chaque étape du pipeline"""
import json
import boto3
import psycopg2

# =====================================================
# CONFIGURATION (identique à 07_query_rag_ui.py)
# =====================================================
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = "SmarterRAG99!"
AWS_REGION = "eu-west-1"
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"

RRF_K = 60
THEME_BOOST = 0.05
DOC_TYPE_BOOST = 0.01
SIMILARITY_THRESHOLD = 0.15

QUERY = "Liste moi tous les sinistres relevés dans cette copropriété"
MISSING_DOSSIERS = ["DDE ALAMI", "NAVARRO", "DDE CRUET", "DDE GARBAY", "DDE MARROUNI", "DDE LEMEAU"]

# =====================================================
# Embedding
# =====================================================
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

def get_embedding(text):
    body = json.dumps({"inputText": text[:5000], "dimensions": 1024, "normalize": True})
    response = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL, body=body,
        contentType="application/json", accept="application/json"
    )
    return json.loads(response["body"].read())["embedding"]

def is_missing_dossier(source_file):
    """Vérifie si un chunk appartient à l'un des 5 dossiers manquants."""
    sf = source_file.lower()
    for d in MISSING_DOSSIERS:
        if d.lower() in sf:
            return d
    return None

# =====================================================
# ÉTAPE 1 : Requête BRUTE (sans diversité, sans rerank) — top 200
# =====================================================
print("=" * 70)
print(f"REQUÊTE : \"{QUERY}\"")
print("=" * 70)

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                        user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

query_embedding = get_embedding(QUERY)
print("✅ Embedding généré\n")

# RRF brut sans diversité ni rerank
cur.execute(f"""
    WITH base AS (
        SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
               themes, text,
               1 - (embedding <=> %s::vector) as vec_similarity,
               CASE WHEN themes && %s::text[] THEN {THEME_BOOST} ELSE 0 END as theme_boost,
               ts_rank(text_search, plainto_tsquery('french', %s), 32) as bm25_score,
               CASE WHEN doc_type = 'SINISTRE' THEN {DOC_TYPE_BOOST} ELSE 0 END as doc_type_boost
        FROM chunks
    ),
    with_ranks AS (
        SELECT *,
               row_number() OVER (ORDER BY vec_similarity DESC) as vec_rank,
               row_number() OVER (ORDER BY bm25_score DESC) as bm25_rank
        FROM base
    ),
    with_rrf AS (
        SELECT *,
               (1.0 / ({RRF_K} + vec_rank)
                + 1.0 / ({RRF_K} + bm25_rank)
                + theme_boost
                + doc_type_boost) as rrf_score
        FROM with_ranks
    )
    SELECT chunk_id, source_file, nom_fichier, doc_type,
           vec_similarity, bm25_score, vec_rank, bm25_rank, rrf_score
    FROM with_rrf
    WHERE vec_similarity >= {SIMILARITY_THRESHOLD}
    ORDER BY rrf_score DESC
    LIMIT 200
""", [str(query_embedding), ["assurance_sinistres"], QUERY])

brut_results = cur.fetchall()

print("=" * 70)
print(f"ÉTAPE 1 — Top 200 RRF brut (sans diversité)")
print("=" * 70)
print(f"\nTotal : {brut_results[0][8]:.5f} (meilleur) → {brut_results[-1][8]:.5f} (200e)\n")

# Trouver les 5 dossiers manquants
print("Position des 5 dossiers manquants dans le top 200 :\n")
for dossier in MISSING_DOSSIERS:
    positions = []
    for i, r in enumerate(brut_results):
        if dossier.lower() in r[1].lower():
            positions.append({
                "rang": i + 1,
                "fichier": r[2],
                "doc_type": r[3],
                "vec_sim": r[4],
                "bm25": r[5],
                "vec_rank": r[6],
                "bm25_rank": r[7],
                "rrf": r[8],
            })
    
    if positions:
        print(f"  ✅ {dossier} — {len(positions)} chunk(s) dans le top 200")
        for p in positions:
            print(f"     Rang {p['rang']:3d} | vec={p['vec_sim']:.3f} (r{p['vec_rank']}) | "
                  f"bm25={p['bm25']:.4f} (r{p['bm25_rank']}) | rrf={p['rrf']:.5f} | "
                  f"[{p['doc_type']}] {p['fichier']}")
    else:
        print(f"  ❌ {dossier} — ABSENT du top 200 !")
        # Chercher sa position réelle
        cur.execute(f"""
            WITH base AS (
                SELECT source_file, nom_fichier, doc_type,
                       1 - (embedding <=> %s::vector) as vec_similarity,
                       ts_rank(text_search, plainto_tsquery('french', %s), 32) as bm25_score
                FROM chunks
                WHERE source_file ILIKE %s
            )
            SELECT source_file, nom_fichier, doc_type, vec_similarity, bm25_score
            FROM base ORDER BY vec_similarity DESC LIMIT 3
        """, [str(query_embedding), QUERY, f"%{dossier}%"])
        deep = cur.fetchall()
        if deep:
            for d in deep:
                print(f"     Score réel : vec={d[3]:.3f} bm25={d[4]:.4f} [{d[2]}] {d[1]}")
                if d[3] < SIMILARITY_THRESHOLD:
                    print(f"     ⚠️  vec_similarity {d[3]:.3f} < seuil {SIMILARITY_THRESHOLD} → FILTRÉ !")
        else:
            print(f"     ⚠️  Aucun chunk trouvé pour ce dossier en base !")

# =====================================================
# ÉTAPE 2 : Avec diversité par source (3/source et 2/source)
# =====================================================
for max_per_source in [3, 2]:
    cur.execute(f"""
        WITH base AS (
            SELECT chunk_id, source_file, nom_fichier, doc_type,
                   1 - (embedding <=> %s::vector) as vec_similarity,
                   ts_rank(text_search, plainto_tsquery('french', %s), 32) as bm25_score,
                   CASE WHEN doc_type = 'SINISTRE' THEN {DOC_TYPE_BOOST} ELSE 0 END as doc_type_boost,
                   CASE WHEN themes && %s::text[] THEN {THEME_BOOST} ELSE 0 END as theme_boost
            FROM chunks
        ),
        with_ranks AS (
            SELECT *,
                   row_number() OVER (ORDER BY vec_similarity DESC) as vec_rank,
                   row_number() OVER (ORDER BY bm25_score DESC) as bm25_rank
            FROM base
        ),
        with_rrf AS (
            SELECT *,
                   (1.0 / ({RRF_K} + vec_rank) + 1.0 / ({RRF_K} + bm25_rank)
                    + theme_boost + doc_type_boost) as rrf_score
            FROM with_ranks
        ),
        diversified AS (
            SELECT *,
                   row_number() OVER (PARTITION BY source_file ORDER BY rrf_score DESC) as rank_in_source
            FROM with_rrf
        )
        SELECT source_file, nom_fichier, doc_type, vec_similarity, bm25_score, rrf_score
        FROM diversified
        WHERE rank_in_source <= {max_per_source}
          AND vec_similarity >= {SIMILARITY_THRESHOLD}
        ORDER BY rrf_score DESC
        LIMIT 120
    """, [str(query_embedding), QUERY, ["assurance_sinistres"]])

    div_results = cur.fetchall()
    unique_sources = len(set(r[0] for r in div_results))

    print(f"\n{'=' * 70}")
    print(f"ÉTAPE 2 — Avec diversité ({max_per_source}/source) → {len(div_results)} chunks, {unique_sources} sources")
    print(f"{'=' * 70}")

    for dossier in MISSING_DOSSIERS:
        positions = [(i+1, r) for i, r in enumerate(div_results) if dossier.lower() in r[0].lower()]
        if positions:
            for rang, r in positions:
                print(f"  ✅ {dossier:30s} rang {rang:3d} | vec={r[3]:.3f} bm25={r[4]:.4f} rrf={r[5]:.5f} | [{r[2]}] {r[1]}")
        else:
            print(f"  ❌ {dossier:30s} ABSENT")

    # Montrer aussi les sources qui monopolisent
    source_counts = {}
    for r in div_results[:50]:
        src = r[0]
        source_counts[src] = source_counts.get(src, 0) + 1
    
    top_sources = sorted(source_counts.items(), key=lambda x: -x[1])[:5]
    print(f"\n  Top 5 sources les plus représentées dans le top 50 :")
    for src, count in top_sources:
        short = src.split("\\")[-1] if "\\" in src else src.split("/")[-1]
        is_missing = any(d.lower() in src.lower() for d in MISSING_DOSSIERS)
        flag = " ← SINISTRE MANQUANT" if is_missing else ""
        print(f"    {count} chunks — {short}{flag}")

cur.close()
conn.close()

print(f"\n{'=' * 70}")
print("CONCLUSION")
print(f"{'=' * 70}")
print("""
Si les 5 dossiers sont ABSENTS du top 200 brut :
  → Le problème est le scoring (vec_similarity et/ou bm25 trop faibles)
  → Les chunks de ces sinistres ne parlent peut-être pas de "sinistre" explicitement

Si les 5 dossiers sont PRÉSENTS dans le top 200 mais ABSENTS après diversité :
  → Le problème est que d'autres sources plus scorées prennent toutes les places
  → Réduire MAX_CHUNKS_PER_SOURCE ou augmenter RERANK_CANDIDATES

Si les 5 sont PRÉSENTS après diversité mais pas dans la réponse Claude :
  → Le problème est dans la synthèse LLM, pas dans le retrieval
""")