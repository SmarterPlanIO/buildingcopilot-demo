"""
PALIM_dossiers.py — Recherche dossiers sinistres (wrap de
dossiers_api.search_dossiers_for_query, déjà conn-based).

Multi-copro : appel par code + équilibrage. Sans copro : découverte de
dossiers candidats (pas de dump complet).
"""
import os
import sys

# Import du module dossiers_api.py situé dans "Streamlit Cloud"
_SC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Streamlit Cloud")
if _SC_DIR not in sys.path:
    sys.path.insert(0, _SC_DIR)

try:
    from dossiers_api import (
        search_dossiers_for_query as _search_raw,
        list_dossiers_for_copro as _list_scoped,
    )
except Exception:  # packaging Lambda : dossiers_api vendorisé dans le même dossier
    from dossiers_api_vendored import (  # type: ignore
        search_dossiers_for_query as _search_raw,
        list_dossiers_for_copro as _list_scoped,
    )


def _project(d):
    """Projette un dict dossier complet sur les champs garantis du contrat."""
    return {
        "dossier_id": d.get("dossier_id"),
        "code_ncg": d.get("code_ncg"),
        "copropriete": d.get("copropriete"),
        "type": d.get("type_dossier"),
        "statut": d.get("at_situation") or d.get("statut"),
        "lese": d.get("lese_nom"),
        "montant": d.get("montant_reel") if d.get("montant_reel") is not None else d.get("montant_estime"),
        "source": "airtable" if d.get("airtable_record_id") else "rag",
    }


def search_dossiers(conn, query, copro_codes=None, max_results=20):
    """
    copro_codes vide/None -> découverte (toutes copros, recherche mots-clés, plafonné).
    Sinon -> énumération scopée : TOUS les dossiers des copros, rankés par query
    (pas d'exclusion sur les mots-clés -> jamais de faux négatif).

    Retourne (results, n_total) : results = liste de dicts projetés (<= max_results) ;
    n_total = nb total de dossiers des copros AVANT troncature (== len(results) en
    mode découverte, où le total exact n'est pas calculé).
    """
    codes = [c for c in (copro_codes or []) if c]
    if not codes:
        rows = _search_raw(conn, query, None)
        out = [_project(d) for d in rows[:max_results]]
        return out, len(out)

    rows, total = _list_scoped(conn, codes, query=query, max_results=max_results)
    return [_project(d) for d in rows], total
