"""Tests de la gate de qualite de couche texte (fix cas 320, cf. ocr_quality.py)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import ocr_quality  # noqa: E402

# Extrait REEL de la couche embarquee pourrie du cas prouve (320-PV AG OCT 2024)
GARBAGE = (
    "'l:,r: l.',, il',, ]11 i.'l'': i,,r'i,lr'r' r i lr| itii .r tr iri\n"
    "Le 1Z octobre 2024 a 18:00:00, les membres du syndicat, 921 OO BOULOGNE.\n"
    "Rappel de I'article 1 7-1 A de la loi 65-557. Resultat selon les regles de l'arlicle24:\n"
    "Votantsu*, a\"t{.i*'i l{i{To$,ft T\" Totaux de la cleAbsents (ou non votant)\n"
    "BRAMAUD DU BOUCHERONER|K (194/1OOOO), Ft Jt (rgezlt Oooo), p ONS JEAU-e Ht Ltp PE\n"
    "choix de I'entre Prise. Honoraires du syndic pour la gestion linanciere aott2021\n"
) * 4

CLEAN = (
    "L'assemblée générale, après en avoir délibéré, autorise la société ZEPLUG SAS à "
    "effectuer l'installation, la gestion et l'entretien d'une infrastructure de recharge "
    "pour véhicules électriques et hybrides rechargeables, sans frais pour la copropriété, "
    "sans obligation d'abonnement pour les occupants, avec installation dès le premier "
    "utilisateur, dans le cadre d'une convention dont le modèle est joint au dossier. "
    "Le budget prévisionnel de l'exercice est approuvé à la somme de 25 000 euros, appelé "
    "par provisions trimestrielles égales, exigibles le premier jour de chaque trimestre. "
    "Résultat selon les règles de l'article 24 : sur 24 copropriétaires représentant "
    "10 000 tantièmes, 13 participants au vote totalisant 5 541 tantièmes ; la résolution "
    "est refusée par 13 voix contre, aucune abstention. Il est précisé que la résolution "
    "sera remise à l'ordre du jour de la prochaine assemblée générale. Les copropriétaires "
    "peuvent voter par correspondance avant la tenue de l'assemblée, au moyen du formulaire "
    "établi conformément au modèle fixé par arrêté. La séance est levée à vingt-deux heures "
    "dix, après épuisement de l'ordre du jour et signature de la feuille de présence par "
    "le président de séance, les scrutateurs et le secrétaire désignés en début de réunion."
)


def setup_function(_):
    ocr_quality._verdict_cache.clear()


def test_score_discrimine_les_deux_classes():
    assert ocr_quality.score_texte(CLEAN) < ocr_quality.SEUIL_PROPRE
    assert ocr_quality.score_texte(GARBAGE) > ocr_quality.SEUIL_PROPRE


def test_verdict_propre_sans_llm():
    v, score, methode = ocr_quality.verdict_couche(CLEAN)
    assert v == "PROPRE" and methode == "heuristique"


def test_verdict_sans_arbitre_privilegie_la_qualite():
    v, _, _ = ocr_quality.verdict_couche(GARBAGE, bedrock_factory=None)
    assert v == "DEGRADE"


# Tableau dense et SAIN (balance comptable) : le score heuristique le condamne
# (peu de mots-outils, tokens alphanumeriques colles) -> c'est la classe de faux
# positifs mesuree a 57 % le 27/08. L'arbitrage doit le sauver.
TABLE_SAINE = (
    "0001P001001M. AMZALLAG Jean Pierre2710 12 Rue Félicien David75016 PARIS 16P "
    "0002P001003M. AMZALLAG Jean-Pierre55 12 Rue Félicien David75016 PARIS 16P "
    "0003P006001M. ou Mme FOTSE-NJOMGANG/ SEGOUIN Benoit ou Emeline924 20 Rue Perier92120 MONTROUGEP "
    "0015P018004M. ou Mme RAVIZZA Guy1300 20 Rue Perier92120 MONTROUGEP "
    "Total des tantièmes12500 CodeNuméroNom CopropriétaireTantièmesAdresse1CP VillePaysT "
) * 3


def test_table_saine_score_haut_mais_pas_condamnee_sans_arbitrage():
    """Documente le faux positif : score eleve, mais l'heuristique seule ne doit
    pas etre la voie normale — la gate arbitre des que Bedrock est dispo."""
    assert ocr_quality.score_texte(TABLE_SAINE) >= ocr_quality.SEUIL_PROPRE
    calls = []

    def _factory():
        calls.append(1)
        raise ConnectionError("stub")  # force le fail-open, prouve l'appel

    ocr_quality.verdict_couche(TABLE_SAINE, bedrock_factory=_factory)
    assert calls, "un score au-dessus du seuil doit declencher l'arbitrage"


@pytest.mark.skipif(not os.environ.get("BEDROCK_LIVE"), reason="BEDROCK_LIVE=1 requis")
def test_table_saine_sauvee_par_haiku_live():
    import boto3
    factory = lambda: boto3.client("bedrock-runtime", region_name="eu-west-1")
    v, _, methode = ocr_quality.verdict_couche(TABLE_SAINE, bedrock_factory=factory)
    assert v == "PROPRE" and methode == "haiku"


def test_fail_open_si_bedrock_casse():
    def _factory():
        raise ConnectionError("bedrock indisponible")
    # zone grise forcee : texte intermediaire (melange 50/50)
    mixed = CLEAN[:1500] + GARBAGE[:1200]
    score = ocr_quality.score_texte(mixed)
    if not (ocr_quality.SEUIL_PROPRE <= score <= ocr_quality.SEUIL_DEGRADE):
        pytest.skip(f"melange hors zone grise (score {score})")
    v, _, methode = ocr_quality.verdict_couche(mixed, bedrock_factory=_factory)
    assert methode == "fail-open" and v == "PROPRE"


def test_memoisation():
    v1 = ocr_quality.verdict_couche(GARBAGE, cache_key="k1")
    v2 = ocr_quality.verdict_couche("autre texte totalement different", cache_key="k1")
    assert v1 == v2  # cache prime


@pytest.mark.skipif(not os.environ.get("BEDROCK_LIVE"), reason="BEDROCK_LIVE=1 requis")
def test_arbitrage_haiku_live():
    import boto3
    factory = lambda: boto3.client("bedrock-runtime", region_name="eu-west-1")
    assert ocr_quality._arbitrage_haiku(GARBAGE, factory()) == "DEGRADE"
    assert ocr_quality._arbitrage_haiku(CLEAN, factory()) == "PROPRE"


_PDF_320 = Path(r"G:/Mon Drive/Projet SmarterPlan/Sales/Prospects/Delacour Patrimoine/Sample Dataset/320-PV AG OCT 2024.pdf")


@pytest.mark.skipif(not (_PDF_320.exists() and os.environ.get("BEDROCK_LIVE")),
                    reason="PDF 320 + BEDROCK_LIVE requis")
def test_recette_cas_320_reroute_vers_textract():
    """Le cas prouve : avec la gate, extract_pdf_native ne prend plus la couche pourrie."""
    import importlib
    sys.argv = ["02_extraction_optimized.py"]
    m = importlib.import_module("02_extraction_optimized")
    text, is_native = m.extract_pdf_native(str(_PDF_320))
    assert is_native is False and text == ""
