"""
PALIM_analytics.py — Analytique inter-copro (agrégations SQL, parc entier autorisé).

Copie maîtrisée de `Streamlit Cloud/analytics.py` (même approche que PALIM_retrieval
vs streamlit_app) : la WHITELIST et le builder SQL paramétré sont repris à
l'identique ; le routeur Haiku (detect_analytical_query) et le formatage LLM
(run_analytical_route) ne sont PAS repris — en MCP, c'est le LLM client qui
construit la spec en arguments du tool et qui met en forme les rows bruts.
Zéro appel Bedrock ici : SQL pur, read-only (mcp_ncg_reader).

Principe de sécurité : le LLM ne fournit que des VALEURS ; colonnes, opérateurs
et structure du SQL sortent exclusivement de la liste blanche, les valeurs
passent en paramètres psycopg2. Toute spec hors whitelist → erreur contrôlée
INVALID_ANALYTICAL_SPEC avec le rappel des valeurs admises (le LLM se corrige).

Exemption de scope : contrairement au retrieval documentaire, copro_codes=None
est LÉGITIME (parc entier). La traçabilité est garantie par construction :
chaque ligne d'agrégat reste rattachée à son code_ncg (GROUP BY).
"""
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, Union

# ──────────────────────────────────────────────────────────────
# LISTE BLANCHE — identique à Streamlit Cloud/analytics.py (source de vérité
# du contrat ; toute évolution se fait DES DEUX CÔTÉS).
# ──────────────────────────────────────────────────────────────
WHITELIST: Dict[str, Dict[str, Any]] = {
    "documents": {
        "table": "documents",
        "filters": {
            "doc_type":   ("COALESCE(doc_type_corrige, doc_type)", "="),
            "sous_type":  ("sous_type", "="),
            "statut":     ("statut", "="),
            "annee":      ("annee", "="),
            "annee_min":  ("annee", ">="),
            "annee_max":  ("annee", "<="),
        },
        "list_fields": {
            "nom_fichier": "nom_fichier",
            "sous_type":   "sous_type",
            "doc_type":    "COALESCE(doc_type_corrige, doc_type)",
            "partie":      "__UNNEST_PARTIES__",  # cas spécial (UNNEST parties_concernees)
        },
        "sum_metrics": {
            "montant_principal": "montant_principal",
        },
    },
    "dossiers": {
        "table": "dossiers",
        "filters": {
            "type_dossier": ("type_dossier", "="),
            "statut":       ("statut", "="),
            "annee":        ("EXTRACT(YEAR FROM date_ouverture)::int", "="),
            "annee_min":    ("EXTRACT(YEAR FROM date_ouverture)::int", ">="),
            "annee_max":    ("EXTRACT(YEAR FROM date_ouverture)::int", "<="),
        },
        "list_fields": {
            "nom_dossier":  "nom_dossier",
            "type_dossier": "type_dossier",
            "assureur":     "assureur",
            "expert_nom":   "expert_nom",
        },
        "sum_metrics": {
            "montant_estime":    "montant_estime",
            "montant_reel":      "montant_reel",
            "total_regle":       "total_regle",
            "provisions":        "provisions",
            "franchise":         "franchise",
            "reglement_realise": "reglement_realise",
            "cout_client":       "cout_client",
        },
    },
}

_MAX_LIST_ROWS = 5000      # garde-fou SQL sur les listes
_MAX_ROWS_RETURNED = 300   # lignes max renvoyées dans la réponse MCP

# Filtres communs de la spec qui ne sont pas des colonnes whitelistées
_SPEC_META_KEYS = {"operation", "source", "select_field", "metric"}


def _allowed(source_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Valeurs admises pour un message d'erreur auto-correctif."""
    return {
        "operations": ["count", "sum", "list"],
        "sources": sorted(WHITELIST),
        "filters": sorted(source_cfg["filters"]) if source_cfg else [],
        "list_fields": sorted(source_cfg["list_fields"]) if source_cfg else [],
        "sum_metrics": sorted(source_cfg["sum_metrics"]) if source_cfg else [],
    }


def validate_spec(spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Valide la spec contre la whitelist. Retourne None si OK, sinon un dict
    d'erreur contrôlée {ok:False, error_type:"INVALID_ANALYTICAL_SPEC", ...}."""
    source = spec.get("source")
    cfg = WHITELIST.get(source)
    if not cfg:
        return {"ok": False, "error_type": "INVALID_ANALYTICAL_SPEC",
                "message": f"source inconnue : {source!r}.",
                "allowed": _allowed(None) | {"sources": sorted(WHITELIST)}}

    op = spec.get("operation")
    if op not in ("count", "sum", "list"):
        return {"ok": False, "error_type": "INVALID_ANALYTICAL_SPEC",
                "message": f"operation inconnue : {op!r}.", "allowed": _allowed(cfg)}

    if op == "list":
        if spec.get("select_field") not in cfg["list_fields"]:
            return {"ok": False, "error_type": "INVALID_ANALYTICAL_SPEC",
                    "message": f"select_field {spec.get('select_field')!r} invalide pour "
                               f"source={source} (requis quand operation=list).",
                    "allowed": _allowed(cfg)}
    if op == "sum":
        if spec.get("metric") not in cfg["sum_metrics"]:
            return {"ok": False, "error_type": "INVALID_ANALYTICAL_SPEC",
                    "message": f"metric {spec.get('metric')!r} invalide pour "
                               f"source={source} (requis quand operation=sum).",
                    "allowed": _allowed(cfg)}

    # Filtres fournis mais inconnus de CETTE source (ex. doc_type sur dossiers)
    unknown = [k for k, v in spec.items()
               if v is not None and k not in _SPEC_META_KEYS and k not in cfg["filters"]]
    if unknown:
        return {"ok": False, "error_type": "INVALID_ANALYTICAL_SPEC",
                "message": f"filtre(s) non supporté(s) pour source={source} : {sorted(unknown)}.",
                "allowed": _allowed(cfg)}
    return None


def build_analytical_sql(spec: Dict[str, Any],
                         copro_filter: Optional[Union[str, List[str]]]) -> Optional[Tuple[str, list]]:
    """Traduit une spec validée en (sql, params). Retourne None si la spec n'est
    pas traduisible (source/opération/champ hors liste blanche).
    Identique au builder de Streamlit Cloud/analytics.py."""
    source = spec.get("source")
    cfg = WHITELIST.get(source)
    if not cfg:
        return None
    op = spec.get("operation")
    if op not in ("list", "count", "sum"):
        return None

    table = cfg["table"]
    where, params = [], []

    for key, (expr, oper) in cfg["filters"].items():
        val = spec.get(key)
        if val is None or val == "null":
            continue
        # La DB stocke ces champs en MAJUSCULES → normaliser la casse
        if key in ("sous_type", "doc_type", "type_dossier") and isinstance(val, str):
            val = val.upper()
        where.append(f"{expr} {oper} %s")
        params.append(val)

    if copro_filter:
        _codes = [copro_filter] if isinstance(copro_filter, str) else [c for c in copro_filter if c]
        if len(_codes) == 1:
            where.append("code_ncg = %s")
            params.append(_codes[0])
        elif _codes:
            where.append("code_ncg IN (" + ",".join(["%s"] * len(_codes)) + ")")
            params.extend(_codes)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    if op == "count":
        sql = (f"SELECT code_ncg, MIN(copropriete) AS copro_nom, COUNT(*) AS valeur "
               f"FROM {table} {where_sql} GROUP BY code_ncg ORDER BY code_ncg")
        return sql, params

    if op == "sum":
        mcol = cfg["sum_metrics"].get(spec.get("metric"))
        if not mcol:
            return None
        sql = (f"SELECT code_ncg, MIN(copropriete) AS copro_nom, "
               f"COALESCE(SUM({mcol}), 0) AS valeur "
               f"FROM {table} {where_sql} GROUP BY code_ncg ORDER BY code_ncg")
        return sql, params

    # op == "list"
    field = spec.get("select_field")
    if field == "partie":
        if source != "documents":
            return None
        wl = list(where) + ["p IS NOT NULL", "p <> ''"]
        wsql = "WHERE " + " AND ".join(wl)
        sql = (f"SELECT DISTINCT code_ncg, copropriete AS copro_nom, p AS valeur "
               f"FROM documents, UNNEST(parties_concernees) AS p {wsql} "
               f"ORDER BY code_ncg, valeur LIMIT {_MAX_LIST_ROWS}")
        return sql, params

    fexpr = cfg["list_fields"].get(field)
    if not fexpr or fexpr == "__UNNEST_PARTIES__":
        return None
    wl = list(where) + [f"{fexpr} IS NOT NULL"]
    wsql = "WHERE " + " AND ".join(wl)
    sql = (f"SELECT DISTINCT code_ncg, copropriete AS copro_nom, {fexpr} AS valeur "
           f"FROM {table} {wsql} ORDER BY code_ncg, valeur LIMIT {_MAX_LIST_ROWS}")
    return sql, params


def _concentration(rows: List[tuple], operation: str) -> Optional[str]:
    """Part cumulée du top-N (count/sum uniquement) — donne à l'agent la phrase
    « N copros concentrent X% du total » sans round-trip supplémentaire."""
    if operation not in ("count", "sum") or len(rows) < 4:
        return None
    try:
        vals = sorted((float(r[2] or 0) for r in rows), reverse=True)
    except (TypeError, ValueError):
        return None
    total = sum(vals)
    if total <= 0:
        return None
    cum, n = 0.0, 0
    for v in vals:
        cum += v
        n += 1
        if cum / total >= 0.75:
            break
    if n >= len(rows):  # pas de concentration notable
        return None
    return f"les {n} premières copros portent {round(100 * cum / total)}% du total"


def _refine_suggestions(spec: Dict[str, Any], copro_filter) -> List[str]:
    cfg = WHITELIST[spec["source"]]
    sugg = [k for k in ("annee_min", "annee_max", "sous_type", "doc_type",
                        "type_dossier", "statut")
            if k in cfg["filters"] and spec.get(k) is None]
    if not copro_filter:
        sugg.append("copro_codes")
    return sugg


def run_analytical_query(conn, spec: Dict[str, Any],
                         copro_filter: Optional[Union[str, List[str]]] = None) -> Dict[str, Any]:
    """Valide, construit, exécute. Retourne le payload structuré du tool :
    {ok, rows[], n_rows, truncated, coverage, facets} ou une erreur contrôlée.
    Read-only ; rollback systématique sur erreur SQL (connexion réutilisable)."""
    err = validate_spec(spec)
    if err:
        return err
    built = build_analytical_sql(spec, copro_filter)
    if built is None:  # ceinture+bretelles : validate_spec couvre déjà ces cas
        return {"ok": False, "error_type": "INVALID_ANALYTICAL_SPEC",
                "message": "spec non traduisible.", "allowed": _allowed(WHITELIST[spec["source"]])}
    sql, params = built

    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            # Couverture : nb de copros ingérées (référentiel = documents)
            cur.execute("SELECT COUNT(DISTINCT code_ncg) FROM documents WHERE code_ncg IS NOT NULL")
            n_base = int(cur.fetchone()[0] or 0)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise

    copros_avec_donnees = {r[0] for r in rows if r[0]}
    out_rows = [{"code_ncg": r[0], "copro_nom": r[1],
                 "valeur": float(r[2]) if isinstance(r[2], Decimal) else r[2]}
                for r in rows[:_MAX_ROWS_RETURNED]]
    truncated = len(rows) > _MAX_ROWS_RETURNED

    facets: Dict[str, Any] = {"refine_suggestions": _refine_suggestions(spec, copro_filter)}
    conc = _concentration(rows, spec["operation"])
    if conc:
        facets["concentration"] = conc

    return {
        "ok": True,
        "rows": out_rows,
        "n_rows": len(rows),
        "truncated": truncated,
        "coverage": {
            "n_copros_avec_donnees": len(copros_avec_donnees),
            "n_copros_en_base": n_base,
            "note": ("les copros absentes du résultat n'ont aucune donnée matchant ces filtres ; "
                     "annoncer cette couverture dans la réponse"),
        },
        "facets": facets,
    }
