"""Noms de copro servis depuis le registre (copros.nom_residence) par la fiche,
l'analytique, la découverte et le document complet — suite du relevé Delacour du
21/09/2026 (la fiche d'Escudier affichait encore « SDC - 92100 » après v13).

Sans DB ni réseau : les accès base sont simulés.
Lance : python mcp_server/tests/test_palim_noms_registre.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(_HERE), os.path.dirname(os.path.dirname(_HERE))]

import PALIM_analytics as A  # noqa: E402
import PALIM_copros as C  # noqa: E402
import PALIM_overview as O  # noqa: E402

echecs = []


def check(cond, msg):
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        echecs.append(msg)


ESC, NOC = "AH7171655", "AJ6978050"
NOM_ESC = "SDC 67 rue Escudier, 92100 Boulogne-Billancourt"


class _Cur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if self.conn.fail:
            raise RuntimeError("relation \"copros\" does not exist")
        self.conn.queue = list(self.conn.results.pop(0)) if self.conn.results else []

    def fetchall(self):
        return self.conn.queue

    def fetchone(self):
        return self.conn.queue[0] if self.conn.queue else None


class _Conn:
    def __init__(self, results=None, fail=False):
        self.results, self.fail, self.rolled_back, self.queue = list(results or []), fail, False, []

    def cursor(self):
        return _Cur(self)

    def rollback(self):
        self.rolled_back = True


# ── registry_names ──
check(C.registry_names(_Conn([[(ESC, NOM_ESC)]])) == {ESC: NOM_ESC},
      "registry_names renvoie {code: nom_residence}")
c = _Conn(fail=True)
check(C.registry_names(c) == {} and c.rolled_back,
      "registre absent (tenant non migre) => {} et rollback, jamais d'exception")

_orig = C.registry_names
C.registry_names = lambda conn: {ESC: NOM_ESC}
try:
    # ── analytique : copro_nom depuis le registre, repli sur le dossier sinon ──
    conn = _Conn([[(ESC, "SDC - 92100", 12), (NOC, "SDC 9 rue Edmond Nocard - 94410", 3)], [(30,)]])
    r = A.run_analytical_query(conn, {"source": "documents", "operation": "count"})
    noms = {x["code_ncg"]: x["copro_nom"] for x in r.get("rows", [])}
    check(noms.get(ESC) == NOM_ESC, "analytique : nom du registre quand il existe")
    check(noms.get(NOC) == "SDC 9 rue Edmond Nocard - 94410",
          "analytique : repli sur le nom du dossier quand le registre est muet")

    # ── fiche : nom de tete ET identite.nom de la fiche v2 ──
    saved = O._fetch_immatriculation, O._fetch_fiche_v2, O._collect_live
    O._fetch_immatriculation = lambda conn, code: code
    O._fetch_fiche_v2 = lambda conn, code: ({"identite": {"nom": "SDC - 92100"}}, "v2", None)
    O._collect_live = lambda conn, code: ({}, {"nom": "SDC - 92100", "nb_documents": 1,
                                               "nb_dossiers": 0, "nb_sinistres_assynco": 0,
                                               "dernier_pv_date": None})
    try:
        row = ("SDC - 92100", None, None, 1, 1, 0, 0, None, None, None, None, None)
        o = O.get_overview(_Conn([[row]]), ESC)
        check(o["nom"] == NOM_ESC, "fiche : nom de tete depuis le registre")
        check(o["fiche"]["identite"]["nom"] == NOM_ESC,
              "fiche : identite.nom depuis le registre (et non le nom fige au calcul)")
        o = O.get_overview(_Conn([[row]]), NOC)
        check(o["nom"] == "SDC - 92100" and o["fiche"]["identite"]["nom"] == "SDC - 92100",
              "fiche : registre muet => nom stocke inchange")
    finally:
        O._fetch_immatriculation, O._fetch_fiche_v2, O._collect_live = saved
finally:
    C.registry_names = _orig

if echecs:
    print(f"\n{len(echecs)} ECHEC(S)")
    sys.exit(1)
print("\nNOMS REGISTRE TESTS PASSED")
