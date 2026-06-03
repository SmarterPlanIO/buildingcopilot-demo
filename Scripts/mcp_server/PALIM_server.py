"""
PALIM_server.py — Serveur MCP FastMCP exposant le retrieval PALIM à Claude Teams.

5 tools (cf. PLAN_ACTION §3) :
  PALIM_search_chunks      — retrieval scopé (réponse finale), invariant non-dilution
  PALIM_list_copros        — annuaire (identité, fuzzy nom/adresse/alias)
  PALIM_discover_copros    — découverte documentaire (agrégat, final_answer_allowed=false)
  PALIM_get_full_document  — drilldown plafonné (anti-aspiration)
  PALIM_search_dossiers    — dossiers sinistres scopés

Invariants serveur : scope validé en amont, retours structurés {ok,...},
aucune exception brute, jamais d'env var dans les messages, caps appliqués.

App ASGI exposée sous `app` pour uvicorn (Lambda Web Adapter).
"""
import json
import time

import boto3
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import PALIM_config as cfg
import PALIM_scope as scope
import PALIM_tracing as lf
from PALIM_db import get_conn
from PALIM_retrieval import hybrid_search
from PALIM_discovery import discover_copros
from PALIM_copros import list_copros as _list_copros
from PALIM_dossiers import search_dossiers as _search_dossiers
import PALIM_assynco as assynco

# ── Clients singletons (réutilisés sur invocations warm) ──
_bedrock = None
_rerank = None


def _bedrock_client():
    global _bedrock
    if _bedrock is None:
        from botocore.config import Config
        _bedrock = boto3.client(
            "bedrock-runtime", region_name=cfg.AWS_REGION_EMBED,
            config=Config(read_timeout=60, connect_timeout=10,
                          retries={"max_attempts": 3}, tcp_keepalive=True),
        )
    return _bedrock


def _rerank_client():
    """Client rerank Cohere — bedrock-agent-runtime en eu-central-1 (Francfort).
    None si rerank désactivé. Creds = rôle Lambda en prod / env en local."""
    global _rerank
    if not cfg.ENABLE_RERANK:
        return None
    if _rerank is None:
        from botocore.config import Config
        _rerank = boto3.client(
            "bedrock-agent-runtime", region_name=cfg.AWS_REGION_RERANK,
            config=Config(read_timeout=30, connect_timeout=10,
                          retries={"max_attempts": 2}, tcp_keepalive=True),
        )
    return _rerank


def _log(tool, **fields):
    """Log structuré JSON → stdout (CloudWatch). Jamais de secret."""
    rec = {"tool": tool, **fields}
    try:
        print(json.dumps(rec, ensure_ascii=False, default=str))
    except Exception:
        pass


def _clamp(val, default, cap):
    try:
        v = int(val)
    except (TypeError, ValueError):
        return default
    return max(1, min(v, cap))


def _internal_error(tool, exc):
    """Erreur contrôlée, sans détail interne sensible."""
    _log(tool, error_type="INTERNAL", error=f"{type(exc).__name__}")
    return {"ok": False, "error_type": "INTERNAL",
            "message": "Erreur interne du serveur PALIM. Réessayer ou reformuler."}


# DNS rebinding protection OFF : FastMCP l'auto-active pour les hosts localhost
# avec une allowlist localhost (server.py:178), ce qui rejette en 421
# "Invalid Host header" le domaine *.lambda-url.*.on.aws derrière la Function URL.
# Inadaptée à un endpoint public ; barrière d'accès = slug secret + resource policy.
# Le check Content-Type des POST reste actif (indépendant de ce flag).
# stateless_http=True : indispensable en Lambda. Le mode stateful garde les
# sessions (mcp-session-id) en mémoire du conteneur ; or chaque requête est une
# invocation séparée pouvant taper une autre instance → "Session not found".
# En stateless chaque requête est autonome (adapté au serverless).
_SECURITY = TransportSecuritySettings(enable_dns_rebinding_protection=False)
try:
    mcp = FastMCP("PALIM", streamable_http_path="/" + cfg.MCP_URL_SLUG.lstrip("/"),
                  transport_security=_SECURITY, stateless_http=True)
except TypeError:
    mcp = FastMCP("PALIM", transport_security=_SECURITY, stateless_http=True)


# ============================================================================
# Tools
# ============================================================================

@mcp.tool()
def PALIM_search_chunks(
    query: str,
    copro_codes: list[str],
    doc_type: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    statut: str | None = None,
    sous_type: str | None = None,
    retrieval_mode: str = "equilibre",
    max_chunks: int = 12,
    include_bordereau_ar: bool = False,
    include_legal_context: bool = False,
) -> dict:
    """Recherche les passages (chunks) les plus pertinents pour répondre à une question sur une ou plusieurs copropriétés.

    INVARIANT : nécessite au moins un code_ncg dans copro_codes (sinon erreur MISSING_COPRO_SCOPE).
    Pour identifier les copros d'abord, utiliser PALIM_discover_copros ou PALIM_list_copros.

    Args:
        query: La question ou requête (reformulée si besoin).
        copro_codes: Codes NCG des copropriétés (1 = mono ; >=2 = comparaison équilibrée).
        doc_type: Filtre type de document (PV_AG, RCP, CONTRAT, ASSURANCE, ...).
        year_min / year_max: Bornes temporelles (année du document).
        statut / sous_type: Filtres document-level optionnels.
        retrieval_mode: "cible" (précis), "equilibre" (défaut), "inventaire" (large).
        max_chunks: Nombre de chunks (plafonné à 30 côté serveur).
        include_bordereau_ar: Inclure les bordereaux AR (exclus par défaut).
        include_legal_context: Force un quota minimum de chunks RCP (cas juridiques).

    Returns:
        {ok, inferred_scope, copro_codes, query_used, filters_applied, warnings, results[]}.
    """
    t0 = time.time()
    codes = scope.normalize_copro_codes(copro_codes)
    tr = lf.start_trace("PALIM_search_chunks",
                        input={"query": query, "copro_codes": codes,
                               "filters": {"doc_type": doc_type, "year_min": year_min,
                                           "year_max": year_max, "retrieval_mode": retrieval_mode}},
                        tags=["mcp", "search_chunks"])
    try:
        ok, inferred, err = scope.validate_search_scope(codes)
        if not ok:
            _log("PALIM_search_chunks", error_type=err["error_type"], copro_codes=codes)
            lf.update_trace(tr, output=err,
                            metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return err

        max_chunks = _clamp(max_chunks, 12, cfg.MAX_CHUNKS_CAP)
        warnings = scope.build_scope_warnings(codes)
        try:
            results = hybrid_search(
                get_conn(), _bedrock_client(), query,
                copro_codes=codes, doc_type=doc_type, year_min=year_min, year_max=year_max,
                statut=statut, sous_type=sous_type, retrieval_mode=retrieval_mode,
                max_chunks=max_chunks, include_bordereau_ar=include_bordereau_ar,
                include_legal_context=include_legal_context,
                enable_rerank=cfg.ENABLE_RERANK, rerank_client=_rerank_client(), trace=tr,
            )
        except Exception as exc:
            lf.update_trace(tr, output={"error_type": "INTERNAL"},
                            metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return _internal_error("PALIM_search_chunks", exc)

        if inferred == "multi":
            found = {r["code_ncg"] for r in results}
            missing = [c for c in codes if c not in found]
            if missing:
                warnings.append(f"Aucun résultat pour : {missing}.")

        _log("PALIM_search_chunks", inferred_scope=inferred, copro_codes=codes,
             max_chunks=max_chunks, n_results=len(results),
             latency_ms=int((time.time() - t0) * 1000), warnings=warnings)
        lf.update_trace(tr, output={"n_results": len(results), "inferred_scope": inferred,
                                    "warnings": warnings},
                        metadata={"latency_ms": int((time.time() - t0) * 1000),
                                  "max_chunks": max_chunks})
        return {
            "ok": True, "inferred_scope": inferred, "copro_codes": codes,
            "query_used": query,
            "filters_applied": {"doc_type": doc_type, "year_min": year_min, "year_max": year_max,
                                "statut": statut, "sous_type": sous_type, "retrieval_mode": retrieval_mode,
                                "include_bordereau_ar": include_bordereau_ar,
                                "include_legal_context": include_legal_context},
            "warnings": warnings, "results": results,
            "trace_ref": lf.trace_id(tr),
        }
    finally:
        lf.flush()


@mcp.tool()
def PALIM_list_copros(query: str | None = None) -> dict:
    """Annuaire des copropriétés (identité). Permet de choisir la bonne copro SANS lancer de recherche documentaire.

    Si query est fourni, retourne des CANDIDATS classés par correspondance sur le code NCG,
    le nom de résidence, la rue, l'adresse ou un alias. Un alias n'est PAS unique : plusieurs
    copros peuvent matcher (ex. une même rue). La sélection finale du code revient à l'utilisateur.

    Args:
        query: Nom, adresse, rue, alias ou code à rechercher (optionnel).

    Returns:
        {ok, copros[]} avec code_ncg, nom, nb_documents, nb_chunks, doc_types_available,
        annee_min/max, has_rcp, has_pv_ag, has_dossiers (+ adresse/aliases si disponibles).
    """
    t0 = time.time()
    tr = lf.start_trace("PALIM_list_copros", input={"query": query}, tags=["mcp", "list_copros"])
    try:
        try:
            res = _list_copros(get_conn(), query)
        except Exception as exc:
            lf.update_trace(tr, output={"error_type": "INTERNAL"},
                            metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return _internal_error("PALIM_list_copros", exc)
        n = len(res.get("copros", []))
        _log("PALIM_list_copros", query=bool(query), n=n,
             latency_ms=int((time.time() - t0) * 1000))
        lf.update_trace(tr, output={"n_copros": n},
                        metadata={"latency_ms": int((time.time() - t0) * 1000)})
        return res
    finally:
        lf.flush()


@mcp.tool()
def PALIM_discover_copros(
    query: str, doc_type: str | None = None,
    year_min: int | None = None, year_max: int | None = None, top_k: int = 10,
) -> dict:
    """Découverte documentaire : identifie les copropriétés ayant des documents pertinents pour une requête.

    NE PRODUIT PAS de réponse finale (final_answer_allowed=false). C'est une étape de triage :
    après avoir identifié les copros candidates ici, appeler PALIM_search_chunks scopé sur le(s) code(s) choisi(s).

    Args:
        query: La requête de découverte.
        doc_type: Restreindre à un type de document (optionnel).
        year_min / year_max: Bornes temporelles (optionnel).
        top_k: Nombre de copros candidates (défaut 10).

    Returns:
        {ok, final_answer_allowed: false, candidates[], warnings} ; chaque candidat :
        code_ncg, nom, match_count, doc_types, years, top_evidence_snippet.
    """
    t0 = time.time()
    top_k = _clamp(top_k, cfg.DISCOVERY_TOP_K, 25)
    tr = lf.start_trace("PALIM_discover_copros",
                        input={"query": query, "doc_type": doc_type, "top_k": top_k},
                        tags=["mcp", "discover_copros"])
    try:
        try:
            candidates = discover_copros(get_conn(), _bedrock_client(), query,
                                         doc_type=doc_type, year_min=year_min,
                                         year_max=year_max, top_k=top_k, trace=tr)
        except Exception as exc:
            lf.update_trace(tr, output={"error_type": "INTERNAL"},
                            metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return _internal_error("PALIM_discover_copros", exc)
        _log("PALIM_discover_copros", n=len(candidates), latency_ms=int((time.time() - t0) * 1000))
        lf.update_trace(tr, output={"n_candidates": len(candidates)},
                        metadata={"latency_ms": int((time.time() - t0) * 1000)})
        return {"ok": True, "final_answer_allowed": False, "candidates": candidates,
                "warnings": ["final_answer_not_allowed_from_global_discovery"]}
    finally:
        lf.flush()


@mcp.tool()
def PALIM_get_full_document(
    source_file: str, max_chars: int = cfg.GET_FULL_DOC_DEFAULT_CHARS,
    chunk_start: int | None = None, chunk_end: int | None = None,
    reason: str | None = None,
) -> dict:
    """Charge le texte intégral (concaténé, plafonné) d'un document identifié par son source_file.

    Anti-aspiration : max_chars plafonné serveur, tronqué par défaut. Refuse les patterns larges.
    Utiliser uniquement pour un document précis repéré via PALIM_search_chunks.

    Args:
        source_file: Identifiant exact du document (champ source_file d'un chunk).
        max_chars: Longueur max retournée (plafonné à 50000).
        chunk_start / chunk_end: Plage de chunk_index à inclure (optionnel).
        reason: Raison de la demande (traçabilité, optionnel).

    Returns:
        {ok, source_file, metadata, text, truncated, max_chars, total_chars_available, chunks_returned}.
    """
    t0 = time.time()
    tr = lf.start_trace("PALIM_get_full_document",
                        input={"source_file": source_file, "reason": reason},
                        tags=["mcp", "get_full_document"])
    try:
        sf = (source_file or "").strip()
        if len(sf) < 3 or "%" in sf or "*" in sf:
            res = {"ok": False, "error_type": "INVALID_SOURCE_FILE",
                   "message": "source_file invalide ou trop large. Fournir un source_file exact issu de PALIM_search_chunks."}
            lf.update_trace(tr, output=res, metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return res
        max_chars = _clamp(max_chars, cfg.GET_FULL_DOC_DEFAULT_CHARS, cfg.MAX_CHARS_CAP)
        try:
            with get_conn().cursor() as cur:
                cur.execute(
                    """SELECT code_ncg, copropriete, doc_type, nom_fichier, chunk_index, text
                       FROM chunks WHERE source_file = %s ORDER BY chunk_index""", (sf,))
                rows = cur.fetchall()
        except Exception as exc:
            lf.update_trace(tr, output={"error_type": "INTERNAL"},
                            metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return _internal_error("PALIM_get_full_document", exc)

        if not rows:
            res = {"ok": False, "error_type": "NOT_FOUND",
                   "message": f"Aucun document pour source_file={sf}."}
            lf.update_trace(tr, output=res, metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return res

        if chunk_start is not None or chunk_end is not None:
            lo = chunk_start if chunk_start is not None else -10**9
            hi = chunk_end if chunk_end is not None else 10**9
            rows = [r for r in rows if r[4] is not None and lo <= r[4] <= hi]

        meta = {"code_ncg": rows[0][0], "copropriete": rows[0][1],
                "doc_type": rows[0][2], "nom_fichier": rows[0][3]}
        full = "\n\n".join((r[5] or "") for r in rows)
        total = len(full)

        text, included, acc = [], [], 0
        for r in rows:
            seg = r[5] or ""
            if acc + len(seg) > max_chars and included:
                break
            text.append(seg)
            included.append(r[4])
            acc += len(seg) + 2
        out_text = "\n\n".join(text)[:max_chars]
        truncated = total > len(out_text)

        _log("PALIM_get_full_document", source_file=sf, total_chars=total,
             returned_chars=len(out_text), truncated=truncated,
             latency_ms=int((time.time() - t0) * 1000))
        lf.update_trace(tr, output={"returned_chars": len(out_text), "truncated": truncated,
                                    "chunks_returned": len(included), "code_ncg": meta["code_ncg"]},
                        metadata={"latency_ms": int((time.time() - t0) * 1000)})
        return {"ok": True, "source_file": sf, "metadata": meta, "text": out_text,
                "truncated": truncated, "max_chars": max_chars,
                "total_chars_available": total, "chunks_returned": included}
    finally:
        lf.flush()


@mcp.tool()
def PALIM_search_dossiers(
    query: str, copro_codes: list[str] | None = None, max_results: int = 20,
) -> dict:
    """Recherche les dossiers sinistres / travaux / contentieux (base Assynco/RAG).

    Scope dérivé de copro_codes (0 = découverte de dossiers candidats ; 1 = single ; >=2 = équilibré).

    Args:
        query: Référence, nom de lésé, ou description.
        copro_codes: Codes NCG (optionnel).
        max_results: Nombre max de dossiers (plafonné à 50).

    Returns:
        {ok, inferred_scope, copro_codes, warnings, results[]} ; chaque dossier :
        dossier_id, code_ncg, copropriete, type, statut, lese, montant, source.
    """
    t0 = time.time()
    codes = scope.normalize_copro_codes(copro_codes)
    inferred = scope.infer_scope(codes)
    tr = lf.start_trace("PALIM_search_dossiers",
                        input={"query": query, "copro_codes": codes},
                        tags=["mcp", "search_dossiers"])
    try:
        max_results = _clamp(max_results, 20, cfg.MAX_RESULTS_CAP)
        warnings = scope.build_scope_warnings(codes)
        if inferred == "global":
            warnings.append("Recherche dossiers sans copro : résultats candidats, à confirmer par scope.")
        try:
            results = _search_dossiers(get_conn(), query, copro_codes=codes, max_results=max_results)
        except Exception as exc:
            lf.update_trace(tr, output={"error_type": "INTERNAL"},
                            metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return _internal_error("PALIM_search_dossiers", exc)
        _log("PALIM_search_dossiers", inferred_scope=inferred, copro_codes=codes,
             n_results=len(results), latency_ms=int((time.time() - t0) * 1000))
        lf.update_trace(tr, output={"n_results": len(results), "inferred_scope": inferred,
                                    "warnings": warnings},
                        metadata={"latency_ms": int((time.time() - t0) * 1000)})
        return {"ok": True, "inferred_scope": inferred, "copro_codes": codes,
                "warnings": warnings, "results": results, "trace_ref": lf.trace_id(tr)}
    finally:
        lf.flush()


# ============================================================================
# Tools Assynco (ERP assurance — Airtable, lecture R1 : Copro + Police + Sinistre)
# cf. PLAN_ACTION_MCP_ASSYNCO.md. Scope = hub Copropriétés (code NCG -> record).
# ============================================================================

def _assynco_guard(code_ncg):
    """(ok, code|None, err|None) — vérifie l'activation + la présence d'un code."""
    if not cfg.ENABLE_ASSYNCO:
        return False, None, {"ok": False, "error_type": "ASSYNCO_DISABLED",
                             "message": "Accès Assynco désactivé sur ce serveur."}
    codes = scope.normalize_copro_codes(code_ncg)
    if not codes:
        return False, None, {"ok": False, "error_type": "MISSING_COPRO_SCOPE",
                             "message": "code_ncg requis (ex: '5390'). Utiliser PALIM_list_copros pour le trouver."}
    return True, codes[0], None


@mcp.tool()
def PALIM_assynco_get_copro(code_ncg: str) -> dict:
    """Fiche d'une copropriété dans l'ERP assurance Assynco : identité + synthèse assurance.

    Données LIVE Airtable (base courtier Assynco), distinctes du RAG documentaire.
    Pour les détails des contrats, enchaîner sur PALIM_assynco_list_polices.

    Args:
        code_ncg: Code NCG de la copropriété (ex: "5390").

    Returns:
        {ok, code_ncg, copro} ; copro : nom, adresse, type_syndicat, nb_coproprietaires,
        descriptif (surface, bâtiments, ascenseurs, chauffage), total_prime, prime_mri,
        total_sinistres, nb_polices_liees. {ok:false, NOT_FOUND} si copro absente d'Assynco.
    """
    t0 = time.time()
    tr = lf.start_trace("PALIM_assynco_get_copro", input={"code_ncg": code_ncg},
                        tags=["mcp", "assynco", "get_copro"])
    try:
        ok, code, err = _assynco_guard(code_ncg)
        if not ok:
            lf.update_trace(tr, output=err, metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return err
        try:
            copro = assynco.get_copro(code)
        except Exception as exc:
            lf.update_trace(tr, output={"error_type": "INTERNAL"},
                            metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return _internal_error("PALIM_assynco_get_copro", exc)
        if not copro:
            res = {"ok": False, "error_type": "NOT_FOUND",
                   "message": f"Aucune copropriété Assynco pour le code {code}."}
            lf.update_trace(tr, output=res, metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return res
        _log("PALIM_assynco_get_copro", code_ncg=code, latency_ms=int((time.time() - t0) * 1000))
        lf.update_trace(tr, output={"found": True, "nb_polices": copro.get("nb_polices_liees")},
                        metadata={"latency_ms": int((time.time() - t0) * 1000)})
        return {"ok": True, "code_ncg": code, "copro": copro}
    finally:
        lf.flush()


@mcp.tool()
def PALIM_assynco_list_polices(code_ncg: str, max_results: int = 20) -> dict:
    """Polices d'assurance souscrites d'une copropriété (Assynco, live).

    Garanties au niveau libellés + franchises + primes (cf. data-model R1). Les
    plafonds structurés par risque ne sont pas inclus (table Produit, hors R1).

    Args:
        code_ncg: Code NCG de la copropriété (ex: "5390").
        max_results: Nombre max de polices (plafonné serveur).

    Returns:
        {ok, code_ncg, n_results, polices[]} ; chaque police : numero_police,
        statut_contrat, garanties (libellés), franchise(s), prime_annuelle_ttc/ht,
        date_effet/resiliation, assureur, syndic, courtier.
    """
    t0 = time.time()
    tr = lf.start_trace("PALIM_assynco_list_polices", input={"code_ncg": code_ncg},
                        tags=["mcp", "assynco", "list_polices"])
    try:
        ok, code, err = _assynco_guard(code_ncg)
        if not ok:
            lf.update_trace(tr, output=err, metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return err
        max_results = _clamp(max_results, 20, cfg.ASSYNCO_MAX_RECORDS_CAP)
        try:
            polices = assynco.list_polices(code, max_records=max_results)
        except Exception as exc:
            lf.update_trace(tr, output={"error_type": "INTERNAL"},
                            metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return _internal_error("PALIM_assynco_list_polices", exc)
        warnings = [] if polices else [f"Aucune police trouvée pour {code} dans Assynco."]
        _log("PALIM_assynco_list_polices", code_ncg=code, n_results=len(polices),
             latency_ms=int((time.time() - t0) * 1000))
        lf.update_trace(tr, output={"n_results": len(polices)},
                        metadata={"latency_ms": int((time.time() - t0) * 1000)})
        return {"ok": True, "code_ncg": code, "n_results": len(polices),
                "warnings": warnings, "polices": polices}
    finally:
        lf.flush()


@mcp.tool()
def PALIM_assynco_search_sinistres(code_ncg: str, query: str | None = None,
                                   max_results: int = 20) -> dict:
    """Sinistres d'une copropriété dans l'ERP Assynco (live, plus riche que la table dossiers RAG).

    Args:
        code_ncg: Code NCG de la copropriété (ex: "5390").
        query: Filtre texte optionnel (insensible casse) sur le libellé du sinistre.
        max_results: Nombre max de sinistres (plafonné serveur).

    Returns:
        {ok, code_ncg, n_results, sinistres[]} ; chaque sinistre : nom, situation,
        date_survenance, lese_nom, cause, garantie_impactee, franchise, plafond,
        montants (estimation/cout_assureur/provisions/total_regle), pipeline 🚦,
        références (cie/expert/client), assureur, expert.
    """
    t0 = time.time()
    tr = lf.start_trace("PALIM_assynco_search_sinistres",
                        input={"code_ncg": code_ncg, "query": query},
                        tags=["mcp", "assynco", "search_sinistres"])
    try:
        ok, code, err = _assynco_guard(code_ncg)
        if not ok:
            lf.update_trace(tr, output=err, metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return err
        max_results = _clamp(max_results, 20, cfg.ASSYNCO_MAX_RECORDS_CAP)
        try:
            sinistres = assynco.search_sinistres(code, query=query, max_records=max_results)
        except Exception as exc:
            lf.update_trace(tr, output={"error_type": "INTERNAL"},
                            metadata={"latency_ms": int((time.time() - t0) * 1000)})
            return _internal_error("PALIM_assynco_search_sinistres", exc)
        warnings = [] if sinistres else [f"Aucun sinistre trouvé pour {code} dans Assynco."]
        _log("PALIM_assynco_search_sinistres", code_ncg=code, n_results=len(sinistres),
             query=bool(query), latency_ms=int((time.time() - t0) * 1000))
        lf.update_trace(tr, output={"n_results": len(sinistres)},
                        metadata={"latency_ms": int((time.time() - t0) * 1000)})
        return {"ok": True, "code_ncg": code, "n_results": len(sinistres),
                "warnings": warnings, "sinistres": sinistres}
    finally:
        lf.flush()


@mcp.tool()
def PALIM_log_feedback(
    rating: str,
    comment: str | None = None,
    question: str | None = None,
    copro_codes: list[str] | None = None,
    mode: str | None = None,
    utilisateur: str | None = None,
    trace_ref: str | None = None,
) -> dict:
    """Enregistre un retour utilisateur sur une réponse PALIM (observabilité pilote).

    N'appeler que pour un feedback PROFESSIONNEL sur une réponse métier non triviale
    (jamais sur du trivial ou du personnel). Si la réponse s'appuyait sur un
    PALIM_search_chunks / PALIM_search_dossiers, passer son `trace_ref` pour rattacher
    le feedback à la trace correspondante. Ne JAMAIS afficher `trace_ref` à l'utilisateur.

    Args:
        rating: "utile" ou "a_ameliorer".
        comment: Commentaire libre de l'utilisateur (optionnel mais précieux).
        question: Sujet ou question court (contexte).
        copro_codes: Codes NCG concernés (contexte).
        mode: Mot qualificatif du mode (ex: "factuel", "juridique", "rédaction", "synthèse-dossier").
        utilisateur: Prénom (minuscules, sans accent).
        trace_ref: Référence renvoyée par search_chunks/search_dossiers (optionnel, pour le rattachement).

    Returns:
        {ok, logged, linked}. logged=true si enregistré ; linked=true si rattaché à une trace existante.
    """
    t0 = time.time()
    r = (rating or "").strip().lower()
    value = 1.0 if r.startswith("util") else 0.0
    label = "utile" if value >= 1.0 else "a_ameliorer"
    c = (comment or "").strip()[:2000] or None
    if not r and not c:
        return {"ok": False, "logged": False, "linked": False, "error_type": "EMPTY_FEEDBACK",
                "message": "Feedback vide : fournir au moins rating ou comment."}
    codes = scope.normalize_copro_codes(copro_codes)
    context = {"question": (question or "").strip()[:500] or None,
               "copro_codes": codes or None, "mode": (mode or "").strip() or None,
               "rating": label}
    try:
        ok, linked = lf.log_feedback(value, comment=c, context=context,
                                     user=(utilisateur or "").strip().lower() or None,
                                     trace_ref=(trace_ref or "").strip() or None)
    except Exception as exc:
        return _internal_error("PALIM_log_feedback", exc)
    finally:
        lf.flush()
    _log("PALIM_log_feedback", rating=label, linked=linked, has_comment=bool(c),
         copro_codes=codes, mode=context["mode"], latency_ms=int((time.time() - t0) * 1000))
    return {"ok": True, "logged": bool(ok), "linked": bool(linked)}


# App ASGI pour uvicorn / Lambda Web Adapter
app = mcp.streamable_http_app()
