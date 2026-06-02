"""
PALIM_discovery.py — Découverte documentaire (agrégat, PAS le pipeline RRF).

Identifie les copropriétés pertinentes pour une requête sans produire de
réponse de fond. Retour orienté triage : match_count, doc_types, years,
snippet court. final_answer_allowed=false (cf. PLAN_ACTION §3.3).
"""
import PALIM_config as cfg
from PALIM_retrieval import embed_query


def discover_copros(conn, bedrock, query, doc_type=None, year_min=None,
                    year_max=None, top_k=cfg.DISCOVERY_TOP_K):
    qvec = embed_query(query, bedrock)

    pool_clauses = ["c.doc_type != 'BORDEREAU_AR'", "c.nb_caracteres >= %s"]
    pool_params = [cfg.MIN_CHUNK_CHARS]
    if doc_type:
        pool_clauses.append("c.doc_type = %s")
        pool_params.append(doc_type)
    pool_where = " AND ".join(pool_clauses)

    year_clause = ""
    year_params = []
    if year_min and year_max:
        year_clause = "WHERE annee IS NULL OR annee BETWEEN %s AND %s"
        year_params = [year_min, year_max]
    elif year_min:
        year_clause = "WHERE annee IS NULL OR annee >= %s"
        year_params = [year_min]
    elif year_max:
        year_clause = "WHERE annee IS NULL OR annee <= %s"
        year_params = [year_max]

    sql = f"""
        WITH pool AS (
            SELECT c.code_ncg, c.copropriete, c.doc_type, c.text, c.source_file,
                   1 - (c.embedding <=> %s::vector) AS sim
            FROM chunks c
            WHERE {pool_where}
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
        ),
        enriched AS (
            SELECT p.code_ncg, p.copropriete, p.doc_type, p.text, p.sim, d.annee,
                   row_number() OVER (PARTITION BY p.code_ncg ORDER BY p.sim DESC) AS rk
            FROM pool p
            LEFT JOIN documents d ON p.source_file = d.source_file
        ),
        filtered AS (
            SELECT * FROM enriched {year_clause}
        )
        SELECT code_ncg,
               MAX(copropriete) AS nom,
               COUNT(*) AS match_count,
               array_agg(DISTINCT doc_type) AS doc_types,
               array_remove(array_agg(DISTINCT annee), NULL) AS years,
               MAX(CASE WHEN rk = 1 THEN LEFT(text, %s) END) AS snippet,
               ROUND(MAX(sim)::numeric, 4) AS top_sim
        FROM filtered
        GROUP BY code_ncg
        ORDER BY match_count DESC, top_sim DESC
        LIMIT %s
    """
    params = [str(qvec), *pool_params, str(qvec), cfg.RERANK_CANDIDATES,
              *year_params, cfg.DISCOVERY_SNIPPET_CHARS, top_k]

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    candidates = []
    for code_ncg, nom, match_count, doc_types, years, snippet, top_sim in rows:
        candidates.append({
            "code_ncg": code_ncg,
            "nom": nom,
            "match_count": int(match_count),
            "doc_types": sorted([t for t in (doc_types or []) if t]),
            "years": sorted([int(y) for y in (years or [])]),
            "top_evidence_snippet": (snippet or "").strip(),
            "top_sim": float(top_sim) if top_sim is not None else None,
        })
    return candidates
