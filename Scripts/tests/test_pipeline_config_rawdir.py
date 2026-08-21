"""Tests — raw_dir per-copro (lecture directe share VPN/disque externe, zéro recopie).

Exécution (pas besoin de pytest) :
    cd Scripts && PYTHONIOENCODING=utf-8 python tests/test_pipeline_config_rawdir.py
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import pipeline_config as pcfg  # noqa: E402


def test_regression_ncg_sans_raw_dir():
    """Les copros sans raw_dir gardent la résolution historique RAW_ROOT/folder."""
    p = pcfg.raw_source_dir("8050")
    assert p == pcfg.RAW_ROOT / pcfg.folder_for("8050"), p
    assert pcfg.paths_for("8050")["raw_source"] == p
    print("OK regression NCG (raw_source_dir = RAW_ROOT/folder)")


def test_raw_dir_absolu_honore():
    """raw_dir absolu (UNC) prime pour la LECTURE ; les sorties restent locales."""
    unc = r"\\192.192.192.15\Copropriete Entreprise\5412 - TOUR LYON BERCY"
    pcfg._COPROS["5412"] = {"folder": "5412 - TOUR LYON BERCY", "raw_dir": unc,
                            "immatriculation": None}
    try:
        assert pcfg.raw_source_dir("5412") == Path(unc)
        # Les sorties filtrées/extraites ne partent JAMAIS vers la source
        assert pcfg.filtered_dir("5412") == pcfg.FILTERED_ROOT / "5412 - TOUR LYON BERCY"
        assert pcfg.extracted_dir("5412") == pcfg.EXTRACTED_ROOT / "5412 - TOUR LYON BERCY"
        assert pcfg.paths_for("5412")["raw_source"] == Path(unc)
    finally:
        del pcfg._COPROS["5412"]
    print("OK raw_dir UNC honore en lecture, sorties locales inchangees")


def _load_profile_expect_fail(included, needle):
    """Charge un profil temporaire clients/_tmp_test/ et attend un SystemExit."""
    tmp = Path(SCRIPTS) / "clients" / "_tmp_test"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "client.json").write_text(json.dumps({
        "client_code": "_tmp_test", "client_name": "TMP", "project_root": None,
        "included_copros": included,
    }), encoding="utf-8")
    try:
        r = subprocess.run([sys.executable, "-c", "import pipeline_config"],
                           cwd=SCRIPTS, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PALIM_CLIENT": "_tmp_test",
                                "PYTHONIOENCODING": "utf-8"})
        assert r.returncode != 0, "le chargement aurait dû échouer"
        assert needle in (r.stdout + r.stderr), (r.stdout + r.stderr)[-400:]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_validations_fail_fast():
    # folder absolu → refus net (sinon Archives_Filtrees partirait vers la source)
    _load_profile_expect_fail(
        {"9999": {"folder": r"\\srv\part\9999 - X"}}, "doit être un nom RELATIF")
    # raw_dir relatif → refus net
    _load_profile_expect_fail(
        {"9999": {"folder": "9999 - X", "raw_dir": "Données brutes/9999"}},
        "doit être un chemin ABSOLU")
    print("OK validations fail-fast (folder absolu / raw_dir relatif rejetes)")


if __name__ == "__main__":
    test_regression_ncg_sans_raw_dir()
    test_raw_dir_absolu_honore()
    test_validations_fail_fast()
    print("\nTous les tests raw_dir passent.")
