"""Tests de l'assemblage de lignes preservant les colonnes (layout_text)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from layout_text import SEPARATEUR, assembler_ligne  # noqa: E402


def mots(*specs):
    """(texte, left, width) -> mots au format Textract normalise."""
    return [{"text": t, "left": l, "width": w} for t, l, w in specs]


def test_phrase_normale_sans_separateur():
    # mots espaces normalement : ~0,3 largeur de caractere entre eux
    ligne = mots(("L'assemblee", 0.10, 0.070), ("generale", 0.182, 0.052),
                 ("approuve", 0.240, 0.052), ("le", 0.298, 0.012),
                 ("budget", 0.316, 0.040))
    out = assembler_ligne(ligne)
    assert SEPARATEUR not in out
    assert out == "L'assemblee generale approuve le budget"


def test_colonnes_de_tableau_separees():
    # ligne de tantiemes : gros ecarts entre colonnes
    ligne = mots(("AMZALLAG", 0.10, 0.060), ("Jean", 0.166, 0.026), ("Pierre", 0.196, 0.036),
                 ("2710", 0.400, 0.026),
                 ("12", 0.520, 0.012), ("Rue", 0.538, 0.020), ("Felicien", 0.562, 0.048),
                 ("75016", 0.800, 0.032), ("PARIS", 0.838, 0.034))
    out = assembler_ligne(ligne)
    assert out == ("AMZALLAG Jean Pierre | 2710 | 12 Rue Felicien | 75016 PARIS")
    assert out.count(SEPARATEUR) == 3


def test_sans_width_comportement_historique():
    ligne = [{"text": "Total", "left": 0.1}, {"text": "12500", "left": 0.8}]
    assert assembler_ligne(ligne) == "Total 12500"


def test_ligne_vide_et_mot_unique():
    assert assembler_ligne([]) == ""
    assert assembler_ligne(mots(("Seul", 0.1, 0.03))) == "Seul"


def test_seuil_parametrable():
    ligne = mots(("A", 0.10, 0.010), ("B", 0.150, 0.010))
    assert SEPARATEUR in assembler_ligne(ligne, seuil_largeurs_car=2.0)
    assert SEPARATEUR not in assembler_ligne(ligne, seuil_largeurs_car=10.0)


def test_montants_alignes_a_droite_restent_distincts():
    """Cas balance comptable : libelle a gauche, deux montants en colonnes."""
    ligne = mots(("Charges", 0.08, 0.048), ("courantes", 0.132, 0.058),
                 ("1", 0.560, 0.008), ("234,56", 0.572, 0.042),
                 ("987,00", 0.800, 0.042))
    out = assembler_ligne(ligne)
    assert out == "Charges courantes | 1 234,56 | 987,00"
