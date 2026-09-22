"""Contrat du registre copros écrit par 06b (handoff 06B, C1) — sans DB.

Lance : python tests/test_06b_registre.py   (ou pytest tests/test_06b_registre.py)
"""
import os
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import pipeline_config as pcfg  # noqa: E402


def test_label_present_donne_nom_et_adresse():
    meta = {"folder": "SDC - 92100", "immatriculation": "AH7171655",
            "label": "SDC 67 rue Escudier, 92100 Boulogne-Billancourt"}
    assert pcfg.registre_row("AH7171655", meta) == (
        "AH7171655", "AH7171655",
        "SDC 67 rue Escudier, 92100 Boulogne-Billancourt",
        "SDC 67 rue Escudier, 92100 Boulogne-Billancourt")


def test_label_absent_retombe_sur_folder_sans_adresse():
    meta = {"folder": "5390 - 2-6 BIS HENRI TARIEL", "immatriculation": "AB8635559"}
    assert pcfg.registre_row("5390", meta) == (
        "5390", "AB8635559", "5390 - 2-6 BIS HENRI TARIEL", None)


def test_label_blanc_compte_comme_absent():
    meta = {"folder": "X", "label": "   ", "immatriculation": None}
    assert pcfg.registre_row("0001", meta) == ("0001", None, "X", None)


def test_profil_courant_jamais_sans_nom():
    # Sur le profil chargé (PALIM_CLIENT) : nom_residence jamais vide, code inchangé.
    for code in pcfg.COPRO_META:
        row = pcfg.registre_row(code)
        assert row[0] == code and row[2], row


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"{len(fns)} test(s) OK — client={pcfg.CLIENT_CODE}")
