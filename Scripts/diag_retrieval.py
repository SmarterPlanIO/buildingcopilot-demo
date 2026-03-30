"""
DIAGNOSTIC RETRIEVAL — Trace pas à pas du pipeline de recherche
================================================================
Objective : comprendre pourquoi "liste tous les sinistres de cette copro"
ne ramène qu'1 sinistre sur 9 possibles.

Trace chaque étape :
  A. Inventaire brut : combien de chunks SINISTRE existent en DB ?
  B. Scores bruts : vec_similarity + bm25 pour TOUS les chunks de la copro
  C. Stratégie détectée : inventaire/équilibré/ciblé + paramètres
  D. RRF : scores fusionnés avec boost theme + doc_type
  E. Source diversity : effet du PARTITION BY source_file
  F. Seuil de similarité : combien coupés par sim >= 0.15 ?
  G. LIMIT : combien coupés par le RERANK_CANDIDATES ?
  H. FlashRank reranking (si dispo) + résultat final

Usage :
  python diag_retrieval.py "liste tous les sinistres de cette copro" "NOM_COPRO"
  python diag_retrieval.py "liste tous les sinistres de cette copro"  (toutes copros)
"""
import json
import sys
import os
import re
import boto3
import psycopg2
from collections import Counter

# =====================================================
# CONFIGURATION — copier depuis 07_query_rag_ui.py
# =====================================================
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = "SmarterRAG99!"
AWS_REGION = "eu-west-1"
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"

SIMILARITY_THRESHOLD = 0.15
THEME_BOOST = 0.05
RRF_K = 60
RERANK_CANDIDATES = 120
MAX_CHUNKS_PER_SOURCE_INVENTAIRE = 2
DOC_TYPE_BOOST_INVENTAIRE = 0.03

PRIMARY_DOC_TYPES = {"SINISTRE", "ENTRETIEN", "COMPTABILITE", "DEVIS", "FACTURE"}

THEMES_KEYWORDS = {
    "assurance_sinistres": ["assurance", "sinistre", "dégât des eaux", "incendie"],
}

DOC_TYPE_KEYWORDS = {
    "SINISTRE": ["sinistre", "anomalie", "constat", "expertise", "dégât", "désordre"],
}

SEP = "=" * 80


def connect_db():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )
    conn.autocommit = True
    return conn


def get_bedrock():
    from botocore.config import Config
    return boto3.client(
        "bedrock-runtime", region_name=AWS_REGION,
        config=Config(read_timeout=120, retries={"max_attempts": 3}),
    )


def get_embedding(bedrock, text):
    if len(text) > 5000:
        text = text[:5000]
    body = json.dumps({"inputText": text, "dimensions": 1024, "normalize": True})
    resp = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL, body=body,
        contentType="application/json", accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]


def detect_strategy(query):
    """Reproduit detect_retrieval_strategy du script UI."""
    q = query.lower()

    year_matches = re.findall(r'20[0-2]\d', q)
    if len(year_matches) >= 2:
        years = sorted(set(int(y) for y in year_matches))
        if years[-1] - years[0] > 2:
            return 2, 0.03, 80, "Inventaire (plage temporelle)"

    since_match = re.search(r'depuis\s+20([0-2]\d)', q)
    if since_match:
        since_year = 2000 + int(since_match.group(1))
        if 2026 - since_year > 2:
            return 2, 0.03, 80, "Inventaire (historique)"

    broad_keywords = [
        "tous les", "toutes les", "liste", "lister", "inventaire",
        "historique", "depuis", "au fil des", "combien de",
        "comparer", "comparaison", "entre les",
        "chaque", "ensemble des", "récapitulatif", "synthèse globale",
        "quels sont", "quelles sont", "y a-t-il eu",
        "évolution", "tendance", "progression",
    ]
    if any(kw in q for kw in broad_keywords):
        return 2, 0.03, 80, "Inventaire"

    deep_keywords = [
        "article ", "lot n°", "lot ", "résolution n°",
        "que dit", "que prévoit", "détaille", "explique",
        "dans le règlement", "dans le pv", "dans le contrat",
        "ce document", "ce rapport",
    ]
    if any(kw in q for kw in deep_keywords):
        return 8, 0.005, 50, "Ciblé"

    return 3, 0.01, 50, "Équilibré"


def detect_themes(query):
    q = query.lower()
    return [t for t, kws in THEMES_KEYWORDS.items() if any(kw in q for kw in kws)]


def detect_doc_type(query):
    q = query.lower()
    for dt, kws in DOC_TYPE_KEYWORDS.items():
        if any(kw in q for kw in kws):
            return dt
    return None


# =====================================================
# MAIN
# =====================================================
def main():
    if len(sys.argv) < 2:
        print("Usage: python diag_retrieval.py \"votre requête\" [NOM_COPRO]")
        sys.exit(1)

    query = sys.argv[1]
    copro_filter = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"\n{SEP}")
    print(f"DIAGNOSTIC RETRIEVAL")
    print(f"Requête : {query}")
    print(f"Copro   : {copro_filter or '(toutes)'}")
    print(SEP)

    conn = connect_db()
    bedrock = get_bedrock()

    # ─── ÉTAPE A : Inventaire brut en DB ───
    print(f"\n{'─'*60}")
    print("ÉTAPE A — Inventaire brut en base de données")
    print(f"{'─'*60}")

    with conn.cursor() as cur:
        where = "WHERE copropriete = %s" if copro_filter else ""
        params = [copro_filter] if copro_filter else []

        # Tous les chunks
        cur.execute(f"SELECT COUNT(*) FROM chunks {where}", params)
        total_chunks = cur.fetchone()[0]

        # Par doc_type
        cur.execute(f"""
            SELECT doc_type, COUNT(*), COUNT(DISTINCT source_file)
            FROM chunks {where}
            GROUP BY doc_type ORDER BY COUNT(*) DESC
        """, params)
        type_stats = cur.fetchall()

        # Détail SINISTRE par source_file
        where_sin = f"WHERE doc_type = 'SINISTRE'" + (f" AND copropriete = %s" if copro_filter else "")
        params_sin = [copro_filter] if copro_filter else []
        cur.execute(f"""
            SELECT source_file, nom_fichier, COUNT(*), MIN(chunk_index), MAX(chunk_index)
            FROM chunks {where_sin}
            GROUP BY source_file, nom_fichier ORDER BY source_file
        """, params_sin)
        sinistre_files = cur.fetchall()

    print(f"\nTotal chunks en base : {total_chunks}")
    print(f"\nRépartition par doc_type :")
    for dt, cnt, n_files in type_stats:
        marker = " ◄◄◄" if dt == "SINISTRE" else ""
        print(f"  {dt:20s} : {cnt:5d} chunks, {n_files:3d} fichiers{marker}")

    print(f"\nDétail des fichiers SINISTRE ({len(sinistre_files)} fichiers) :")
    for sf, fn, cnt, cmin, cmax in sinistre_files:
        print(f"  [{cnt:2d} chunks] {fn}")
        print(f"             → {sf}")

    # ─── ÉTAPE B : Embedding + scores bruts ───
    print(f"\n{'─'*60}")
    print("ÉTAPE B — Embedding de la requête + scores bruts")
    print(f"{'─'*60}")

    query_embedding = get_embedding(bedrock, query)
    themes = detect_themes(query)
    doc_type_hint = detect_doc_type(query)

    print(f"\nThèmes détectés     : {themes or '(aucun)'}")
    print(f"Doc type hint       : {doc_type_hint or '(aucun)'}")

    # ─── ÉTAPE C : Stratégie ───
    print(f"\n{'─'*60}")
    print("ÉTAPE C — Stratégie de retrieval détectée")
    print(f"{'─'*60}")

    cps, dtb, mcl, strategy_label = detect_strategy(query)
    print(f"\nStratégie           : {strategy_label}")
    print(f"chunks_per_source   : {cps}")
    print(f"doc_type_boost      : {dtb}")
    print(f"max_chunks_llm      : {mcl}")

    # ─── ÉTAPE D+E+F+G : Requête SQL complète avec trace ───
    print(f"\n{'─'*60}")
    print("ÉTAPE D — Scores RRF bruts (AVANT source diversity + seuil)")
    print(f"{'─'*60}")

    doc_type_for_boost = doc_type_hint if doc_type_hint else "__NONE__"

    with conn.cursor() as cur:
        where_clauses = []
        params_before = []
        if copro_filter:
            where_clauses.append("copropriete = %s")
            params_before.append(copro_filter)
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # D — RRF brut (sans diversity, sans seuil, sans LIMIT)
        sql_rrf = f"""
            WITH base AS (
                SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                       themes, LEFT(text, 120) as text_preview,
                       1 - (embedding <=> %s::vector) as vec_similarity,
                       CASE WHEN themes && %s::text[] THEN {THEME_BOOST} ELSE 0 END as theme_boost,
                       ts_rank(text_search, plainto_tsquery('french', %s), 32) as bm25_score,
                       CASE WHEN doc_type = %s THEN %s ELSE 0 END as doc_type_boost
                FROM chunks
                {where_sql}
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
            SELECT chunk_id, doc_type, source_file, nom_fichier,
                   vec_similarity, bm25_score, theme_boost, doc_type_boost,
                   vec_rank, bm25_rank, rrf_score, text_preview
            FROM with_rrf
            ORDER BY rrf_score DESC
        """

        params_rrf = [
            str(query_embedding), themes if themes else [], query,
            doc_type_for_boost, dtb,
            *params_before,
        ]
        cur.execute(sql_rrf, params_rrf)
        all_rrf = cur.fetchall()

    # Stats globales
    sinistre_rrf = [r for r in all_rrf if r[1] == "SINISTRE"]
    print(f"\nTotal chunks scorés              : {len(all_rrf)}")
    print(f"Chunks SINISTRE dans le scoring  : {len(sinistre_rrf)}")

    # Afficher les SINISTRE avec leurs rangs
    print(f"\n{'─'*40}")
    print("Tous les chunks SINISTRE — rangs et scores :")
    print(f"{'─'*40}")
    for r in sinistre_rrf:
        cid, dt, sf, fn, vsim, bm25, tb, dtb_val, vr, br, rrf, preview = r
        above_thresh = "✅" if vsim >= SIMILARITY_THRESHOLD else "❌"
        print(f"\n  {above_thresh} vec={vsim:.4f} (rank {vr})  bm25={bm25:.4f} (rank {br})  "
              f"rrf={rrf:.5f}  theme={tb}  dtype_boost={dtb_val}")
        print(f"     {fn}")
        print(f"     {preview}...")

    # Combien au-dessus du seuil ?
    above = sum(1 for r in sinistre_rrf if r[4] >= SIMILARITY_THRESHOLD)
    below = len(sinistre_rrf) - above
    print(f"\n→ SINISTRE au-dessus du seuil ({SIMILARITY_THRESHOLD}) : {above}")
    print(f"→ SINISTRE en-dessous du seuil                      : {below}  ◄◄◄ PERDUS ICI SI > 0")

    # ─── ÉTAPE E : Source diversity ───
    print(f"\n{'─'*60}")
    print(f"ÉTAPE E — Source diversity (chunks_per_source = {cps})")
    print(f"{'─'*60}")

    # Simuler le PARTITION BY source_file
    source_counts = Counter()
    survived_diversity = []
    for r in all_rrf:
        sf = r[2]
        source_counts[sf] += 1
        if source_counts[sf] <= cps:
            survived_diversity.append(r)

    sin_after_div = [r for r in survived_diversity if r[1] == "SINISTRE"]
    print(f"\nChunks totaux après diversity    : {len(survived_diversity)}")
    print(f"Chunks SINISTRE après diversity  : {len(sin_after_div)}")

    # Quels fichiers SINISTRE ont perdu des chunks ?
    sin_sources_before = Counter(r[2] for r in sinistre_rrf)
    sin_sources_after = Counter(r[2] for r in sin_after_div)
    for sf in sin_sources_before:
        before = sin_sources_before[sf]
        after = sin_sources_after.get(sf, 0)
        if before != after:
            fn = next((r[3] for r in sinistre_rrf if r[2] == sf), "?")
            print(f"  ⚠️  {fn} : {before} → {after} chunks (capped)")

    # ─── ÉTAPE F : Seuil de similarité ───
    print(f"\n{'─'*60}")
    print(f"ÉTAPE F — Seuil de similarité ({SIMILARITY_THRESHOLD})")
    print(f"{'─'*60}")

    survived_threshold = [r for r in survived_diversity if r[4] >= SIMILARITY_THRESHOLD]
    sin_after_thresh = [r for r in survived_threshold if r[1] == "SINISTRE"]
    print(f"\nChunks après diversity + seuil   : {len(survived_threshold)}")
    print(f"SINISTRE après seuil             : {len(sin_after_thresh)}")

    lost_at_threshold = [r for r in sin_after_div if r[4] < SIMILARITY_THRESHOLD]
    if lost_at_threshold:
        print(f"\n⚠️  SINISTRE perdus par le seuil de similarité :")
        for r in lost_at_threshold:
            print(f"  ❌ vec={r[4]:.4f} < {SIMILARITY_THRESHOLD}  —  {r[3]}")
    else:
        print("  ✅ Aucun SINISTRE perdu au seuil")

    # ─── ÉTAPE G : LIMIT (RERANK_CANDIDATES) ───
    print(f"\n{'─'*60}")
    print(f"ÉTAPE G — LIMIT {RERANK_CANDIDATES} (RERANK_CANDIDATES)")
    print(f"{'─'*60}")

    survived_limit = survived_threshold[:RERANK_CANDIDATES]
    sin_after_limit = [r for r in survived_limit if r[1] == "SINISTRE"]
    print(f"\nChunks après LIMIT               : {len(survived_limit)}")
    print(f"SINISTRE après LIMIT             : {len(sin_after_limit)}")

    lost_at_limit = [r for r in sin_after_thresh if r not in survived_limit]
    if lost_at_limit:
        print(f"\n⚠️  SINISTRE perdus par le LIMIT :")
        for r in lost_at_limit:
            print(f"  ❌ rrf={r[10]:.5f}  —  {r[3]}")

    # ─── ÉTAPE H : Top max_chunks_llm ───
    print(f"\n{'─'*60}")
    print(f"ÉTAPE H — Top {mcl} chunks envoyés au LLM")
    print(f"{'─'*60}")

    final = survived_limit[:mcl]
    sin_final = [r for r in final if r[1] == "SINISTRE"]
    print(f"\nChunks finaux                    : {len(final)}")
    print(f"SINISTRE finaux                  : {len(sin_final)}")

    if len(sin_final) < len(sin_after_limit):
        print(f"\n⚠️  SINISTRE perdus dans le top {mcl} :")
        lost_final = [r for r in sin_after_limit if r not in final]
        for r in lost_final:
            print(f"  ❌ rrf={r[10]:.5f}  —  {r[3]}")

    # ─── RÉSUMÉ ───
    print(f"\n{SEP}")
    print("RÉSUMÉ — Où sont perdus les SINISTRE ?")
    print(SEP)
    n_db = len(sinistre_rrf)
    n_div = len(sin_after_div)
    n_thr = len(sin_after_thresh)
    n_lim = len(sin_after_limit)
    n_fin = len(sin_final)
    print(f"""
  En base (doc_type=SINISTRE)     : {n_db:3d} chunks / {len(set(r[2] for r in sinistre_rrf)):2d} fichiers
  Après source diversity (≤{cps}/src) : {n_div:3d} chunks  (perdu {n_db - n_div})
  Après seuil sim ≥ {SIMILARITY_THRESHOLD}          : {n_thr:3d} chunks  (perdu {n_div - n_thr})  ◄ suspect si gros
  Après LIMIT {RERANK_CANDIDATES}                 : {n_lim:3d} chunks  (perdu {n_thr - n_lim})
  Après top {mcl} (max_chunks_llm)      : {n_fin:3d} chunks  (perdu {n_lim - n_fin})
""")

    # Distribution des doc_types dans le top final
    dt_counter = Counter(r[1] for r in final)
    print("Distribution doc_type dans le top final :")
    for dt, cnt in dt_counter.most_common():
        marker = " ◄◄◄" if dt == "SINISTRE" else ""
        print(f"  {dt:20s} : {cnt:3d}{marker}")

    # Les fichiers SINISTRE distincts qui ARRIVENT vs qui MANQUENT
    sinistre_files_in_db = set(r[2] for r in sinistre_rrf)
    sinistre_files_final = set(r[2] for r in sin_final)
    missing = sinistre_files_in_db - sinistre_files_final
    if missing:
        print(f"\n⚠️  Fichiers SINISTRE ABSENTS du résultat final ({len(missing)}/{len(sinistre_files_in_db)}) :")
        for sf in sorted(missing):
            # Trouver le score de ce fichier
            scores = [(r[4], r[10]) for r in sinistre_rrf if r[2] == sf]
            best_vsim = max(s[0] for s in scores)
            best_rrf = max(s[1] for s in scores)
            fn = next((r[3] for r in sinistre_rrf if r[2] == sf), "?")
            reason = "sim < seuil" if best_vsim < SIMILARITY_THRESHOLD else "rrf trop bas"
            print(f"  ❌ {fn}")
            print(f"     best vec={best_vsim:.4f}  best rrf={best_rrf:.5f}  → {reason}")
    else:
        print(f"\n✅ Tous les {len(sinistre_files_in_db)} fichiers SINISTRE sont dans le résultat final")

    conn.close()
    print(f"\n{SEP}\nFIN\n{SEP}")


if __name__ == "__main__":
    main()
