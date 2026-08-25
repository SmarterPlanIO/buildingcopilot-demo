"""
Factorisation intra-document des bundles de publipostage.

CONTEXTE (PLAN_PUBLIPOSTAGE_FACTORISATION.md, mesure du 23/08/2026) : certains
fichiers sont un corps commun (PV d'AG, convocation) recopie une fois par
destinataire, entrelace d'annexes individualisees. Cas type NCG :
`Courrier-complet.pdf`, jusqu'a 92 % de redondance interne. Sur la base NCG,
33 documents portent 88 982 chunks strictement redondants, soit 24 % du total.

DOCTRINE : factoriser, pas detruire. Le texte repete n'est ecrit qu'UNE fois,
et le chunk survivant porte `nb_occurrences`. L'information « ce texte apparait
N fois dans ce document » est preservee et interrogeable ; le retrieval n'en
voit qu'un exemplaire.

IDENTITE DES CHUNKS : `03` calcule `chunk_id = md5(source_file||texte)` pour la
premiere occurrence, et suffixe `#N` les suivantes. La factorisation ne garde
que la premiere : le `chunk_id` survivant est donc INCHANGE. Consequence
majeure, aucun re-embedding n'est necessaire au rattrapage.

EGALITE STRICTE, pas normalisee : deux chunks ne sont fusionnes que si leurs
textes sont identiques a l'octet pres. Une normalisation (casse, ponctuation)
fusionnerait des textes differents et perdrait celui qui n'est pas conserve.
C'est aussi la definition utilisee par la mesure qui a produit les seuils.

CONDITIONNALITE : seuls les documents qualifies PUBLIPOSTAGE sont factorises.
Un document ordinaire garde tous ses chunks, meme s'il contient une repetition.
Garde-fou gratuit : le risque « meme clause rattachee a deux resolutions
differentes » est mesure a ZERO sur le corpus (0 texte identique portant des
`resolution_category` divergentes), mais le corpus d'un futur client peut differer.

Module pur (aucune I/O, aucun etat) : cf. tests/test_publipostage.py.
Les seuils sont partages avec `qualifier_publipostage.py` (P1) pour qu'une
mesure et un traitement ne puissent jamais diverger.
"""
from __future__ import annotations

from collections import namedtuple

# ── Seuils, deduits de la mesure du 23/08 (plan §6), pas choisis a priori ────
SEUIL_PUBLIPOSTAGE = 0.80   # redondance a partir de laquelle on factorise
SEUIL_SUSPECT      = 0.60   # simple observation, AUCUN traitement
MIN_CHUNKS         = 20     # en deca, la redondance n'est pas significative
MIN_UNIQUES        = 2      # jamais de document reduit a un seul texte

Resultat = namedtuple("Resultat", "profil redondance bruts uniques gardes")


def profil(bruts: int, uniques: int):
    """Qualification d'un document depuis ses seuls compteurs.

    Retourne (profil, redondance) ou profil vaut 'PUBLIPOSTAGE',
    'REPETITIF_SUSPECT' ou None.

    La bande 60-80 % ne declenche RIEN : elle contient des documents legitimes
    verifies (rapport d'expertise a 1 560 chunks / 519 uniques, synthese de
    diagnostic a 1 341 / 447) dont la repetition vient de tableaux et d'en-tetes.
    """
    if bruts <= 0:
        return None, 0.0
    redondance = (bruts - uniques) / bruts
    if redondance >= SEUIL_PUBLIPOSTAGE and bruts >= MIN_CHUNKS and uniques >= MIN_UNIQUES:
        return "PUBLIPOSTAGE", redondance
    if redondance >= SEUIL_SUSPECT:
        return "REPETITIF_SUSPECT", redondance
    return None, redondance


def factoriser(textes: list) -> Resultat:
    """Analyse les chunks d'UN document et decide de la factorisation.

    `gardes` est la liste des chunks a ecrire, dans l'ordre de premiere
    apparition : [(index_dans_textes, nb_occurrences), ...].
    Hors profil PUBLIPOSTAGE, tous les chunks sont gardes avec nb_occurrences=1
    (comportement strictement identique a l'existant).
    """
    premier = {}          # texte -> index de sa premiere apparition
    occurrences = {}      # index de premiere apparition -> compte
    for i, t in enumerate(textes):
        j = premier.get(t)
        if j is None:
            premier[t] = i
            occurrences[i] = 1
        else:
            occurrences[j] += 1

    bruts, uniques = len(textes), len(premier)
    p, redondance = profil(bruts, uniques)

    if p == "PUBLIPOSTAGE":
        gardes = [(i, occurrences[i]) for i in sorted(occurrences)]
    else:
        gardes = [(i, 1) for i in range(bruts)]

    return Resultat(profil=p, redondance=redondance, bruts=bruts,
                    uniques=uniques, gardes=gardes)
