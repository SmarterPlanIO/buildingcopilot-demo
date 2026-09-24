"""v13 — contrats des trois correctifs issus du relevé client Delacour du 21/09/2026.

Sans DB ni réseau : les dépendances externes sont simulées.
Lance : python mcp_server/tests/test_palim_v13.py
"""
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import PALIM_assynco as A  # noqa: E402
import PALIM_config as cfg  # noqa: E402
import PALIM_copros as C  # noqa: E402

echecs = []


def check(cond, msg):
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        echecs.append(msg)


# ── v13-1 : list_copros ne renvoie plus le portefeuille entier quand rien ne matche ──
_ENTRIES = [
    {"code_ncg": "AH7171655", "nom_residence": "SDC 67 rue Escudier, 92100 Boulogne-Billancourt",
     "adresse": "SDC 67 rue Escudier, 92100 Boulogne-Billancourt", "immatriculation": "AH7171655"},
    {"code_ncg": "AJ6978050", "nom_residence": "SDC 9 rue Edmond Nocard, 94410 Saint-Maurice",
     "adresse": "SDC 9 rue Edmond Nocard, 94410 Saint-Maurice", "immatriculation": "AJ6978050"},
]


def _fake_db(monkey):
    """Remplace les accès DB de PALIM_copros par deux copros en dur."""
    monkey["stats"] = C._fetch_doc_stats
    monkey["chunks"] = C._fetch_chunk_counts
    monkey["dossiers"] = C._fetch_dossier_copros
    monkey["registry"] = C._fetch_registry
    C._fetch_doc_stats = lambda conn: {
        e["code_ncg"]: {"nom": "dossier drive brut", "nb_documents": 10, "doc_types": ["RCP", "PV_AG"],
                        "annee_min": 2020, "annee_max": 2026} for e in _ENTRIES}
    C._fetch_chunk_counts = lambda conn: {e["code_ncg"]: 100 for e in _ENTRIES}
    C._fetch_dossier_copros = lambda conn: set()
    C._fetch_registry = lambda conn: {e["code_ncg"]: e for e in _ENTRIES}


def _restore(monkey):
    C._fetch_doc_stats, C._fetch_chunk_counts = monkey["stats"], monkey["chunks"]
    C._fetch_dossier_copros, C._fetch_registry = monkey["dossiers"], monkey["registry"]


m = {}
_fake_db(m)
try:
    r = C.list_copros(None, "Escudier")
    check(r["n_results"] == 1 and "Escudier" in r["copros"][0]["nom"],
          "v13-1 une recherche qui matche renvoie la seule copro concernee")

    r = C.list_copros(None, "Neuilly-Plaisance")
    check(r["ok"] and r["copros"] == [] and r["n_results"] == 0,
          "v13-1 aucun match => liste VIDE (et non le portefeuille entier)")
    check(bool(r.get("warnings")) and "2" in r["warnings"][0],
          "v13-1 aucun match => avertissement citant le nombre de copros indexees")

    r = C.list_copros(None)
    check(r["n_results"] == 2, "v13-1 sans query => annuaire complet (inchange)")

    # le nom vient du registre, pas du dossier Drive
    check(all("drive brut" not in e["nom"] for e in C.list_copros(None)["copros"]),
          "v13-1 nom = copros.nom_residence quand il existe")
finally:
    _restore(m)


# ── v13-3 : Assynco absorbe les incidents transitoires, remonte les echecs repetes ──
class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"records": []}'


_orig = urllib.request.urlopen
cfg.ASSYNCO_HTTP_BACKOFF = 0.01  # test rapide
try:
    n = {"i": 0}

    def flaky(req, timeout=None):
        n["i"] += 1
        if n["i"] <= cfg.ASSYNCO_HTTP_RETRIES:
            raise TimeoutError("simulation")
        return _Resp()

    urllib.request.urlopen = flaky
    A._airtable_list(cfg.ASSYNCO_TABLE_COPRO, max_records=1)
    check(n["i"] == cfg.ASSYNCO_HTTP_RETRIES + 1,
          f"v13-3 {cfg.ASSYNCO_HTTP_RETRIES} timeout(s) absorbe(s), la requete aboutit")

    n["i"] = 0

    def always(req, timeout=None):
        n["i"] += 1
        raise TimeoutError("simulation")

    urllib.request.urlopen = always
    try:
        A._airtable_list(cfg.ASSYNCO_TABLE_COPRO, max_records=1)
        check(False, "v13-3 un echec repete doit remonter")
    except TimeoutError:
        check(n["i"] == cfg.ASSYNCO_HTTP_RETRIES + 1,
              "v13-3 echec repete : remonte apres 1 + retries tentatives")

    n["i"] = 0

    def http_404(req, timeout=None):
        n["i"] += 1
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    urllib.request.urlopen = http_404
    try:
        A._airtable_list(cfg.ASSYNCO_TABLE_COPRO, max_records=1)
        check(False, "v13-3 une erreur definitive doit remonter")
    except urllib.error.HTTPError:
        check(n["i"] == 1, "v13-3 erreur non transitoire (404) : aucune reprise inutile")
finally:
    urllib.request.urlopen = _orig

print("\nV13 TESTS PASSED" if not echecs else f"\n{len(echecs)} FAILED: {echecs}")
sys.exit(1 if echecs else 0)
