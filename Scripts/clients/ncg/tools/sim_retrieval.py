"""
SIMULATION RETRIEVAL — Compare AVANT / APRÈS les corrections proposées
======================================================================
Connecte à la DB live, exécute la requête avec les paramètres actuels et proposés,
et compare les résultats côte à côte.

Corrections simulées :
  A. Point 2 (corrigé) : exempter du seuil vec_similarity les chunks dont doc_type
     matche le doc_type détecté → filtre = (vec_sim >= seuil OR doc_type = detected_type)
  B. Point 3 : RERANK_CANDIDATES 120 → 200 en inventaire
  C. Point 4 : doc_type_boost inventaire 0.03 → 0.05
  D. Point 5 : quota minimum SINISTRE (3 slots garantis dans le top final)

Usage :
  python sim_retrieval.py "liste tous les sinistres de cette copro" "NOM_COPRO"
"""
import os
import json
import sys
import re
import boto3
import psycopg2
from collections import Counter

# =====================================================
# CONFIG
# =====================================================
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
AWS_REGION = "eu-west-1"
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"

THEME_BOOST = 0.05
RRF_K = 60
SIMILARITY_THRESHOLD = 0.15

# AVANT (actuel)
CURRENT = {
    "label": "ACTUEL",
    "rerank_candidates": 120,
    "doc_type_boost": 0.03,
    "max_chunks_llm": 80,
    "chunks_per_source": 2,
    "sim_bypass_doctype": False,  # seuil dur pour tout le monde
    "sinistre_min_slots": 0,
}

# APRÈS (proposé)
PROPOSED = {
    "label": "PROPOSÉ",
    "rerank_candidates": 200,     # Point 3
    "doc_type_boost": 0.05,       # Point 4
    "max_chunks_llm": 80,
    "chunks_per_source": 2,
    "sim_bypass_doctype": True,   # Point 2 : bypass seuil si doc_type match
    "sinistre_min_slots": 5,      # Point 5 : quota minimum
}

PRIMARY_DOC_TYPES = {"SINISTRE", "ENTRETIEN", "COMPTABILITE", "DEVIS", "FACTURE"}

DOC_TYPE_KEYWORDS = {
    "SINISTRE": ["sinistre", "anomalie", "constat", "expertise", "dégât", "désordre"],
    "RCP": ["règlement de copropriété", "rcp", "règlement"],
    "PV_AG": ["pv", "procès-verbal", "assemblée générale", "ag"],
    "ENTRETIEN": ["entretien", "maintenance", "carnet"],
    "COMPTABILITE": ["annexe comptable", "grand livre", "comptabilité"],
}

THEMES_KEYWORDS = {
    "assurance_sinistres": ["assurance", "sinistre", "dégât des eaux", "incendie"],
}

SEP = "=" * 80


def connect_db():
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                            user=DB_USER, password=DB_PASSWORD)
    conn.autocommit = True
    return conn


def get_bedrock():
    from botocore.config import Config
    return boto3.client("bedrock-runtime", region_name=AWS_REGION,
                        config=Config(read_timeout=120, retries={"max_attempts": 3}))


def get_embedding(bedrock, text):
    if len(text) > 5000:
        text = text[:5000]
    body = json.dumps({"inputText": text, "dimensions": 1024, "normalize": True})
    resp = bedrock.invoke_model(modelId=EMBEDDING_MODEL, body=body,
                                contentType="application/json", accept="application/json")
    return json.loads(resp["body"].read())["embedding"]


def detect_themes(query):
    q = query.lower()
    return [t for t, kws in THEMES_KEYWORDS.items() if any(kw in q for kw in kws)]


def detect_doc_type(query):
    q = query.lower()
    for dt, kws in DOC_TYPE_KEYWORDS.items():
        if any(kw in q for kw in kws):
            return dt
    return None


def run_pipeline(conn, query_embedding, query, themes, doc_type_hint, copro, config):
    """Exécute le pipeline complet avec les paramètres donnés. Retourne la liste finale."""
    doc_type_for_boost = doc_type_hint if doc_type_hint else "__NONE__"
    dtb = config["doc_type_boost"]
    cps = config["chunks_per_source"]
    rerank_limit = config["rerank_candidates"]
    max_llm = config["max_chunks_llm"]
    bypass = config["sim_bypass_doctype"]
    min_slots = config["sinistre_min_slots"]

    where_clauses, params_before = [], []
    if copro:
        where_clauses.append("copropriete = %s")
        params_before.append(copro)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Point 2 : condition de seuil adaptée
    if bypass and doc_type_hint:
        sim_filter = f"AND (vec_similarity >= %s OR doc_type = %s)"
        sim_params = [SIMILARITY_THRESHOLD, doc_type_hint]
    else:
        sim_filter = f"AND vec_similarity >= %s"
        sim_params = [SIMILARITY_THRESHOLD]

    sql = f"""
        WITH base AS (
            SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                   themes, LEFT(text, 200) as text_preview,
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
        ),
        diversified AS (
            SELECT *,
                   row_number() OVER (
                       PARTITION BY source_file ORDER BY rrf_score DESC
                   ) as rank_in_source
            FROM with_rrf
        )
        SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
               themes, text_preview, vec_similarity, theme_boost, bm25_score,
               rrf_score, doc_type_boost
        FROM diversified
        WHERE rank_in_source <= %s
          {sim_filter}
        ORDER BY rrf_score DESC
        LIMIT %s
    """

    params = [
        str(query_embedding), themes if themes else [], query,
        doc_type_for_boost, dtb,
        *params_before,
        cps,
        *sim_params,
        rerank_limit,
    ]

    with conn.cursor() as cur:
        cur.execute(sql, params)
        results = cur.fetchall()

    # Déduplication
    seen, deduped = set(), []
    for r in results:
        sig = r[6][:150].strip()
        if sig not in seen:
            seen.add(sig)
            deduped.append(r)

    # Point 5 : quota minimum pour le doc_type détecté
    top = deduped[:max_llm]
    if min_slots > 0 and doc_type_hint:
        dtype_in_top = sum(1 for r in top if r[4] == doc_type_hint)
        if dtype_in_top < min_slots:
            dtype_below = [r for r in deduped[max_llm:] if r[4] == doc_type_hint]
            needed = min(min_slots - dtype_in_top, len(dtype_below))
            if needed > 0:
                extra = dtype_below[:needed]
                for _ in range(needed):
                    for j in range(len(top) - 1, -1, -1):
                        if top[j][4] != doc_type_hint:
                            top.pop(j)
                            break
                top.extend(extra)

    return top


def analyze_results(results, doc_type_hint, label):
    """Analyse et affiche les métriques d'un résultat."""
    dt_counter = Counter(r[4] for r in results)
    files_by_type = {}
    for r in results:
        dt = r[4]
        sf = r[2]
        if dt not in files_by_type:
            files_by_type[dt] = set()
        files_by_type[dt].add(sf)

    target_type = doc_type_hint or "SINISTRE"
    target_chunks = [r for r in results if r[4] == target_type]
    target_files = set(r[2] for r in target_chunks)

    print(f"\n  [{label}]")
    print(f"  Total chunks dans le top : {len(results)}")
    print(f"  {target_type} : {len(target_chunks)} chunks / {len(target_files)} fichiers distincts")

    print(f"\n  Distribution doc_type :")
    for dt, cnt in dt_counter.most_common():
        n_files = len(files_by_type.get(dt, set()))
        marker = " ◄◄◄" if dt == target_type else ""
        print(f"    {dt:20s} : {cnt:3d} chunks, {n_files:2d} fichiers{marker}")

    # Détail des fichiers cibles
    print(f"\n  Fichiers {target_type} présents :")
    for r in target_chunks:
        seen_files = set()
        key = r[2]
        if key not in seen_files:
            seen_files.add(key)
    # Afficher unique par fichier
    file_best = {}
    for r in target_chunks:
        sf = r[2]
        if sf not in file_best or r[10] > file_best[sf][10]:
            file_best[sf] = r
    for sf, r in sorted(file_best.items(), key=lambda x: -x[1][10]):
        print(f"    ✅ vec={r[7]:.4f}  rrf={r[10]:.5f}  {r[3]}")

    return target_files


def main():
    if len(sys.argv) < 2:
        print("Usage: python sim_retrieval.py \"requête\" [NOM_COPRO]")
        sys.exit(1)

    query = sys.argv[1]
    copro = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"\n{SEP}")
    print(f"SIMULATION RETRIEVAL — AVANT vs APRÈS")
    print(f"Requête : {query}")
    print(f"Copro   : {copro or '(toutes)'}")
    print(SEP)

    conn = connect_db()
    bedrock = get_bedrock()

    print("\n⏳ Calcul de l'embedding...")
    query_embedding = get_embedding(bedrock, query)
    themes = detect_themes(query)
    doc_type_hint = detect_doc_type(query)
    print(f"  Thèmes     : {themes or '(aucun)'}")
    print(f"  Doc type   : {doc_type_hint or '(aucun)'}")

    # Compter les fichiers cibles en base pour référence
    target_type = doc_type_hint or "SINISTRE"
    with conn.cursor() as cur:
        where_db = "WHERE doc_type = %s" + (f" AND copropriete = %s" if copro else "")
        params_db = [target_type] + ([copro] if copro else [])
        cur.execute(f"SELECT COUNT(DISTINCT source_file) FROM chunks {where_db}", params_db)
        total_target_files = cur.fetchone()[0]
    print(f"\n  Fichiers {target_type} en base : {total_target_files}")

    # ── ACTUEL ──
    print(f"\n{'─'*60}")
    print(f"Configuration ACTUELLE")
    print(f"  rerank_candidates={CURRENT['rerank_candidates']}, "
          f"doc_type_boost={CURRENT['doc_type_boost']}, "
          f"sim_bypass={CURRENT['sim_bypass_doctype']}, "
          f"min_slots={CURRENT['sinistre_min_slots']}")
    print(f"{'─'*60}")

    results_current = run_pipeline(conn, query_embedding, query, themes, doc_type_hint, copro, CURRENT)
    files_current = analyze_results(results_current, doc_type_hint, "ACTUEL")

    # ── PROPOSÉ ──
    print(f"\n{'─'*60}")
    print(f"Configuration PROPOSÉE")
    print(f"  rerank_candidates={PROPOSED['rerank_candidates']}, "
          f"doc_type_boost={PROPOSED['doc_type_boost']}, "
          f"sim_bypass={PROPOSED['sim_bypass_doctype']}, "
          f"min_slots={PROPOSED['sinistre_min_slots']}")
    print(f"{'─'*60}")

    results_proposed = run_pipeline(conn, query_embedding, query, themes, doc_type_hint, copro, PROPOSED)
    files_proposed = analyze_results(results_proposed, doc_type_hint, "PROPOSÉ")

    # ── COMPARAISON ──
    print(f"\n{SEP}")
    print("COMPARAISON DIRECTE")
    print(SEP)

    gained_files = files_proposed - files_current
    lost_files = files_current - files_proposed

    dt_current = Counter(r[4] for r in results_current)
    dt_proposed = Counter(r[4] for r in results_proposed)

    target_current = dt_current.get(target_type, 0)
    target_proposed = dt_proposed.get(target_type, 0)
    pvag_current = dt_current.get("PV_AG", 0)
    pvag_proposed = dt_proposed.get("PV_AG", 0)

    print(f"""
  Métrique                        ACTUEL    PROPOSÉ    Delta
  ─────────────────────────────────────────────────────────
  {target_type} chunks            {target_current:5d}     {target_proposed:5d}      {target_proposed - target_current:+d}
  {target_type} fichiers          {len(files_current):5d}     {len(files_proposed):5d}      {len(files_proposed) - len(files_current):+d}  (sur {total_target_files} en base)
  PV_AG chunks                  {pvag_current:5d}     {pvag_proposed:5d}      {pvag_proposed - pvag_current:+d}
  Total chunks                  {len(results_current):5d}     {len(results_proposed):5d}      {len(results_proposed) - len(results_current):+d}
""")

    if gained_files:
        print(f"  ✅ Fichiers {target_type} GAGNÉS ({len(gained_files)}) :")
        for sf in sorted(gained_files):
            # Trouver les détails
            r = next((x for x in results_proposed if x[2] == sf), None)
            if r:
                print(f"     + {r[3]}  (vec={r[7]:.4f}  rrf={r[10]:.5f})")

    if lost_files:
        print(f"\n  ❌ Fichiers {target_type} PERDUS ({len(lost_files)}) :")
        for sf in sorted(lost_files):
            r = next((x for x in results_current if x[2] == sf), None)
            if r:
                print(f"     - {r[3]}  (vec={r[7]:.4f}  rrf={r[10]:.5f})")

    if not lost_files and not gained_files:
        print(f"  ⚖️  Mêmes fichiers {target_type} dans les deux cas")

    # ── Détail des top 20 pour chaque config ──
    print(f"\n{'─'*60}")
    print(f"TOP 20 — ACTUEL")
    print(f"{'─'*60}")
    for i, r in enumerate(results_current[:20]):
        marker = " ◄" if r[4] == target_type else ""
        print(f"  #{i+1:2d}  {r[4]:15s}  vec={r[7]:.4f}  rrf={r[10]:.5f}  {r[3][:60]}{marker}")

    print(f"\n{'─'*60}")
    print(f"TOP 20 — PROPOSÉ")
    print(f"{'─'*60}")
    for i, r in enumerate(results_proposed[:20]):
        marker = " ◄" if r[4] == target_type else ""
        print(f"  #{i+1:2d}  {r[4]:15s}  vec={r[7]:.4f}  rrf={r[10]:.5f}  {r[3][:60]}{marker}")

    # ── Test avec d'autres requêtes pour vérifier pas d'effets de bord ──
    print(f"\n{SEP}")
    print("TESTS D'EFFETS DE BORD — autres requêtes")
    print(SEP)

    other_queries = [
        "quel est le budget prévisionnel 2024",
        "que dit le règlement de copropriété sur les parties communes",
        "quels travaux ont été votés en AG",
        "quel est le montant des charges de l'exercice 2023",
    ]

    for oq in other_queries:
        print(f"\n  Requête : \"{oq}\"")
        oq_emb = get_embedding(bedrock, oq)
        oq_themes = detect_themes(oq)
        oq_dt = detect_doc_type(oq)

        r_cur = run_pipeline(conn, oq_emb, oq, oq_themes, oq_dt, copro, CURRENT)
        r_pro = run_pipeline(conn, oq_emb, oq, oq_themes, oq_dt, copro, PROPOSED)

        dt_cur = Counter(r[4] for r in r_cur)
        dt_pro = Counter(r[4] for r in r_pro)

        # Vérifier si la distribution change de manière significative
        top5_cur = [(r[4], r[3][:50]) for r in r_cur[:5]]
        top5_pro = [(r[4], r[3][:50]) for r in r_pro[:5]]

        changed = top5_cur != top5_pro
        status = "⚠️  TOP 5 MODIFIÉ" if changed else "✅ Top 5 identique"
        print(f"    Doc type détecté : {oq_dt or '(aucun)'}")
        print(f"    {status}")

        if changed:
            for i in range(min(5, max(len(r_cur), len(r_pro)))):
                c = r_cur[i] if i < len(r_cur) else None
                p = r_pro[i] if i < len(r_pro) else None
                c_str = f"{c[4]:12s} {c[3][:40]}" if c else "(vide)"
                p_str = f"{p[4]:12s} {p[3][:40]}" if p else "(vide)"
                diff = " ≠" if c_str != p_str else "  "
                print(f"      #{i+1} ACTUEL: {c_str}")
                print(f"      #{i+1} PROPOSÉ: {p_str}{diff}")

    conn.close()
    print(f"\n{SEP}\nFIN\n{SEP}")


if __name__ == "__main__":
    main()
