"""
PALIM_retrieval.py — Retrieval hybride autonome (extrait de search_chunks
streamlit_app.py:761-959, sans dépendance Streamlit).

Conserve : pré-filtrage table documents (piloté par params explicites au lieu
de Haiku), vector + BM25 + RRF (k=60), diversité par groupe_doc, boost doc_type,
exclusion BORDEREAU_AR, MIN_CHUNK_CHARS, déduplication texte, quota RCP.
Ajoute : filtre multi-copro (code_ncg = ANY) + équilibrage par copropriété.

Le scoping est validé EN AMONT par PALIM_scope.validate_search_scope :
hybrid_search suppose copro_codes non vide et déjà normalisé.
"""
import json

import PALIM_config as cfg

# Index des colonnes du SELECT final (ordre figé ci-dessous)
_C_CHUNK_ID, _C_CODE_NCG, _C_COPRO, _C_SRC, _C_FILE, _C_DOCTYPE, \
    _C_TEXT, _C_CHUNK_IDX, _C_VEC, _C_BM25, _C_RRF, _C_RESCAT = range(12)


def embed_query(text, bedrock):
    """Embedding Titan V2 (1024 dims, normalisé). Repris de get_embedding:693."""
    if len(text) > cfg.EMBED_MAX_CHARS:
        text = text[:cfg.EMBED_MAX_CHARS]
    body = json.dumps({"inputText": text, "dimensions": cfg.EMBED_DIM, "normalize": True})
    resp = bedrock.invoke_model(
        modelId=cfg.EMBEDDING_MODEL, body=body,
        contentType="application/json", accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]


def _prefilter_source_files(conn, copro_codes, doc_type, year_min, year_max, statut, sous_type):
    """
    Pré-filtrage document-level via la table documents (cf. streamlit_app.py:772-831).
    Retourne (source_files|None, n_unique_groups). None => pré-filtrage inactif
    (0 résultat ou > 50 => pipeline complet, comportement d'origine).
    """
    clauses = ["code_ncg = ANY(%s)"]
    params = [copro_codes]
    if doc_type:
        clauses.append("(COALESCE(doc_type_corrige, doc_type) = %s OR dossier_lie = %s)")
        params.extend([doc_type, doc_type])
    if year_min and year_max:
        clauses.append("annee BETWEEN %s AND %s")
        params.extend([year_min, year_max])
    elif year_min:
        clauses.append("annee >= %s")
        params.append(year_min)
    elif year_max:
        clauses.append("annee <= %s")
        params.append(year_max)
    if statut:
        clauses.append("statut = %s")
        params.append(statut)
    if sous_type:
        clauses.append("sous_type = %s")
        params.append(sous_type)

    # Pas de filtre métier au-delà de la copro => pas de pré-filtrage (pipeline complet)
    if len(clauses) == 1:
        return None, 0

    sql = ("SELECT source_file, COALESCE(groupe_doc, source_file) FROM documents WHERE "
           + " AND ".join(clauses))
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception:
        return None, 0  # table absente / erreur => fallback pipeline complet

    files = [r[0] for r in rows]
    n_groups = len({r[1] for r in rows})
    if 0 < len(files) <= 50:
        return files, n_groups
    return None, 0


def _balance_by_copro(rows, copro_codes, max_chunks):
    """Équilibrage multi-copro : ~max_chunks/n par copro, redistribution des slots vides."""
    if len(copro_codes) < 2:
        return rows[:max_chunks]
    target = max(1, max_chunks // len(copro_codes))
    by = {}
    for r in rows:  # rows déjà ordonnés par rrf desc
        by.setdefault(r[_C_CODE_NCG], []).append(r)
    picked, picked_ids = [], set()
    for code in copro_codes:
        for r in by.get(code, [])[:target]:
            picked.append(r)
            picked_ids.add(r[_C_CHUNK_ID])
    if len(picked) < max_chunks:
        for r in rows:
            if r[_C_CHUNK_ID] not in picked_ids:
                picked.append(r)
                picked_ids.add(r[_C_CHUNK_ID])
                if len(picked) >= max_chunks:
                    break
    picked.sort(key=lambda r: r[_C_RRF], reverse=True)
    return picked[:max_chunks]


def _row_to_dict(r, rank):
    return {
        "chunk_id": r[_C_CHUNK_ID],
        "code_ncg": r[_C_CODE_NCG],
        "copropriete": r[_C_COPRO],
        "source_file": r[_C_SRC],
        "nom_fichier": r[_C_FILE],
        "doc_type": r[_C_DOCTYPE],
        "chunk_index": r[_C_CHUNK_IDX],
        "text": r[_C_TEXT],
        "score": round(float(r[_C_RRF]), 6),
        "vec_similarity": round(float(r[_C_VEC]), 4),
        "bm25_score": round(float(r[_C_BM25]), 4),
        "source_rank": rank,
    }


def hybrid_search(conn, bedrock, query, *, copro_codes, doc_type=None,
                  year_min=None, year_max=None, statut=None, sous_type=None,
                  retrieval_mode="equilibre", max_chunks=12,
                  include_bordereau_ar=False, include_legal_context=False,
                  enable_rerank=False):
    """
    Retrieval hybride scopé. copro_codes : liste non vide (validée en amont).
    Retourne une liste de dicts (cf. _row_to_dict), ordonnée par pertinence.
    """
    mode = cfg.RETRIEVAL_MODES.get(retrieval_mode, cfg.RETRIEVAL_MODES["equilibre"])
    chunks_per_source = mode["chunks_per_source"]
    sim_threshold = mode["sim_threshold"]

    query_embedding = embed_query(query, bedrock)

    # ── Étape 0 : pré-filtrage document ──
    prefilter_files, n_groups = _prefilter_source_files(
        conn, copro_codes, doc_type, year_min, year_max, statut, sous_type
    )
    prefilter_active = prefilter_files is not None
    if prefilter_active:
        n_unique = n_groups if n_groups > 0 else len(prefilter_files)
        chunks_per_source = max(2, min(15, max_chunks // max(n_unique, 1)))
        sim_threshold = 0.05  # docs déjà sélectionnés, seuil vectoriel inutile

    # ── Catégories de résolution exclues (mode inventaire) ──
    exclude_categories = None
    if retrieval_mode == "inventaire":
        exclude_categories = list(cfg.INVENTAIRE_EXCLUDE_CATEGORIES)

    with conn.cursor() as cur:
        where, wparams = ["c.nb_caracteres >= %s", "c.code_ncg = ANY(%s)"], [cfg.MIN_CHUNK_CHARS, copro_codes]
        if prefilter_active and prefilter_files:
            where.append("c.source_file = ANY(%s)")
            wparams.append(prefilter_files)
        if not include_bordereau_ar:
            where.append("c.doc_type != 'BORDEREAU_AR'")
        if exclude_categories:
            where.append("(c.resolution_category IS NULL OR c.resolution_category != ALL(%s))")
            wparams.append(exclude_categories)

        where_sql = "WHERE " + " AND ".join(where)
        doc_type_for_boost = doc_type if doc_type else "__NONE__"

        sql_cap = 30 if prefilter_active else chunks_per_source
        sql_limit = max(cfg.RERANK_CANDIDATES, max_chunks * 4) if prefilter_active else cfg.RERANK_CANDIDATES

        sql = f"""
            WITH base AS (
                SELECT c.chunk_id, c.code_ncg, c.copropriete, c.source_file, c.nom_fichier,
                       c.doc_type, c.text, c.chunk_index, c.resolution_category,
                       COALESCE(d.groupe_doc, c.source_file) AS groupe_doc,
                       1 - (c.embedding <=> %s::vector) AS vec_similarity,
                       ts_rank(c.text_search, plainto_tsquery('french', %s), 32) AS bm25_score,
                       CASE WHEN c.doc_type = %s THEN %s ELSE 0 END AS doc_type_boost
                FROM chunks c
                LEFT JOIN documents d ON c.source_file = d.source_file
                {where_sql}
            ),
            with_ranks AS (
                SELECT *,
                       row_number() OVER (ORDER BY vec_similarity DESC) AS vec_rank,
                       row_number() OVER (ORDER BY bm25_score DESC) AS bm25_rank
                FROM base
            ),
            with_rrf AS (
                SELECT *,
                       (1.0 / ({cfg.RRF_K} + vec_rank)
                        + 1.0 / ({cfg.RRF_K} + bm25_rank)
                        + doc_type_boost) AS rrf_score
                FROM with_ranks
            ),
            diversified AS (
                SELECT *,
                       row_number() OVER (PARTITION BY groupe_doc ORDER BY rrf_score DESC) AS rank_in_source
                FROM with_rrf
            )
            SELECT chunk_id, code_ncg, copropriete, source_file, nom_fichier, doc_type,
                   text, chunk_index, vec_similarity, bm25_score, rrf_score, resolution_category
            FROM diversified
            WHERE rank_in_source <= %s AND vec_similarity >= %s
            ORDER BY rrf_score DESC
            LIMIT %s
        """
        params = [str(query_embedding), query, doc_type_for_boost, 0.01,
                  *wparams, sql_cap, sim_threshold, sql_limit]
        cur.execute(sql, params)
        raw = cur.fetchall()

    # ── Déduplication par signature de texte ──
    seen, deduped = set(), []
    for r in raw:
        sig = (r[_C_TEXT] or "")[:300].strip()
        if sig not in seen:
            seen.add(sig)
            deduped.append(r)

    # ── Cap par source quand pré-filtrage actif ──
    if prefilter_active:
        from collections import defaultdict
        order = {id(r): i for i, r in enumerate(deduped)}
        by_src = defaultdict(list)
        for r in deduped:
            by_src[r[_C_SRC]].append(r)
        capped = []
        for chunks in by_src.values():
            capped.extend(chunks[:chunks_per_source])
        capped.sort(key=lambda r: order.get(id(r), 1_000_000))
        deduped = capped

    # TODO Phase 6 : rerank cohere (eu-central-1) si enable_rerank ; no-op en V1.

    # ── Équilibrage multi-copro + sélection finale ──
    top = _balance_by_copro(deduped, copro_codes, max_chunks)

    # ── Quota minimum RCP (contexte juridique) ──
    if include_legal_context:
        rcp_in_top = sum(1 for r in top if r[_C_DOCTYPE] == "RCP")
        if rcp_in_top < cfg.RCP_MIN_SLOTS:
            top_ids = {r[_C_CHUNK_ID] for r in top}
            rcp_extra = [r for r in deduped if r[_C_DOCTYPE] == "RCP" and r[_C_CHUNK_ID] not in top_ids]
            need = min(cfg.RCP_MIN_SLOTS - rcp_in_top, len(rcp_extra))
            for _ in range(need):
                for j in range(len(top) - 1, -1, -1):
                    if top[j][_C_DOCTYPE] != "RCP":
                        top.pop(j)
                        break
            top.extend(rcp_extra[:need])
            top.sort(key=lambda r: r[_C_RRF], reverse=True)

    return [_row_to_dict(r, i + 1) for i, r in enumerate(top)]
