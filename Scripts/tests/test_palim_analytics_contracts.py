"""Tests contrats — PALIM_analytics (analytique inter-copro MCP).

Porte bloquante avant déploiement (cf. PLAN_ANALYTIQUE_INTER_COPRO.md §1.6) :
spec hors whitelist rejetée proprement ; injection inerte (les valeurs restent
en paramètres, jamais dans le SQL) ; count/sum/list sur les deux sources ;
copro_codes None / 1 / N ; cas spécial partie.

Exécution (pas besoin de pytest, aucune connexion DB) :
    cd Scripts && PYTHONIOENCODING=utf-8 python tests/test_palim_analytics_contracts.py
"""
import os
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SCRIPTS, "mcp_server"))
sys.path.insert(0, SCRIPTS)  # copro_id pour l'import dev de PALIM_scope/copros

from PALIM_analytics import (  # noqa: E402
    WHITELIST, _MAX_LIST_ROWS, build_analytical_sql, validate_spec,
)


def _spec(**kw):
    base = {"operation": None, "source": None, "select_field": None, "metric": None,
            "doc_type": None, "sous_type": None, "statut": None, "type_dossier": None,
            "annee": None, "annee_min": None, "annee_max": None}
    base.update(kw)
    return base


def test_rejets_whitelist():
    # source inconnue
    err = validate_spec(_spec(operation="count", source="prestataires"))
    assert err and err["error_type"] == "INVALID_ANALYTICAL_SPEC" and "sources" in err["allowed"]
    # operation inconnue
    err = validate_spec(_spec(operation="delete", source="documents"))
    assert err and err["error_type"] == "INVALID_ANALYTICAL_SPEC"
    # list sans select_field valide
    err = validate_spec(_spec(operation="list", source="dossiers", select_field="partie"))
    assert err and "list_fields" in err["allowed"]
    # sum sans metric valide (metric documents sur source dossiers)
    err = validate_spec(_spec(operation="sum", source="dossiers", metric="montant_principal"))
    assert err and "sum_metrics" in err["allowed"]
    # filtre inconnu pour la source (doc_type sur dossiers)
    err = validate_spec(_spec(operation="count", source="dossiers", doc_type="PV_AG"))
    assert err and "doc_type" in err["message"]
    print("OK rejets whitelist (5 cas, erreurs controlees avec valeurs admises)")


def test_injection_inerte():
    """Une valeur hostile reste un paramètre psycopg2 : jamais dans le texte SQL."""
    evil = "PLOMBERIE'; DROP TABLE chunks; --"
    sql, params = build_analytical_sql(
        _spec(operation="count", source="documents", sous_type=evil), None)
    assert evil.upper() in params           # la valeur part en paramètre (normalisée upper)
    assert "DROP" not in sql.upper()        # et n'apparaît jamais dans le SQL
    assert sql.count("%s") == len(params)
    # idem via copro_codes
    sql2, params2 = build_analytical_sql(
        _spec(operation="count", source="documents"), ["8050", "x; DELETE FROM documents"])
    assert "DELETE" not in sql2.upper() and "x; DELETE FROM documents" in params2
    print("OK injection inerte (valeurs en parametres, SQL fixe)")


def test_count_sum_list_deux_sources():
    # count documents, parc entier
    sql, params = build_analytical_sql(
        _spec(operation="count", source="documents", doc_type="pv_ag"), None)
    assert "GROUP BY code_ncg" in sql and params == ["PV_AG"]  # casse normalisée
    # sum dossiers, parc entier
    sql, params = build_analytical_sql(
        _spec(operation="sum", source="dossiers", metric="total_regle"), None)
    assert "SUM(total_regle)" in sql and "GROUP BY code_ncg" in sql
    # list documents (nom_fichier) filtré statut
    sql, params = build_analytical_sql(
        _spec(operation="list", source="documents", select_field="nom_fichier",
              doc_type="CONTRAT", sous_type="SYNDIC", statut="actif"), None)
    assert "DISTINCT" in sql and f"LIMIT {_MAX_LIST_ROWS}" in sql and len(params) == 3
    # list dossiers (assureur)
    sql, params = build_analytical_sql(
        _spec(operation="list", source="dossiers", select_field="assureur"), None)
    assert "assureur" in sql and "dossiers" in sql
    print("OK count/sum/list sur documents + dossiers")


def test_copro_codes_none_un_n():
    spec = _spec(operation="count", source="documents")
    sql_none, p_none = build_analytical_sql(spec, None)
    assert "code_ncg = " not in sql_none and "code_ncg IN" not in sql_none and p_none == []
    sql_un, p_un = build_analytical_sql(spec, ["8050"])
    assert "code_ncg = %s" in sql_un and p_un == ["8050"]
    sql_n, p_n = build_analytical_sql(spec, ["5412", "5709", "5757"])
    assert "code_ncg IN (%s,%s,%s)" in sql_n and p_n == ["5412", "5709", "5757"]
    print("OK copro_codes None / 1 / N")


def test_cas_special_partie():
    sql, params = build_analytical_sql(
        _spec(operation="list", source="documents", select_field="partie",
              annee_min=2023), None)
    assert "UNNEST(parties_concernees)" in sql and params == [2023]
    # partie n'existe pas côté dossiers (déjà rejeté par validate_spec, et par le builder)
    assert build_analytical_sql(
        _spec(operation="list", source="dossiers", select_field="partie"), None) is None
    print("OK cas special partie (UNNEST) + refus cote dossiers")


def test_parite_whitelist_streamlit():
    """La whitelist MCP doit rester identique à celle du harness Streamlit."""
    sys.path.insert(0, os.path.join(SCRIPTS, "Streamlit Cloud"))
    import analytics as st_analytics
    assert st_analytics.WHITELIST == WHITELIST, "whitelists divergentes MCP vs Streamlit"
    # même builder : 3 specs représentatives produisent le même SQL
    for spec, codes in [
        (_spec(operation="count", source="documents", sous_type="PLOMBERIE"), None),
        (_spec(operation="sum", source="dossiers", metric="total_regle"), ["8050"]),
        (_spec(operation="list", source="documents", select_field="partie"), ["5412", "5709"]),
    ]:
        assert st_analytics.build_analytical_sql(spec, codes) == build_analytical_sql(spec, codes)
    print("OK parite whitelist + builder avec Streamlit Cloud/analytics.py")


if __name__ == "__main__":
    test_rejets_whitelist()
    test_injection_inerte()
    test_count_sum_list_deux_sources()
    test_copro_codes_none_un_n()
    test_cas_special_partie()
    test_parite_whitelist_streamlit()
    print("\nTous les tests contrats PALIM_analytics passent.")
