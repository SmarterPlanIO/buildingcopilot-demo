"""Tests — factorisation intra-document des bundles de publipostage (P2).

Les cas reproduisent les mesures du 23/08/2026 (guide, plan §1) :
`Courrier-complet.pdf` de 8050 a 1 353 chunks pour 91 textes uniques (93 %),
et les documents legitimes de la bande 60-80 % qui ne doivent RIEN declencher
(rapport d'expertise 1 560/519, synthese de diagnostic 1 341/447).

Execution (pas besoin de pytest) :
    cd Scripts && PYTHONIOENCODING=utf-8 python tests/test_publipostage.py
"""
import os
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from publipostage import (  # noqa: E402
    MIN_CHUNKS, SEUIL_PUBLIPOSTAGE, SEUIL_SUSPECT, factoriser, profil)


def _bundle(corps, n_destinataires, uniques_par_dest=1):
    """Simule un publipostage : un corps commun repete, plus des blocs propres."""
    out = []
    for d in range(n_destinataires):
        for u in range(uniques_par_dest):
            out.append(f"BLOC NOMINATIF destinataire {d} annexe {u}")
        out.extend(corps)
    return out


def cas_seuils():
    """Qualification par les seuls compteurs, aux bornes."""
    assert profil(1353, 91)[0] == "PUBLIPOSTAGE"            # cas reel 8050
    assert profil(1560, 519)[0] == "REPETITIF_SUSPECT"      # rapport d'expertise (66 %)
    assert profil(1341, 447)[0] == "REPETITIF_SUSPECT"      # synthese diagnostic (67 %)
    assert profil(100, 100)[0] is None                      # aucune repetition
    # Sous les seuils de factorisation, le document reste OBSERVE mais n'est
    # jamais factorise : c'est le profil PUBLIPOSTAGE seul qui declenche P2.
    assert profil(19, 1)[0] != "PUBLIPOSTAGE", "sous MIN_CHUNKS : jamais factorise"
    assert profil(100, 1)[0] != "PUBLIPOSTAGE", "un seul texte unique : jamais factorise"
    assert profil(0, 0) == (None, 0.0), "document vide : pas de division par zero"
    # borne exacte du seuil (0.70 depuis le 25/08 ; la bande 70-80% mesuree
    # sur NCG+Delacour ne contient que des publipostages averes)
    assert profil(100, 30)[0] == "PUBLIPOSTAGE"             # 70 % pile
    assert profil(100, 31)[0] == "REPETITIF_SUSPECT"        # 69 %
    assert profil(100, 20)[0] == "PUBLIPOSTAGE"             # 80 % toujours factorise
    return "seuils et bornes (dont MIN_CHUNKS, MIN_UNIQUES, document vide)"


def cas_publipostage_factorise():
    """Bundle typique : le corps n'est ecrit qu'une fois, les blocs restent."""
    corps = [f"corps du PV paragraphe {k}" for k in range(20)]
    textes = _bundle(corps, n_destinataires=60)      # 60*(1+20) = 1260 chunks
    r = factoriser(textes)
    assert r.profil == "PUBLIPOSTAGE", r
    assert r.bruts == 1260 and r.uniques == 80, r    # 60 blocs + 20 paragraphes
    assert len(r.gardes) == 80, len(r.gardes)
    occ = dict(r.gardes)
    # chaque paragraphe du corps compte 60 occurrences, chaque bloc nominatif 1
    corps_occ = [n for i, n in r.gardes if textes[i].startswith("corps")]
    blocs_occ = [n for i, n in r.gardes if textes[i].startswith("BLOC")]
    assert corps_occ == [60] * 20, corps_occ
    assert blocs_occ == [1] * 60, blocs_occ
    # la somme des occurrences reconstitue le compte d'origine : rien n'est perdu
    assert sum(n for _, n in r.gardes) == r.bruts
    # les blocs nominatifs (contenu exclusif) sont TOUS conserves
    gardes_txt = {textes[i] for i, _ in r.gardes}
    assert all(f"BLOC NOMINATIF destinataire {d} annexe 0" in gardes_txt for d in range(60))
    return f"bundle 1260 chunks -> {len(r.gardes)} ecrits, contenu exclusif intact"


def cas_ordre_et_indices():
    """L'ordre de premiere apparition est preserve, les index sont croissants."""
    textes = ["A", "B", "A", "C", "B", "A"]
    # sous les seuils : aucune factorisation, comportement historique
    r = factoriser(textes)
    assert r.profil is None and len(r.gardes) == 6
    assert [i for i, _ in r.gardes] == [0, 1, 2, 3, 4, 5]
    assert all(n == 1 for _, n in r.gardes), "hors profil : nb_occurrences toujours 1"
    # au-dessus des seuils
    gros = (["A"] * 50) + ["B"] + (["A"] * 50) + ["C"]
    r2 = factoriser(gros)
    assert r2.profil == "PUBLIPOSTAGE", r2
    idx = [i for i, _ in r2.gardes]
    assert idx == sorted(idx), "les gardes doivent suivre l'ordre du document"
    assert idx == [0, 50, 101], idx
    assert dict(r2.gardes)[0] == 100, "les 100 'A' comptes sur la premiere occurrence"
    return "ordre de premiere apparition preserve, indices croissants"


def cas_legitime_non_touche():
    """Un document de la bande 60-80 % garde TOUS ses chunks."""
    # 1 560 chunks / 519 uniques = 66,7 % : rapport d'expertise reel de 8050
    textes = [f"texte {i % 519}" for i in range(1560)]
    r = factoriser(textes)
    assert r.profil == "REPETITIF_SUSPECT", r
    assert len(r.gardes) == 1560, "aucune factorisation dans la bande 60-80 %"
    assert all(n == 1 for _, n in r.gardes)
    return "document 60-80 % (expertise) -> 1560 chunks tous conserves"


def cas_bords():
    """Document vide, document d'un seul chunk, tous chunks identiques."""
    assert factoriser([]).gardes == []
    assert factoriser([]).profil is None
    r = factoriser(["seul"])
    assert len(r.gardes) == 1 and r.gardes[0] == (0, 1)
    # 100 fois le meme texte : 1 seul unique -> MIN_UNIQUES bloque la factorisation
    r2 = factoriser(["idem"] * 100)
    assert r2.profil != "PUBLIPOSTAGE", "un document mono-texte n'est jamais reduit"
    assert len(r2.gardes) == 100, "MIN_UNIQUES protege : les 100 chunks restent"
    return "bords : vide, chunk unique, document entierement repete"


def main():
    cas = [cas_seuils, cas_publipostage_factorise, cas_ordre_et_indices,
           cas_legitime_non_touche, cas_bords]
    for f in cas:
        print(f"  OK  {f():<64} [{f.__name__}]")
    print(f"\n{len(cas)}/{len(cas)} cas passes.")


if __name__ == "__main__":
    main()
