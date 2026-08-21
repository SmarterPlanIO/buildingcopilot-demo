"""Tests — confirmation des doublons proches (dedup_confirm, étage 2 de 03).

Chaque cas reproduit une situation MESUREE sur les corpus NCG / Delacour le
20/08/2026. Les textes sont synthétiques mais les signatures (en-tête commun,
écart de dates, rapport de longueurs, Jaccard) reproduisent celles relevées.

Exécution (pas besoin de pytest) :
    cd Scripts && PYTHONIOENCODING=utf-8 python tests/test_dedup_confirm.py
"""
import os
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from dedup_confirm import (  # noqa: E402
    JACCARD_MIN, RATIO_LONGUEUR_MIN, confirmer, dates, jaccard, normaliser, profil, shingles)

# En-tête commun à tous les documents d'un même cabinet : c'est très exactement
# ce que voyait l'ancienne règle, et la raison pour laquelle elle se trompait.
ENTETE = (
    "CABINET DELACOUR PATRIMOINE\nADMINISTRATEUR DE BIENS\n"
    "SYNDICAT DES COPROPRIETAIRES\nIMMEUBLE 94 BOULEVARD VICTOR HUGO\n"
    "92200 NEUILLY SUR SEINE\nN/Ref : 28/AG7984\nPARIS, le siege social\n"
    "Societe par actions simplifiee au capital de 38 000 euros\n"
    "RCS Paris B 123 456 789 - Carte professionnelle CPI 7501 2018 000 012 345\n"
    "Garantie financiere QBE Insurance - Assurance RCP Allianz\n") * 2


def _corps(mots, graine):
    """Corps de document déterministe, sans recouvrement entre graines."""
    return " ".join(f"{graine}{i % 97}mot{i}" for i in range(mots))


def cas_pv_assemblees_distinctes():
    """Deux AG différentes du même immeuble : même en-tête, fond différent.

    C'est le faux doublon qui a coûté 5 087 documents. La tête les donne à 90 %
    de ressemblance ; le fond et les dates doivent les sauver.
    """
    a = profil("PV AG du 16 fevrier 2022.pdf",
               ENTETE + "ASSEMBLEE GENERALE DU 16/02/2022\n" + _corps(400, "resolutionA"))
    b = profil("PV AG du 02 juin 2022.pdf",
               ENTETE + "ASSEMBLEE GENERALE DU 02/06/2022\n" + _corps(400, "resolutionB"))
    v = confirmer(a, b)
    assert not v.doublon, v
    # Noms de fichier datés : le veto suffit, le fond n'est même pas consulté.
    assert v.motif == "DATES_NOM_DIFFERENTES", v

    # Mêmes documents, noms de fichier non datés (cas fréquent : "PV.pdf",
    # "PV (2).pdf") : le veto ne joue pas, c'est le fond qui doit les sauver.
    v2 = confirmer(profil("Compte rendu.pdf", a.norm),
                   profil("Compte rendu (2).pdf", b.norm))
    assert not v2.doublon, v2
    assert v2.motif == "JACCARD_INSUFFISANT", v2
    assert v2.score < JACCARD_MIN, v2
    return f"2 AG distinctes, meme en-tete -> conservees (veto, puis jaccard={v2.score:.2f})"


def cas_pv_signe_et_non_signe():
    """Même AG, deux exemplaires : signé et non signé. Vrai doublon.

    Relevé en vrai sur Aboukir : 'PV AG 29012021 signé' contre 'PV AG DU
    29012021', Jaccard 0.94, longueurs à 4 % près, mêmes dates.
    """
    corps = ENTETE + "ASSEMBLEE GENERALE DU 29/01/2021\n" + _corps(500, "resolution")
    a = profil("PV AG 29012021 signe 130 aboukir.pdf", corps + " signatures president scrutateurs")
    b = profil("PV AG DU 29012021 130 rue Aboukir.pdf", corps)
    v = confirmer(a, b)
    assert v.doublon, v
    assert v.score >= JACCARD_MIN, v
    return f"PV signe / non signe de la meme AG -> doublon (jaccard={v.score:.2f})"


def cas_bulletins_mensuels():
    """Documents périodiques de même gabarit : mois différents, texte à 92 %.

    52 paires de ce type dans l'échantillon Delacour ('BullStand_AAAAMMJJ.pdf'),
    toutes supprimées par l'ancienne règle. Le Jaccard ne les sauve pas, seul
    l'écart de dates le fait.
    """
    gabarit = ENTETE + "BULLETIN DE SITUATION MENSUEL\n" + _corps(300, "ligne")
    a = profil("BullStand_20250101.pdf", gabarit + "\nPeriode du 01/01/2025 au 31/01/2025")
    b = profil("BullStand_20250201.pdf", gabarit + "\nPeriode du 01/02/2025 au 28/02/2025")
    # Le fond EST quasi identique : c'est bien la similarite qui trompait l'ancienne regle.
    sim = jaccard(a.shingles, b.shingles)
    assert sim >= JACCARD_MIN, f"le gabarit doit etre tres similaire : {sim:.2f}"

    # Premier rempart : les dates portees par les noms de fichier.
    v = confirmer(a, b)
    assert not v.doublon and v.motif == "DATES_NOM_DIFFERENTES", v

    # Second rempart, quand les noms ne sont pas dates : les dates du texte.
    v2 = confirmer(profil("bulletin.pdf", a.norm + " periode du 01 01 2025 au 31 01 2025"),
                   profil("bulletin (2).pdf", b.norm + " periode du 01 02 2025 au 28 02 2025"))
    assert not v2.doublon, v2
    assert v2.motif == "DATES_TEXTE_DIFFERENTES", v2
    assert v2.score >= JACCARD_MIN, v2
    return f"bulletins mensuels (jaccard={sim:.2f}) -> conserves par les 2 remparts"


def cas_dates_seulement_dans_le_nom():
    """Le nom de fichier porte la date, le texte non. Piège du \\b.

    'BullStand_20250101.pdf' : \\b ne se déclenche pas entre '_' et '2', donc
    l'ancienne extraction ne voyait aucune date et le contrôle était neutralisé.
    """
    assert dates("BullStand_20250101.pdf") == frozenset({(2025, 1, 1)}), dates("BullStand_20250101.pdf")
    assert dates("rapport_20240312_final.pdf") == frozenset({(2024, 3, 12)})
    corps = ENTETE + "RELEVE DE COMPTE\n" + _corps(300, "operation")
    a = profil("releve_20250101.pdf", corps)
    b = profil("releve_20250201.pdf", corps + " ")
    v = confirmer(a, b)
    assert not v.doublon, v
    assert v.motif == "DATES_NOM_DIFFERENTES", v
    return "dates portees par le seul nom de fichier -> conserves"


def cas_texte_identique():
    """Deux exports du même document : texte identique après normalisation.

    Seul cas où la suppression ne peut rien coûter. Relevé sur les 35 pouvoirs
    de représentation de Felix Faure, au texte extrait strictement identique.
    """
    corps = ENTETE + "POUVOIR DE REPRESENTATION\n" + _corps(200, "clause")
    a = profil("Pouvoir de representation.pdf", corps)
    b = profil("Pouvoir de representation (12).pdf", corps.upper().replace("\n", "  ;  "))
    v = confirmer(a, b)
    assert v.doublon and v.motif == "TEXTE_IDENTIQUE", v
    assert v.score == 1.0, v
    return "texte identique a la casse/ponctuation pres -> doublon"


def cas_longueurs_ecartees():
    """Jaccard élevé mais un document nettement plus long : version enrichie.

    Un texte répétitif ajoute des caractères sans ajouter de n-grammes : le
    Jaccard reste haut alors que les documents ne sont plus le même exemplaire.
    Le garde-fou de longueur est là pour ça.
    """
    corps = ENTETE + "APPEL DE FONDS DU 12/03/2024\n" + _corps(400, "poste")
    a = profil("appel_12032024.pdf", corps)
    b = profil("appel_12032024.pdf", corps + ("\nrappel echeance impayee" * 400))
    v = confirmer(a, b)
    assert v.score >= JACCARD_MIN, f"le fond doit rester tres proche : {v}"
    ratio = min(a.longueur, b.longueur) / max(a.longueur, b.longueur)
    assert ratio < RATIO_LONGUEUR_MIN, ratio
    assert not v.doublon and v.motif == "LONGUEURS_ECARTEES", v
    return f"longueurs ecartees ({ratio:.0%}) malgre jaccard={v.score:.2f} -> conserves"


def cas_symetrie_et_bords():
    """Symétrie, textes courts, texte vide : aucun crash, aucune suppression."""
    corps = ENTETE + "NOTE DU 01/02/2023\n" + _corps(300, "point")
    a, b = profil("a.pdf", corps), profil("b.pdf", corps + " addendum")
    assert confirmer(a, b) == confirmer(b, a), "la regle doit etre symetrique"
    court = profil("court.pdf", "trois mots seulement")
    assert not confirmer(court, court).doublon, "texte trop court : jamais de suppression"
    assert confirmer(court, court).motif == "TEXTE_TROP_COURT"
    vide = profil("vide.pdf", "")
    assert not confirmer(vide, vide).doublon
    assert not confirmer(vide, a).doublon
    return "symetrie + textes courts/vides -> aucune suppression"


def cas_primitives():
    """Normalisation, n-grammes et Jaccard : comportement de base."""
    assert normaliser("Procès-Verbal  d'AG !") == "proces verbal d ag"
    assert normaliser("PROCES VERBAL D AG") == normaliser("Procès-verbal, d'AG.")
    assert shingles("un deux trois", n=5) == frozenset({"un deux trois"})  # texte < n mots
    assert len(shingles("a b c d e f", n=5)) == 2
    assert jaccard(frozenset(), frozenset({"x"})) == 0.0
    assert jaccard(frozenset({"x", "y"}), frozenset({"x", "y"})) == 1.0
    assert abs(jaccard(frozenset({"x", "y"}), frozenset({"y", "z"})) - 1 / 3) < 1e-9
    # trois écritures de la même date, un seul triplet
    for txt in ("12/03/2024", "20240312", "12 mars 2024", "12-03-24"):
        assert (2024, 3, 12) in dates(txt), txt
    assert dates("le 32/13/2024 n'existe pas") == frozenset()
    return "primitives (normalisation, shingles, jaccard, dates)"


def main():
    cas = [cas_primitives, cas_pv_assemblees_distinctes, cas_pv_signe_et_non_signe,
           cas_bulletins_mensuels, cas_dates_seulement_dans_le_nom, cas_texte_identique,
           cas_longueurs_ecartees, cas_symetrie_et_bords]
    for f in cas:
        print(f"  OK  {f():<70} [{f.__name__}]")
    print(f"\n{len(cas)}/{len(cas)} cas passes.")


if __name__ == "__main__":
    main()
