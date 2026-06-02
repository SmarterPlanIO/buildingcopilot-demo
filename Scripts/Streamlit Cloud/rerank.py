"""Rerank Cohere 3.5 via Bedrock — version cloud.

Remplace l'absence de FlashRank en cloud. Mirroir de la logique desktop
(07_query_rag_ui.py) :
  - injection d'un en-tete propre `[DOC_TYPE] nom_fichier` avant le texte OCR
    (signal fort meme quand le contenu scanne est illisible) ;
  - score hybride RRF x rerank (alpha = RERANK_RRF_WEIGHT) pour eviter les
    chutes brutales des chunks a bon score RRF mais texte bruite.

Backend : Cohere Rerank 3.5 (`cohere.rerank-v3-5:0`) en eu-central-1 (Francfort,
UE) — indisponible en eu-west-1. Appel cross-region Irlande->Francfort, intra-UE,
les documents juridiques restent dans l'UE. API : bedrock-agent-runtime.rerank().

Ce module ne contient que de la logique ; il ne touche ni a Streamlit ni a
st.secrets. Le client boto3 est construit par l'appelant (qui detient les creds)
via build_rerank_client() et passe a rerank_rows().
"""
import os
import time

import boto3

# eu-west-1 (region app/DB) n'a aucun modele rerank ; Francfort oui.
RERANK_REGION = "eu-central-1"
RERANK_MODEL_ARN = (
    "arn:aws:bedrock:eu-central-1::foundation-model/cohere.rerank-v3-5:0"
)

# Mix RRF x Cohere : 0 = Cohere pur, 1 = RRF pur. 0.25 = on fait davantage
# confiance a Cohere (reranker fort) qu'au RRF, pour mieux couper le bruit
# multi-copro, tout en gardant un filet lexical (requetes a terme exact).
RERANK_RRF_WEIGHT = 0.25
# Borne le pool envoye a Cohere (cout + latence). Au-dela, queue en ordre RRF.
MAX_RERANK_DOCS = 200
# Troncature par document : en-tete + debut du texte suffisent au cross-encoder.
_MAX_DOC_CHARS = 1900

# Index dans les tuples retournes par search_chunks (ordre du SELECT SQL cloud) :
# chunk_id, copropriete, source_file, nom_fichier, doc_type, text,
# vec_similarity, bm25_score, rrf_score, chunk_index, resolution_category
_I_NOM, _I_DOC_TYPE, _I_TEXT, _I_RRF = 3, 4, 5, 8


def build_rerank_client(access_key, secret_key, region=RERANK_REGION):
    """Client bedrock-agent-runtime en region rerank. A cacher cote appelant."""
    return boto3.client(
        "bedrock-agent-runtime",
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _passage_text(row):
    """En-tete `[DOC_TYPE] nom lisible` + debut du texte (anti-OCR)."""
    doc_type = row[_I_DOC_TYPE] or ""
    nom = row[_I_NOM] or ""
    nom_clean = (
        os.path.splitext(os.path.basename(nom))[0].replace("_", " ").replace("-", " ")
    )
    header = f"[{doc_type}] {nom_clean}"
    return f"{header}\n{(row[_I_TEXT] or '')[:_MAX_DOC_CHARS]}"


def rerank_rows(query, rows, client, rrf_weight=RERANK_RRF_WEIGHT, stats=None):
    """Reordonne les candidats par score hybride RRF x Cohere.

    rows : liste de tuples issus de search_chunks (ordre RRF decroissant).
    Retourne une liste de la meme longueur, reordonnee. Fallback silencieux sur
    l'ordre RRF d'entree si le client est absent ou si l'appel rerank echoue —
    une requete utilisateur ne doit jamais casser sur un probleme de rerank.

    stats : dict optionnel rempli pour l'observabilite (applied, ok,
    fallback_reason, n_in, n_results, latency_ms). Permet a l'appelant de tracer
    le rerank (span Langfuse) et de distinguer un vrai rerank d'un fallback RRF.
    """
    def _stat(**kw):
        if stats is not None:
            stats.update(kw)

    if client is None or len(rows) <= 1:
        _stat(applied=False, ok=False, fallback_reason="no_client_or_single",
              n_in=len(rows), latency_ms=0)
        return rows

    pool = rows[:MAX_RERANK_DOCS]
    tail = rows[MAX_RERANK_DOCS:]  # au-dela de la borne : conserves en queue, ordre RRF

    passages = [_passage_text(r) for r in pool]

    _t0 = time.time()
    try:
        resp = client.rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query[:2000]}}],
            sources=[
                {
                    "type": "INLINE",
                    "inlineDocumentSource": {
                        "type": "TEXT",
                        "textDocument": {"text": p},
                    },
                }
                for p in passages
            ],
            rerankingConfiguration={
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {
                    "modelConfiguration": {"modelArn": RERANK_MODEL_ARN},
                    "numberOfResults": len(passages),
                },
            },
        )
        results = resp.get("results", [])
    except Exception as exc:
        _stat(applied=False, ok=False, fallback_reason=f"exception:{type(exc).__name__}",
              n_in=len(pool), latency_ms=int((time.time() - _t0) * 1000))
        return rows  # jamais casser la requete sur un echec rerank

    _latency_ms = int((time.time() - _t0) * 1000)

    if not results:
        _stat(applied=False, ok=False, fallback_reason="empty_results",
              n_in=len(pool), n_results=0, latency_ms=_latency_ms)
        return rows

    # index pool -> score de pertinence Cohere (deja ~[0,1])
    cohere_score = {
        res["index"]: float(res.get("relevanceScore", 0.0)) for res in results
    }

    # Normaliser le RRF du pool sur [0,1] pour le mix
    rrf_vals = [float(r[_I_RRF]) for r in pool]
    rmin, rmax = min(rrf_vals), max(rrf_vals)
    rrange = (rmax - rmin) or 1.0

    alpha = rrf_weight
    scored = []
    for i, r in enumerate(pool):
        rrf_norm = (float(r[_I_RRF]) - rmin) / rrange
        c_norm = cohere_score.get(i, 0.0)
        hybrid = alpha * rrf_norm + (1.0 - alpha) * c_norm
        scored.append((hybrid, i, r))

    # tri stable : score hybride desc, puis ordre RRF d'origine en egalite
    scored.sort(key=lambda x: (-x[0], x[1]))
    _stat(applied=True, ok=True, fallback_reason=None,
          n_in=len(pool), n_results=len(results), latency_ms=_latency_ms)
    return [r for _, _, r in scored] + tail
