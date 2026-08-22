"""diagnostic_garbay_elhabr.py — Trace GARBAY et EL HABR dans le pipeline de ranking réel"""
import os
import json
import boto3
import psycopg2

# =====================================================
# CONFIGURATION
# =====================================================
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
AWS_REGION = "eu-west-1"
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"

RRF_K = 60
THEME_BOOST = 0.05
DOC_TYPE_BOOST = 0.01
SIMILARITY_THRESHOLD = 0.15

QUERY = "Liste moi tous les sinistres relevés dans cette copropriété"
TRACKED = ["GARBAY", "EL HABR", "CRUET", "ALAMI", "MARROUNI", "LEMEAU", "NAVARRO"]

bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

def get_embedding(text):
    body = json.dumps({"inputText": text[:5000], "dimensions": 1024, "normalize": True})
    response = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL, body=body,
        contentType="application/json", accept="application/json"
    )
    return json.loads(response["body"].read())["embedding"]

def is_tracked(source_file):
    sf = source_file.lower()
    for t in TRACKED:
        if t.lower() in sf:
            return t
    return None

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                        user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()
query_embedding = get_embedding(QUERY)

# =====================================================
# 1. D'abord vérifier les doc_type en base pour GARBAY et EL HABR
# =====================================================
print("=" * 70)
print("ÉTAPE 0 — doc_type en base pour GARBAY et EL HABR")
print("=" * 70)

for name in ["GARBAY", "EL HABR"]:
    cur.execute("""
        SELECT source_file, doc_type, nb_caracteres, LEFT(text, 150)
        FROM chunks WHERE source_file ILIKE %s
    """, (f"%{name}%",))
    rows = cur.fetchall()
    print(f"\n  {name} — {len(rows)} chunk(s) en base :")
    for src, dt, nc, preview in rows:
        short = src.split("\\")[-1] if "\\" in src else src.split("/")[-1]
        print(f"    [{dt:15s}] {nc:5d} chars — {short}")
        print(f"    Aperçu: {preview[:100]}...")

# =====================================================
# 2. Simuler le ranking COMPLET (top 300, sans diversité, sans rerank)
# =====================================================
print("\n" + "=" * 70)
print("ÉTAPE 1 — Ranking RRF BRUT (top 300, sans diversité)")
print("=" * 70)

cur.execute(f"""
    WITH base AS (
        SELECT chunk_id, source_file, nom_fichier, doc_type, text,
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
    SELECT source_file, nom_fichier, doc_type,
           vec_similarity, bm25_score, vec_rank, bm25_rank,
           theme_boost, doc_type_boost, rrf_score
    FROM with_rrf
    ORDER BY rrf_score DESC
    LIMIT 300
""", [str(query_embedding), ["assurance_sinistres"], QUERY])

brut_results = cur.fetchall()

# Trouver les sinistres trackés
print(f"\nTotal chunks en base au-dessus du seuil dans le top 300\n")
for name in TRACKED:
    positions = []
    for i, r in enumerate(brut_results):
        if name.lower() in r[0].lower():
            positions.append((i+1, r))
    
    if positions:
        for rang, r in positions:
            short = r[1]
            dt_boost = f"+doctype" if r[8] > 0 else "       "
            th_boost = f"+theme" if r[7] > 0 else "      "
            print(f"  Rang {rang:3d} | {name:12s} | vec={r[3]:.3f} (r{r[5]:4d}) | bm25={r[4]:.4f} (r{r[6]:4d}) | "
                  f"{dt_boost} {th_boost} | rrf={r[9]:.5f} | [{r[2]:15s}] {short}")
    else:
        print(f"  ❌ {name:12s} — ABSENT du top 300")

# Montrer aussi les rangs 45-55 (autour du cutoff 50)
print(f"\n--- Rangs 45-55 (zone de cutoff à 50 chunks) ---\n")
for i in range(44, min(55, len(brut_results))):
    r = brut_results[i]
    tracked = is_tracked(r[0])
    flag = f" ← {tracked}" if tracked else ""
    short = r[1][:50]
    print(f"  Rang {i+1:3d} | vec={r[3]:.3f} bm25={r[4]:.4f} rrf={r[9]:.5f} | [{r[2]:15s}] {short}{flag}")

# =====================================================
# 3. Avec diversité 2/source (stratégie inventaire)
# =====================================================
print(f"\n{'=' * 70}")
print("ÉTAPE 2 — Avec diversité 2/source (stratégie inventaire)")
print("=" * 70)

cur.execute(f"""
    WITH base AS (
        SELECT chunk_id, source_file, nom_fichier, doc_type, text,
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
               (1.0 / ({RRF_K} + vec_rank) + 1.0 / ({RRF_K} + bm25_rank)
                + theme_boost + doc_type_boost) as rrf_score
        FROM with_ranks
    ),
    diversified AS (
        SELECT *,
               row_number() OVER (PARTITION BY source_file ORDER BY rrf_score DESC) as rank_in_source
        FROM with_rrf
    )
    SELECT source_file, nom_fichier, doc_type,
           vec_similarity, bm25_score, vec_rank, bm25_rank,
           theme_boost, doc_type_boost, rrf_score
    FROM diversified
    WHERE rank_in_source <= 2 AND vec_similarity >= {SIMILARITY_THRESHOLD}
    ORDER BY rrf_score DESC
    LIMIT 120
""", [str(query_embedding), ["assurance_sinistres"], QUERY])

div_results = cur.fetchall()
unique_sources = len(set(r[0] for r in div_results))

print(f"\n{len(div_results)} chunks de {unique_sources} sources distinctes\n")

for name in TRACKED:
    positions = [(i+1, r) for i, r in enumerate(div_results) if name.lower() in r[0].lower()]
    if positions:
        for rang, r in positions:
            in_top50 = "✅ dans top 50" if rang <= 50 else "⚠️  HORS top 50"
            print(f"  Rang {rang:3d} {in_top50} | {name:12s} | rrf={r[9]:.5f} | [{r[2]:15s}] {r[1]}")
    else:
        print(f"  ❌ {name:12s} — ABSENT après diversité")

# Montrer les 50 premiers avec leurs sources pour comprendre qui prend les places
print(f"\n--- Les 50 chunks qui seraient envoyés à Claude ---\n")
source_count = {}
for i, r in enumerate(div_results[:50]):
    short = r[1][:45]
    tracked = is_tracked(r[0])
    flag = f" ← {tracked}" if tracked else ""
    print(f"  {i+1:2d}. [{r[2]:15s}] rrf={r[9]:.5f} | {short}{flag}")
    
    src_key = r[0].split("\\")[-2] if "\\" in r[0] else r[0]  # dossier parent
    source_count[src_key] = source_count.get(src_key, 0) + 1

print(f"\n--- Répartition par dossier parent dans le top 50 ---\n")
for src, count in sorted(source_count.items(), key=lambda x: -x[1])[:15]:
    short = src.split("\\")[-1] if "\\" in src else src
    has_tracked = any(t.lower() in src.lower() for t in TRACKED)
    flag = " ← SINISTRE TRACKÉ" if has_tracked else ""
    print(f"  {count:2d} chunks — {short[:60]}{flag}")

# =====================================================
# 4. Vérifier si EL HABR existe en base
# =====================================================
print(f"\n{'=' * 70}")
print("ÉTAPE 3 — Recherche large EL HABR")
print("=" * 70)

for search in ["EL HABR", "ELHABR", "EL_HABR", "HABR"]:
    cur.execute("SELECT COUNT(*), array_agg(DISTINCT doc_type) FROM chunks WHERE source_file ILIKE %s", (f"%{search}%",))
    count, types = cur.fetchone()
    if count > 0:
        print(f"  ✅ '{search}' → {count} chunks, types: {types}")
    else:
        print(f"  ❌ '{search}' → 0 chunks")

# Chercher aussi dans le texte
cur.execute("""
    SELECT DISTINCT source_file, doc_type 
    FROM chunks 
    WHERE text ILIKE '%%habr%%' OR text ILIKE '%%el habr%%'
    LIMIT 10
""")
text_hits = cur.fetchall()
if text_hits:
    print(f"\n  'HABR' trouvé dans le TEXTE de {len(text_hits)} fichier(s) :")
    for src, dt in text_hits:
        short = src.split("\\")[-1] if "\\" in src else src.split("/")[-1]
        print(f"    [{dt}] {short}")
else:
    print(f"\n  ❌ 'HABR' introuvable dans le texte des chunks")

cur.close()
conn.close()