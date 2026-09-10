"""Non-régression extraction Word (.doc OLE2) — incident du 10/09/2026.

Reproduit le bug : l'ancien repli binaire d'`extract_docx` transformait un .doc
Word 97-2003 en charabia OLE2 (« ࡱ> bjbj … ») ingéré tel quel — 1 082 fichiers
NCG, 53 458 chunks de bruit. Le test vérifie sur de VRAIS fichiers du parc :
  1. plus aucune signature OLE2 ni caractère de contrôle dans le texte extrait ;
  2. le texte est du français (densité de mots fréquents) ;
  3. un fichier illisible rend "" (échec propre), jamais du binaire.

Exécution (pas de DB, pas de Bedrock) :
    cd Scripts && PYTHONIOENCODING=utf-8 python tests/test_extract_doc.py
"""
import importlib.util
import os
import re
import sys
import tempfile

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
os.environ.setdefault("PALIM_CLIENT", "ncg")

# 02 exécute du code à l'import (config, clients boto3) : on charge le module
# par son chemin, sans lancer main.
spec = importlib.util.spec_from_file_location(
    "extraction02", os.path.join(SCRIPTS, "02_extraction_optimized.py"))
ext02 = importlib.util.module_from_spec(spec)
sys.argv = [sys.argv[0]]
spec.loader.exec_module(ext02)

RAW = os.path.join(os.path.dirname(SCRIPTS), "Données brutes")
CIBLES = [
    os.path.join(RAW, "5033 - 24 TORCY", "5033-24 TORCY", "5033-be miterrand.doc"),
    os.path.join(RAW, "5033 - 24 TORCY", "5033-24 TORCY",
                 "5033-Bordereau envoi à CHABOU cop. cour. Région IDF-dde subvent..doc"),
]
MOTS = re.compile(r"\b(de|la|le|les|des|du|et|une?|pour|par|au|aux|sur|dans|est|"
                  r"monsieur|madame|copropri\w+|immeuble)\b", re.I)
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def densite(t):
    return 1000.0 * len(MOTS.findall(t)) / len(t) if t and len(t) > 40 else 0.0


def test_doc_reel_extrait_en_texte():
    present = [p for p in CIBLES if os.path.exists(p)]
    if not present:
        print("SKIP : fichiers .doc de test absents (Données brutes non montées)")
        return
    for p in present:
        t = ext02.extract_docx(p)
        assert t and len(t) > 100, f"texte vide sur {p}"
        assert "bjbj" not in t and "ࡱ" not in t, f"signature OLE2 dans le texte : {p}"
        assert not CTRL.search(t), f"caractères de contrôle dans le texte : {p}"
        assert densite(t) >= 8, f"densité de mots français trop basse ({densite(t):.1f}) : {p}"
        assert "É" in t or "é" in t, f"accents perdus (encodage) : {p}"
        print(f"OK .doc réel -> {len(t)} c, densité {densite(t):.1f} : {os.path.basename(p)[:50]}")


def test_illisible_rend_vide_jamais_binaire():
    with tempfile.TemporaryDirectory() as td:
        # un .doc qui n'en est pas un (octets aléatoires) : l'ancien repli renvoyait
        # les octets « imprimables » ; le nouveau doit rendre ""
        p = os.path.join(td, "faux.doc")
        with open(p, "wb") as f:
            f.write(os.urandom(4096))
        t = ext02.extract_docx(p)
        assert t == "", f"un fichier illisible doit rendre '' (obtenu {len(t)} c)"
        # idem pour un .docx corrompu
        p2 = os.path.join(td, "faux.docx")
        with open(p2, "wb") as f:
            f.write(os.urandom(4096))
        assert ext02.extract_docx(p2) == ""
        print("OK illisible -> '' (echec propre, plus de repli binaire)")


def test_antiword_disponible():
    assert ext02._antiword_bin(), "antiword introuvable : l'extraction .doc serait muette"
    print(f"OK antiword : {ext02._antiword_bin()}")


if __name__ == "__main__":
    test_antiword_disponible()
    test_illisible_rend_vide_jamais_binaire()
    test_doc_reel_extrait_en_texte()
    print("\nTous les tests extraction .doc passent.")
