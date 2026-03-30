"""
Diagnostic : pourquoi les résolutions AG 2016-2018 ne remontent pas ?
Utilise la table documents (doc_type_corrige, sous_type, annee) pour filtrer,
comme le fait le vrai pipeline de pré-filtrage dans search_chunks().

Lance : python diag_resolutions_ag.py
        python diag_resolutions_ag.py "liste toutes les résolutions votées en AG de 2010 à 2018"
"""
import sys
import json
import os
import boto3
import psycopg2
from botocore.config import Config

# ── Config ──
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = "SmarterRAG99!"
AWS_REGION = "eu-west-1"
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"

RRF_K = 60
MIN_CHUNK_CHARS = 500
SIMILARITY_THRESHOLD = 0.15
RERANK_CANDIDATES = 120
MAX_CHUNKS_LLM_BROAD = 80
COPRO_FILTER = "SOURCE_ARCHIVES"

DEFAULT_QUERY = "liste toutes les résolutions votées en AG de 2010 à 2018"
query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY

print("=" * 95)
print("  DIAGNOSTIC RÉSOLUTIONS AG — VIA TABLE DOCUMENTS")
print(f"  Requête : {query}")
print("=" * 95)

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                        user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

# =====================================================
# 0. Inventaire doc_type / sous_type pour PV_AG dans documents
# =====================================================
print("\n━━━ 0. SOUS-TYPES DISPONIBLES POUR PV_AG (table documents, TARIEL) ━━━")
cur.execute("""
    SELECT COALESCE(doc_type_corrige, doc_type) as dt_eff, sous_type, COUNT(*) as n
    FROM documents
    WHERE source_file ILIKE %s
      AND COALESCE(doc_type_corrige, doc_type) = 'PV_AG'
    GROUP BY dt_eff, sous_type
    ORDER BY n DESC
""", [f"%{COPRO_FILTER}%TARIEL%"])
rows = cur.fetchall()
print(f"  {'doc_type_eff':>15}  {'sous_type':>25}  {'count':>5}")
print(f"  {'─'*15}  {'─'*25}  {'─'*5}")
for r in rows:
    print(f"  {r[0]:>15}  {str(r[1]):>25}  {r[2]:>5}")

# =====================================================
# 1. Fichiers PV_AG par année via table documents
# =====================================================
print("\n━━━ 1. FICHIERS PV_AG PAR ANNÉE (table documents, 2010-2018) ━━━")
cur.execute("""
    SELECT d.source_file, d.doc_type, d.doc_type_corrige, d.annee, d.sous_type,
           d.dossier_lie, d.est_reference, COALESCE(d.groupe_doc, d.source_file) as grp,
           (SELECT COUNT(*) FROM chunks c WHERE c.source_file = d.source_file AND c.nb_caracteres >= 500) as n_chunks
    FROM documents d
    WHERE source_file ILIKE %s
      AND (COALESCE(doc_type_corrige, doc_type) = 'PV_AG' OR dossier_lie = 'PV_AG')
      AND annee BETWEEN 2010 AND 2018
    ORDER BY annee, source_file
""", [f"%{COPRO_FILTER}%TARIEL%"])

pv_docs = cur.fetchall()
print(f"\n  {len(pv_docs)} documents PV_AG trouvés (2010-2018) :\n")

by_year = {}
for r in pv_docs:
    sf, dt, dtc, annee, st, dl, ref, grp, nchunks = r
    by_year.setdefault(annee, []).append(r)
    nom = os.path.basename(sf)[:60]
    dt_eff = dtc or dt
    ref_tag = " ⭐ref" if ref else ""
    print(f"  [{annee}] {dt_eff:>8}  st={str(st):>20}  {nchunks:>3} chunks  {nom}{ref_tag}")

pv_files = [r[0] for r in pv_docs]
pv_groups = set(r[7] for r in pv_docs)

# Années manquantes
for y in range(2010, 2019):
    if y not in by_year:
        print(f"  [{y}] ⚠️  AUCUN DOCUMENT PV_AG")

# =====================================================
# 2. Simulation pré-filtrage
# =====================================================
print(f"\n━━━ 2. SIMULATION PRÉ-FILTRAGE ━━━")
n_files = len(pv_files)
n_groups = len(pv_groups)
prefilter_active = 0 < n_files <= 50
max_chunks = MAX_CHUNKS_LLM_BROAD
dynamic_cap = max(2, min(15, max_chunks // max(n_groups, 1)))

print(f"  Fichiers pré-filtrés   : {n_files}")
print(f"  Groupes uniques        : {n_groups}")
print(f"  Pré-filtrage activé    : {'✅ OUI' if prefilter_active else '❌ NON (' + str(n_files) + ' > 50)'}")
print(f"  Cap dynamique          : {dynamic_cap} chunks/source (max_chunks={max_chunks} / {n_groups} groupes)")
print(f"  SQL cap (prefilter)    : 30")

if not prefilter_active:
    print(f"\n  ⚠️  PROBLÈME : {n_files} fichiers > 50 → pré-filtrage DÉSACTIVÉ")
    print(f"  → Le pipeline retombe sur cap=3 (MAX_CHUNKS_PER_SOURCE par défaut)")
    print(f"  → Un PV avec 36 chunks ne peut en faire passer que 3 !")

# =====================================================
# 3. Embedding + scoring RRF
# =====================================================
print(f"\n━━━ 3. SCORING RRF — CHUNKS PV_AG 2010-2018 ━━━")
print("  ⏳ Embedding de la requête...")
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION,
                       config=Config(read_timeout=300, retries={"max_attempts": 3}))
body = json.dumps({"inputText": query[:5000], "dimensions": 1024, "normalize": True})
resp = bedrock.invoke_model(modelId=EMBEDDING_MODEL, body=body,
                            contentType="application/json", accept="application/json")
query_embedding = json.loads(resp["body"].read())["embedding"]

# Scorer uniquement les chunks des fichiers PV_AG identifiés
if pv_files:
    placeholders = ",".join(["%s"] * len(pv_files))
    cur.execute(f"""
        WITH base AS (
            SELECT chunk_id, source_file, nom_fichier, doc_type, text,
                   chunk_index, nb_caracteres,
                   1 - (embedding <=> %s::vector) as vec_similarity,
                   ts_rank(text_search, plainto_tsquery('french', %s), 32) as bm25_score
            FROM chunks
            WHERE nb_caracteres >= {MIN_CHUNK_CHARS}
              AND source_file IN ({placeholders})
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
                    + 1.0 / ({RRF_K} + bm25_rank)) as rrf_score
            FROM with_ranks
        ),
        diversified AS (
            SELECT *,
                   row_number() OVER (
                       PARTITION BY source_file ORDER BY rrf_score DESC
                   ) as rank_in_source,
                   row_number() OVER (ORDER BY rrf_score DESC) as global_rank
            FROM with_rrf
        )
        SELECT global_rank, rank_in_source, chunk_id, source_file, nom_fichier,
               vec_similarity, bm25_score, rrf_score, chunk_index, nb_caracteres,
               LEFT(text, 200)
        FROM diversified
        ORDER BY rrf_score DESC
    """, [str(query_embedding), query] + pv_files)

    all_chunks = cur.fetchall()
    print(f"  {len(all_chunks)} chunks PV_AG scorés au total\n")

    # ── 4. Impact du cap ──
    print(f"━━━ 4. IMPACT DU CAP RANK_IN_SOURCE ━━━")
    cap_stats = {}
    for cap in sorted(set([3, 5, 10, dynamic_cap, 15, 30])):
        cap_stats[cap] = sum(1 for r in all_chunks if r[1] <= cap)
    for cap, n in sorted(cap_stats.items()):
        label = ""
        if cap == 3:
            label = " (défaut sans prefilter)"
        elif cap == dynamic_cap and cap not in [3, 5, 10, 15, 30]:
            label = f" (dynamic_cap calculé)"
        elif cap == 30:
            label = " (SQL cap prefilter)"
        print(f"  cap={cap:>2} : {n:>4} chunks passent{label}")

    # ── 5. Couverture par année ──
    print(f"\n━━━ 5. COUVERTURE PAR ANNÉE ━━━")
    year_data = {}
    for r in all_chunks:
        grank, ris, cid, sf, nf, vec, bm25, rrf, cidx, chars, apercu = r
        annee = None
        for doc in pv_docs:
            if doc[0] == sf:
                annee = doc[3]
                break
        if annee is None:
            continue
        year_data.setdefault(annee, {"total": 0, "cap3": 0, "cap_dyn": 0, "cap15": 0, "cap30": 0,
                                     "reso_total": 0, "reso_cap3": 0, "files": set()})
        d = year_data[annee]
        d["total"] += 1
        d["files"].add(sf)
        has_reso = any(kw in (apercu or "").lower()
                       for kw in ["résolution", "resolution", "voté", "adopté",
                                  "majorité", "unanimité"])
        if has_reso:
            d["reso_total"] += 1
        if ris <= 3:
            d["cap3"] += 1
            if has_reso:
                d["reso_cap3"] += 1
        if ris <= dynamic_cap:
            d["cap_dyn"] += 1
        if ris <= 15:
            d["cap15"] += 1
        if ris <= 30:
            d["cap30"] += 1

    print(f"\n  {'Année':>5}  {'Fich':>4}  {'Total':>5}  {'📋Réso':>6}  {'cap=3':>5}  {'📋@3':>5}  "
          f"{'cap={}'.format(dynamic_cap):>7}  {'cap=15':>6}  {'cap=30':>6}")
    print(f"  {'─'*5}  {'─'*4}  {'─'*5}  {'─'*6}  {'─'*5}  {'─'*5}  {'─'*7}  {'─'*6}  {'─'*6}")
    for y in range(2010, 2019):
        if y in year_data:
            d = year_data[y]
            reso_pct = f"{d['reso_cap3']}/{d['reso_total']}" if d['reso_total'] > 0 else "0/0"
            print(f"  {y:>5}  {len(d['files']):>4}  {d['total']:>5}  {d['reso_total']:>6}  {d['cap3']:>5}  "
                  f"{reso_pct:>5}  {d['cap_dyn']:>7}  {d['cap15']:>6}  {d['cap30']:>6}")
        else:
            print(f"  {y:>5}     -      -       -      -      -        -       -       -  ⚠️ ABSENT")

    # ── 6. Focus années problématiques (2016, 2017, 2018) ──
    for focus_year in [2016, 2017, 2018]:
        print(f"\n━━━ 6. FOCUS {focus_year} : chunks par fichier PV ━━━")
        year_chunks = [(r, next((d[3] for d in pv_docs if d[0] == r[3]), None))
                       for r in all_chunks]
        year_chunks = [r for r, y in year_chunks if y == focus_year]

        if not year_chunks:
            print(f"  ⚠️  Aucun chunk pour {focus_year}")
            continue

        current_sf = None
        for r in year_chunks:
            grank, ris, cid, sf, nf, vec, bm25, rrf, cidx, chars, apercu = r
            nom = os.path.basename(nf or sf)[:55]
            if sf != current_sf:
                current_sf = sf
                sf_total = sum(1 for x in year_chunks if x[3] == sf)
                sf_cap3 = sum(1 for x in year_chunks if x[3] == sf and x[1] <= 3)
                sf_reso = sum(1 for x in year_chunks if x[3] == sf
                              and any(kw in (x[10] or "").lower()
                                      for kw in ["résolution", "resolution", "voté", "adopté",
                                                  "majorité", "unanimité"]))
                print(f"\n  📄 {nom}  ({sf_total} chunks, {sf_cap3} passent cap=3, {sf_reso} 📋résolutions)")

            cap_marker = ""
            if ris <= 3:
                cap_marker = " ✅"
            elif ris <= dynamic_cap:
                cap_marker = f" ⚠️ cap={dynamic_cap}"
            elif ris <= 15:
                cap_marker = " ⚠️ cap=15"
            else:
                cap_marker = " ❌"

            has_reso = "📋" if any(kw in (apercu or "").lower()
                                   for kw in ["résolution", "resolution", "voté", "adopté",
                                              "majorité", "unanimité", "point "]) else "  "
            print(f"    R/S={ris:>3}  G#{grank:<4}  chk={cidx:>2}  vec={float(vec):.3f}  bm25={float(bm25):.3f}  "
                  f"rrf={float(rrf):.5f}  {chars:>4}c  {has_reso}{cap_marker}")

else:
    print("  ⚠️  Aucun fichier PV_AG trouvé — impossible de scorer")

cur.close()
conn.close()
print("\n" + "=" * 95)
print("  FIN DU DIAGNOSTIC")
print("=" * 95)