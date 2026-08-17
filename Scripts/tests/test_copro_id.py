"""Tests P1 — copro_id + résolution des codes dans pipeline_config (2 profils).

Exécution (pas besoin de pytest) :
    cd Scripts && PYTHONIOENCODING=utf-8 python tests/test_copro_id.py
"""
import os
import subprocess
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from copro_id import canon, display, is_immatriculation  # noqa: E402


def test_copro_id():
    # canon : toutes graphies humaines -> forme canonique
    for raw in ("AE3410578", "AE3-410-578", "ae3 410 578", " ae3.410.578 ", "AE3-410 578"):
        assert canon(raw) == "AE3410578", raw
    assert canon("8050") == "8050"          # NCG numérique : neutre
    assert canon("0200") == "0200"          # zéro de tête préservé
    assert canon(None) == "" and canon("") == ""
    # validation : stricte pour les immatriculations seulement
    assert is_immatriculation("ae3-410-578")
    assert is_immatriculation("AA6219950")
    assert not is_immatriculation("8050")           # numérique court = autre régime
    assert not is_immatriculation("AE341057")       # 8 caractères
    assert not is_immatriculation("AEX410578A")     # format invalide
    # affichage humain
    assert display("ae3410578") == "AE3-410-578"
    assert display("8050") == "8050"
    print("OK copro_id (canon / is_immatriculation / display)")


def _run_in_profile(client, code):
    """Exécute `code` python dans un sous-processus avec PALIM_CLIENT=client."""
    env = dict(os.environ, PALIM_CLIENT=client, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=SCRIPTS,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"[{client}] {r.stderr[-800:]}")
    return r.stdout


def test_profile_delacour():
    out = _run_in_profile("delacour", r"""
import pipeline_config as p
assert len(p.INCLUDED_COPROS) == 25, len(p.INCLUDED_COPROS)
assert all(len(c) == 9 for c in p.INCLUDED_COPROS), "cles = immatriculations canoniques"
# resolution : graphies humaines
assert p.resolve("AE3-410-578") == "AE3410578"
assert p.resolve("ae3 410 578") == "AE3410578"
# resolution : alias codes Lobby
assert p.resolve("0200") == "AA6219950"
assert p.resolve("0179") == "AC9872896"
# alias = forme exacte du code Lobby (zero de tete inclus) : "179" est inconnu
try:
    p.resolve("179"); assert False
except ValueError:
    pass
# paths : un seul shard par copro quel que soit le code d'entree
a = p.paths_for("0200"); b = p.paths_for("AA6219950"); c = p.paths_for("aa6-219-950")
assert a["code"] == b["code"] == c["code"] == "AA6219950"
assert a["per_copro"] == b["per_copro"] == c["per_copro"]
assert p.folder_for("0200") == "SDC 50 rue Vaneau - 75007"
assert p.COPRO_META["AH7171655"]["folder"] == "SDC - 92100"
# code inconnu -> erreur listant les codes valides
try:
    p.resolve("9999"); assert False
except ValueError as e:
    assert "AA6219950" in str(e)
print("delacour ok")
""")
    assert "delacour ok" in out
    print("OK profil delacour (25 immatriculations, alias Lobby, graphies, shard unique)")


def test_profile_ncg_regression():
    out = _run_in_profile("ncg", r"""
import pipeline_config as p
assert set(p.INCLUDED_COPROS) == {"5033","5354","5390","5427","5480","5499","5548","5553","8030","8050"}
assert p.INCLUDED_COPROS["8050"] == "8050 - STYLE - 145 AVENUE DE FRANCE"
assert p.resolve("8050") == "8050"
pa = p.paths_for("8050")
assert pa["code"] == "8050" and str(pa["per_copro"]).endswith("8050")
try:
    p.resolve("0000"); assert False
except ValueError:
    pass
print("ncg ok")
""")
    assert "ncg ok" in out
    print("OK profil ncg (regression : rien ne change)")


if __name__ == "__main__":
    test_copro_id()
    test_profile_delacour()
    test_profile_ncg_regression()
    print("\nTous les tests P1 passent.")
