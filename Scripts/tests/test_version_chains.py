"""Tests — version chains + variantes (soft delete, module version_chains).

Cas calqués sur des situations réelles des corpus (guide Section 22) :
la chaîne v1/v2 du PV AG 08/07/2021 du 100 Victor Hugo, les paires
BU_xxx / BU_xxx_RGPD du 94 Victor Hugo, les publipostages de Félix Faure.

Exécution (pas besoin de pytest) :
    cd Scripts && PYTHONIOENCODING=utf-8 python tests/test_version_chains.py
"""
import os
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from dedup_confirm import profil  # noqa: E402
from dedup_rules import DEFAULTS, compile_rules, load_rules  # noqa: E402
from version_chains import DocVC, detecter, stem  # noqa: E402

RULES = compile_rules({**DEFAULTS, "variant_suffixes": ["_RGPD"]})

CORPS = " ".join(f"resolution{i % 89}terme{i}" for i in range(600))


def doc(nom, texte, dossier="Gestion\\AG", sig=None):
    return DocVC(source_file=f"{dossier}\\{nom}", nom_fichier=nom,
                 dossier_parent=dossier, profil=profil(nom, texte), signature=sig)


def cas_stem():
    """La racine de nom ignore versions, copies, dates et suffixe de variante."""
    attendu = "pv ag sdc victor hugo"
    for nom in ("PV AG SDC VICTOR HUGO v1.pdf", "PV AG SDC VICTOR HUGO v2 (3).pdf",
                "pv ag sdc victor hugo 08 07 2021 VF.pdf", "PV AG SDC VICTOR HUGO_RGPD.docx",
                "PV AG SDC Victor Hugo - Copie.pdf"):
        assert stem(nom, RULES) == attendu, (nom, stem(nom, RULES))
    # deux documents différents ne partagent pas leur stem
    assert stem("Contrat ascenseur OTIS.pdf", RULES) != attendu
    return "stem : versions/copies/dates/variantes neutralisées"


def cas_chaine_v1_v2():
    """Chaîne v1 -> v1bis -> v2 du même PV : v2 = référence, les v1 flaggées.

    Réel (AE3410578) : 3 fichiers 'SDC 100 VICTOR HUGO ... v1/v1/v2 PV AG du
    08 07 2021', containment mesuré 0.94-0.99.
    """
    date = " assemblee generale du 08/07/2021 "
    v1a = doc("2190 SDC 100 VICTOR HUGO v1 PV AG du 08 07 2021.pdf", CORPS[:6000] + date)
    v1b = doc("2190 SDC 100 VICTOR HUGO v1 PV AG du 08 07 2021 (2).pdf", CORPS[:6200] + date)
    v2 = doc("2190 SDC 100 VICTOR HUGO v2 PV AG du 08 07 2021.pdf", CORPS[:6500] + date + " cloture")
    flags = detecter([v1a, v1b, v2], RULES)
    assert v2.source_file not in flags, flags
    assert flags[v1a.source_file]["motif"] == "VERSION_ANTERIEURE"
    assert flags[v1b.source_file]["ref_source_file"] == v2.source_file
    return "chaine v1/v2 -> v2 reference, v1 flaggees"


def cas_signe_prioritaire():
    """Le PV signé fait foi (décision Thai 20/08) : il gagne même plus court."""
    date = " assemblee du 29/01/2021 "
    long_ = doc("PV AG residence Aboukir projet.pdf", CORPS + date + " annexes volumineuses " + CORPS[:3000])
    signe = doc("PV AG residence Aboukir signe.pdf", CORPS + date)
    flags = detecter([long_, signe], RULES)
    assert signe.source_file not in flags, flags
    assert flags[long_.source_file]["ref_source_file"] == signe.source_file
    return "exemplaire signe = reference, meme plus court"


def cas_variante_rgpd():
    """BU_20240301.pdf / BU_20240301_RGPD.pdf : la variante est flaggée VARIANTE."""
    orig = doc("BU_20240301.pdf", CORPS[:5000] + " M. Dupont gardien salaire 1822,74")
    rgpd = doc("BU_20240301_RGPD.pdf", CORPS[:5000])
    flags = detecter([orig, rgpd], RULES)
    assert orig.source_file not in flags, flags
    assert flags[rgpd.source_file]["motif"] == "VARIANTE"
    # et variant_keep="variant" inverse le choix
    inv = compile_rules({**DEFAULTS, "variant_suffixes": ["_RGPD"], "variant_keep": "variant"})
    flags2 = detecter([orig, rgpd], inv)
    assert flags2[orig.source_file]["motif"] == "VARIANTE" and rgpd.source_file not in flags2
    return "variante _RGPD flaggee (sens configurable par client)"


def cas_publipostage_intouchable():
    """15 exemplaires quasi identiques d'une convocation : famille > seuil, zéro flag."""
    docs = [doc(f"Lettre de convocation AG copropriétaire {i}.pdf",
                CORPS[:4000] + f" destinataire numero {i}") for i in range(15)]
    assert detecter(docs, RULES) == {}
    return "publipostage (famille de 15) -> aucun flag"


def cas_series_distinctes_preservees():
    """Même stem mais dates différentes et aucun marqueur : documents distincts.

    Deux convocations d'AG différentes ('Compte Rendu.pdf' réutilisé chaque
    année) ne doivent jamais être chaînées.
    """
    a = doc("Compte Rendu AG.pdf", CORPS[:5000] + " assemblee du 10/05/2016")
    b = doc("Compte Rendu AG - Copie.pdf", CORPS[:5000] + " assemblee du 11/05/2017")
    assert detecter([a, b], RULES) == {}
    # mais avec les MÊMES dates (brouillon resauvegardé), le flag tombe
    c = doc("Compte Rendu AG - Copie.pdf", CORPS[:4800] + " assemblee du 10/05/2016")
    flags = detecter([a, c], RULES)
    assert flags and flags[c.source_file]["motif"] == "VERSION_ANTERIEURE"
    return "series a dates differentes preservees ; memes dates -> flag"


def cas_fond_different_preserve():
    """Même stem, mêmes dates, mais contenus différents : pas une version chain.

    Les clés de répartition 'CLE CHARGES BAT A..E' partagent stem et dates mais
    portent des tantièmes différents (containment < seuil) : intouchables.
    """
    a = doc("CLE CHARGES BAT.pdf", " ".join(f"tantiemes batA {i}" for i in range(500)) + " du 01/01/2024")
    b = doc("CLE CHARGES BAT (2).pdf", " ".join(f"tantiemes batC {i + 900}" for i in range(500)) + " du 01/01/2024")
    assert detecter([a, b], RULES) == {}
    return "fond different (containment faible) -> aucun flag"


def cas_regles_client():
    """Le profil Delacour charge, valide ses clés, et surcharge sans fusionner."""
    r = load_rules(os.path.join(SCRIPTS, "clients", "delacour"))
    assert "_RGPD" in r["variant_suffixes"] and r["variant_keep"] == "original"
    assert r["publipostage_min_famille"] == DEFAULTS["publipostage_min_famille"]  # non surchargé
    defaut = load_rules(os.path.join(SCRIPTS, "clients", "ncg"))                  # pas de fichier
    assert defaut["variant_suffixes"] == []
    try:
        load_rules.__wrapped__  # jamais défini : juste pour le style pytest-free
    except AttributeError:
        pass
    return "profil client : chargement, validation, override partiel"


def main():
    cas = [cas_stem, cas_chaine_v1_v2, cas_signe_prioritaire, cas_variante_rgpd,
           cas_publipostage_intouchable, cas_series_distinctes_preservees,
           cas_fond_different_preserve, cas_regles_client]
    for f in cas:
        print(f"  OK  {f():<62} [{f.__name__}]")
    print(f"\n{len(cas)}/{len(cas)} cas passes.")


if __name__ == "__main__":
    main()
