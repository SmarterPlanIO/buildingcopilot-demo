"""pv_exemplaires.selectionner_pv — un PV par AG dans la fiche, sans jamais masquer
une AG distincte. Cas réels : 45 rue de l'Alma (AD1265248), 2-2 bis av. du Stade de
Coubertin (AB8763831). Sans DB.

Lance : python tests/test_pv_exemplaires.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pv_exemplaires import selectionner_pv  # noqa: E402

echecs = []


def check(cond, msg):
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        echecs.append(msg)


# Objets tels qu'extraits : numérotation décalée et pipes selon l'exemplaire (cas Alma)
OBJ_PDF = ["Résolution 01 : Conformément à l'article 15 du décret du 17 mars 1967, Désignation",
           "Résolution 05 : Conformément à l'article 14-3 de la loi du 10 juillet 1965, Examen et",
           "Résolution 06 : Examen et approbation des comptes travaux clôturés : ''Rénovation"]
OBJ_DOC = ["|Résolution 01 : Conformément à l'article 15 du décret du 17 mars 1967, Désignation |",
           "|Résolution 06 : Conformément à l'article 14-3 de la loi du 10 juillet 1965, Examen |",
           "|Résolution 07 : Examen et approbation des comptes travaux clôturés : ''Rénovation |"]
OBJ_AGE = ["Résolution 01 : Autorisation de travaux de ravalement",
           "Résolution 02 : Choix de l'entreprise de ravalement"]

D1, D2, D3 = date(2026, 6, 29), date(2025, 5, 27), date(2024, 6, 20)
F = lambda sf, d, n, obj: {"source_file": sf, "date": d, "n_etablies": n, "objets": obj}  # noqa: E731

alma = [
    F("Alma/AG/29.06.2026/PV_SDC 45 RUE DE L'ALMA_20260629.doc", D1, 12, OBJ_DOC),
    F("Alma/AG/29.06.2026/PV_SDC_45_RUE_DE_LALMA_20260629 signé.pdf", D1, 12, OBJ_PDF),
    F("Alma/AG/27.05.2025/PV_SDC 45 RUE DE L'ALMA_20250527.doc", D2, 13, OBJ_DOC),
    F("Alma/PROCES-VERBAUX AG/PV_SDC_45_RUE_DE_L'ALMA_20250527.pdf", D2, 12, OBJ_PDF),
    F("Alma/AG/27.05.2025/PV_SDC 45 RUE DE L'ALMA_20250527.pdf", D2, 12, OBJ_PDF),
    F("Alma/451 PV AGA 20 06 2024.PDF", D3, 10, OBJ_PDF),
]
r = selectionner_pv(alma, 5)
check([p["date"] for p in r] == [D1, D2, D3], "une ligne par AG (6 fichiers, 3 AG)")
check(r[0]["source_file"].endswith("signé.pdf"), "exemplaire signe prefere au .doc")
check(r[1]["source_file"].endswith(".pdf") and len(r[1]["autres_exemplaires"]) == 2,
      "PDF prefere au .doc, les 2 autres exemplaires restent pointes")
check(selectionner_pv(list(reversed(alma)), 5) == r, "resultat independant de l'ordre d'entree")

meme_jour = [F("X/PV AGO 12 03 2025.pdf", D2, 8, OBJ_PDF), F("X/PV AGE 12 03 2025.pdf", D2, 2, OBJ_AGE)]
r = selectionner_pv(meme_jour, 5)
check(len(r) == 2 and all(p["autres_exemplaires"] == [] for p in r),
      "AGO et AGE le meme jour : deux PV distincts, aucun masque")

# Cas 60 bd Magenta : objets OCR sans recouvrement, mais même nom au « signé » près
magenta = [F("M/Procès verbal assemblée générale 14 mai 2024.pdf", D3, 6, ["eance levee a"]),
           F("M/ Procès verbal assemblée générale 14 mai 2024  signé.pdf", D3, 7, ["designation du bureau"])]
r = selectionner_pv(magenta, 5)
check(len(r) == 1 and r[0]["source_file"].endswith("signé.pdf"),
      "meme nom au « signe » pres => meme AG, version signee retenue")
autre = [F("X/PV AGO 12 03 2025.pdf", D2, 8, ["eance levee a"]), F("X/PV AGE 12 03 2025.pdf", D2, 2, ["x"])]
check(len(selectionner_pv(autre, 5)) == 2, "noms differents (AGO / AGE) sans recouvrement => deux PV")

r = selectionner_pv(alma, 2)
check(len(r) == 2 and [p["date"] for p in r] == [D1, D2], "limite respectee, dates les plus recentes")

if echecs:
    print(f"\n{len(echecs)} ECHEC(S)")
    sys.exit(1)
print("\nPV EXEMPLAIRES TESTS PASSED")
