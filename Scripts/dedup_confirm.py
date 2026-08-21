"""
Confirmation des doublons proches — étage de vérification de 03_chunking.py.

CONTEXTE
--------
La règle historique de 03 condamnait une paire de documents sur la seule
ressemblance de leurs 500 premiers caractères (seuil 0.85, même dossier parent).
Sur un corpus de syndic, ces 500 caractères ne contiennent que l'en-tête du
cabinet et le nom de l'immeuble : deux AG différentes de la même copropriété s'y
ressemblent à plus de 90 %. Mesure du 20/08/2026 : 5 087 documents supprimés à
tort sur les parcs NCG + Delacour, dont au moins 381 PV/AG.

Ce module ne remplace PAS ce test : il le garde comme *générateur de candidats*
(bon marché, déjà en place) et ajoute l'étage qui manquait, la *confirmation* sur
le texte intégral. Le surcoût ne porte donc que sur les paires déjà signalées.

POURQUOI PAS UN SIMPLE SEUIL PLUS HAUT
--------------------------------------
La distribution du Jaccard sur les 1 605 paires signalées par l'ancienne règle
(échantillon de 6 copropriétés Delacour) est continue, sans vallée : aucun seuil
n'y sépare les vrais des faux doublons. Et la similarité seule ne suffit pas dans
le haut de la distribution : deux bulletins mensuels de mois différents sont à
0.92 de Jaccard avec des longueurs identiques. Il faut un second signal
indépendant du texte, d'où le contrôle sur les jeux de dates.

RÈGLE
-----
Des dates différentes dans les DEUX NOMS DE FICHIER opposent un veto absolu :
deux documents ainsi datés sont distincts, quoi que dise leur texte. Ce veto
passé, B est un doublon de A si et seulement si :
    (a) leurs textes normalisés sont identiques,  OU
    (b) les TROIS conditions sont réunies :
          Jaccard 5-grammes >= JACCARD_MIN sur le texte intégral
          mêmes dates dans les deux textes
          rapport des longueurs >= RATIO_LONGUEUR_MIN
Dans tout autre cas, les deux documents sont conservés. La règle est
volontairement conservatrice : garder un doublon coûte quelques chunks, perdre un
PV d'AG coûte une réponse fausse sur un document juridique.

Résultat mesuré sur l'échantillon de calibration : 84 suppressions sur les 1 605
d'aujourd'hui, avec 0 suppression sur les 52 paires de bulletins mensuels et les
46 paires de relevés bancaires, et conservation des PV d'assemblées distinctes.

Ce module est pur (aucune I/O, aucun état global) pour rester testable sans
exécuter le pipeline : cf. tests/test_dedup_confirm.py.
"""
from __future__ import annotations

import re
import unicodedata
from collections import namedtuple

# ── Paramètres. Aucune valeur magique enfouie dans le code. ──────────────────
CANDIDAT_TETE_SEUIL = 0.85   # génération de candidats (inchangé, appliqué par 03)
JACCARD_MIN         = 0.90   # confirmation sur texte intégral
RATIO_LONGUEUR_MIN  = 0.90   # écart de longueur toléré entre deux exemplaires
SHINGLE_N           = 5      # taille des n-grammes de mots
TEXTE_MIN           = 100    # en deçà, on ne confirme jamais (trop peu de signal)

MOIS = {m: i + 1 for i, m in enumerate(
    "janvier fevrier mars avril mai juin juillet aout septembre octobre "
    "novembre decembre".split())}

# Les lookarounds négatifs remplacent \b : \b ne se déclenche pas entre un
# underscore et un chiffre, donc "BullStand_20250101.pdf" ne rendait aucune date
# et le contrôle sur les noms de fichier était silencieusement neutralisé.
_RE_DATE_SEP     = re.compile(r"(?<!\d)(\d{1,2})[/.\-_ ](\d{1,2})[/.\-_ ](\d{2,4})(?!\d)")
_RE_DATE_COMPACT = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(\d{2})(\d{2})(?!\d)")
_RE_DATE_LETTRES = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(MOIS) + r")\s+(\d{4})")
_RE_NON_ALNUM    = re.compile(r"\W+")

Profil = namedtuple("Profil", "nom_fichier norm shingles dates_texte dates_nom longueur")
Verdict = namedtuple("Verdict", "doublon score motif")


def _ascii(s: str) -> str:
    """Minuscules sans accents : 'Signé' et 'SIGNE' doivent se comparer."""
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def normaliser(texte: str) -> str:
    """Forme canonique d'un texte : casse, accents et ponctuation neutralisés.

    Deux extractions du même PDF (OCR contre texte natif) ne diffèrent souvent
    que par la ponctuation et les espaces.
    """
    return _RE_NON_ALNUM.sub(" ", _ascii(texte)).strip()


def shingles(texte_normalise: str, n: int = SHINGLE_N) -> frozenset:
    """N-grammes de mots. Insensible à l'ordre des paragraphes, sensible au fond."""
    mots = texte_normalise.split()
    if len(mots) < n:
        return frozenset([" ".join(mots)]) if mots else frozenset()
    return frozenset(" ".join(mots[i:i + n]) for i in range(len(mots) - n + 1))


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def dates(texte: str) -> frozenset:
    """Jeu des dates citées, en triplets (année, mois, jour).

    Signal indépendant du texte : deux exemplaires d'un même document portent
    les mêmes dates, deux documents distincts de même gabarit (appels de
    provisions, bulletins mensuels, convocations) n'en portent jamais le même jeu.
    """
    t = _ascii(texte)
    out = set()
    for d, m, y in _RE_DATE_SEP.findall(t):
        an = int(y)
        if an < 50:
            an += 2000
        elif an < 100:
            an += 1900
        if 1900 < an < 2100 and 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
            out.add((an, int(m), int(d)))
    for y, m, d in _RE_DATE_COMPACT.findall(t):
        if 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
            out.add((int(y), int(m), int(d)))
    for d, mo, y in _RE_DATE_LETTRES.findall(t):
        if 1 <= int(d) <= 31:
            out.add((int(y), MOIS[mo], int(d)))
    return frozenset(out)


def profil(nom_fichier: str, texte: str) -> Profil:
    """Précalcul de tout ce que la confirmation demande, pour un document.

    À calculer une fois par document et à mettre en cache côté appelant : sur un
    dossier de 1 000 fichiers, les mêmes documents reviennent dans de nombreuses
    paires.
    """
    norm = normaliser(texte)
    return Profil(nom_fichier=nom_fichier or "", norm=norm, shingles=shingles(norm),
                  dates_texte=dates(texte), dates_nom=dates(nom_fichier or ""),
                  longueur=len(texte or ""))


def confirmer(a: Profil, b: Profil) -> Verdict:
    """Confirme (ou infirme) qu'une paire candidate est un vrai doublon.

    Retourne (doublon, score, motif). Le motif porte la raison, y compris en cas
    de refus : il est écrit au registre d'ingestion et rend la décision auditable.
    Symétrique par construction : confirmer(a, b) == confirmer(b, a).
    """
    if a.longueur < TEXTE_MIN or b.longueur < TEXTE_MIN:
        return Verdict(False, 0.0, "TEXTE_TROP_COURT")

    # Veto absolu, AVANT même le test d'identité : des dates différentes dans les
    # noms de fichier désignent deux documents distincts, quoi que dise le texte.
    # Cas réel : deux relevés mensuels dont l'OCR ne capte que le gabarit, texte
    # extrait identique. Les fusionner effacerait un document de l'inventaire
    # pour ne rien gagner (le texte, lui, est déjà là en double).
    if a.dates_nom != b.dates_nom:
        return Verdict(False, 0.0, "DATES_NOM_DIFFERENTES")

    if a.norm == b.norm:
        return Verdict(True, 1.0, "TEXTE_IDENTIQUE")

    score = jaccard(a.shingles, b.shingles)
    if score < JACCARD_MIN:
        return Verdict(False, score, "JACCARD_INSUFFISANT")
    if a.dates_texte != b.dates_texte:
        return Verdict(False, score, "DATES_TEXTE_DIFFERENTES")
    if min(a.longueur, b.longueur) / max(a.longueur, b.longueur) < RATIO_LONGUEUR_MIN:
        return Verdict(False, score, "LONGUEURS_ECARTEES")
    return Verdict(True, score, "QUASI_IDENTIQUE")
