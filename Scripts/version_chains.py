"""
Détection des version chains — soft delete, jamais de suppression.

Une version chain = les états successifs d'un MÊME document de travail dans un
même dossier (brouillon → v1 → v2 → VF/signé), ou une variante d'un même
document (ex. exemplaire expurgé `_RGPD`). Contrairement au near-duplicate
detection de `dedup_confirm.py` (qui SUPPRIME, et n'attrape que des exemplaires
quasi identiques), ce module ne détruit rien : il désigne un document de
référence par famille et FLAGGE les autres (`retrieval_exclu` + motif), pour
exclusion du retrieval par défaut sur le patron BORDEREAU_AR. Une erreur de
flag se corrige en base ; une suppression est définitive.

Conditions pour flagger B au profit de A (toutes nécessaires) :
  1. même dossier parent ET même racine de nom (stem) après retrait des
     marqueurs de version/copie/variante et des dates — famille de taille
     2..publipostage_min_famille (au-delà = publipostage, on ne touche à rien) ;
  2. containment(B ⊂ A) >= containment_min sur les shingles du texte intégral ;
  3. mêmes dates dans les textes, OU marqueur de version explicite dans au
     moins un nom de la famille (un brouillon et sa VF portent la même date
     d'AG ; deux convocations d'AG différentes, non).

Choix du document de référence (ordre de priorité, décision actée avec Thai le
20/08 : « le PV signé fait foi ») :
  signé/authoritative > numéro de version le plus haut > mtime source le plus
  récent (signature `taille:mtime_ns` de 02, si disponible) > texte le plus long.

Les conventions (marqueurs, suffixes de variante, seuils) viennent du profil
client `dedup_rules.py` — rien en dur ici.

Module pur (aucune I/O) : cf. tests/test_version_chains.py.
"""
from __future__ import annotations

import re
from collections import defaultdict, namedtuple

from dedup_confirm import _ascii, dates

# Entrée : un document = (source_file, nom_fichier, dossier_parent, profil
# dedup_confirm.Profil, signature "taille:mtime_ns" ou None).
DocVC = namedtuple("DocVC", "source_file nom_fichier dossier_parent profil signature")

_RE_DATE_NOM = re.compile(
    r"(?<!\d)\d{1,2}[/.\-_ ]\d{1,2}[/.\-_ ]\d{2,4}(?!\d)|(?<!\d)(19|20)\d{6}(?!\d)"
    r"|(?<!\d)(19|20)\d{2}(?!\d)")
_RE_VNUM = re.compile(r"\bv\s?(\d+)\b", re.I)


def stem(nom_fichier: str, crules: dict) -> str:
    """Racine d'un nom de fichier : ce qui reste quand on retire l'extension,
    les dates, les marqueurs de version/copie et les suffixes de variante.
    Deux membres d'une même version chain partagent leur stem."""
    s = _ascii(nom_fichier)
    s = re.sub(r"\.[a-z0-9]{2,5}$", "", s)          # extension
    if crules["rx_variant"]:
        s = crules["rx_variant"].sub(" ", s)
    s = _RE_DATE_NOM.sub(" ", s)
    for _ in range(3):                               # marqueurs imbriqués ("VF (2)")
        s = crules["rx_copy"].sub(" ", s)
        s = crules["rx_version"].sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _containment(court, long_):
    """Recouvrement des shingles du plus court dans le plus long (0..1)."""
    if not court or not long_:
        return 0.0
    petit, grand = (court, long_) if len(court) <= len(long_) else (long_, court)
    return len(petit & grand) / len(petit)


def _mtime_ns(signature):
    """mtime source depuis la signature "taille:mtime_ns" de 02 (0 si absente)."""
    try:
        return int(str(signature).split(":")[1])
    except (IndexError, ValueError, AttributeError):
        return 0


def _priorite_reference(doc: DocVC, crules: dict):
    """Clé de tri du document de référence d'une famille (max = gagnant)."""
    nom = _ascii(doc.nom_fichier)
    auth = sum(1 for rx in crules["rx_authoritative"] if rx.search(nom))
    m = _RE_VNUM.search(nom)
    vnum = int(m.group(1)) if m else -1
    return (auth, vnum, _mtime_ns(doc.signature), doc.profil.longueur)


def detecter(docs: list, crules: dict) -> dict:
    """Détecte les version chains et variantes. Retourne
    {source_file_flagge: {"motif", "ref_source_file", "score"}}.
    Ne modifie rien : l'appelant applique les flags."""
    flags = {}

    # ── 1. Variantes par suffixe (ex. X.pdf / X_RGPD.pdf, même dossier) ──────
    if crules["rx_variant"]:
        par_cle = defaultdict(dict)     # (dossier, nom sans suffixe) -> {bool_variante: doc}
        for d in docs:
            nom = _ascii(d.nom_fichier)
            sans = crules["rx_variant"].sub("", nom)
            par_cle[(d.dossier_parent, sans)][nom != sans] = d
        for pair in par_cle.values():
            if True not in pair or False not in pair:
                continue
            orig, var = pair[False], pair[True]
            score = _containment(var.profil.shingles, orig.profil.shingles)
            if score < crules["containment_min"]:
                continue                # variante trop éloignée : on ne présume rien
            garde, flagge = (orig, var) if crules["variant_keep"] == "original" else (var, orig)
            flags[flagge.source_file] = {"motif": "VARIANTE", "ref_source_file": garde.source_file,
                                         "score": round(score, 4)}

    # ── 2. Version chains par famille de stem ────────────────────────────────
    familles = defaultdict(list)
    for d in docs:
        if d.source_file in flags:
            continue
        st = stem(d.nom_fichier, crules)
        if len(st) >= crules["stem_min_len"]:
            familles[(d.dossier_parent, st)].append(d)

    for membres in familles.values():
        if not (2 <= len(membres) <= crules["publipostage_min_famille"]):
            continue                    # singleton, ou publipostage : intouchable
        ref = max(membres, key=lambda d: _priorite_reference(d, crules))
        marqueur_explicite = any(crules["rx_version"].search(_ascii(d.nom_fichier)) for d in membres)
        for d in membres:
            if d is ref:
                continue
            score = _containment(d.profil.shingles, ref.profil.shingles)
            if score < crules["containment_min"]:
                continue                # fond trop différent : pas une version
            if not marqueur_explicite and d.profil.dates_texte != ref.profil.dates_texte:
                continue                # sans marqueur, il faut les mêmes dates
            flags[d.source_file] = {"motif": "VERSION_ANTERIEURE",
                                    "ref_source_file": ref.source_file,
                                    "score": round(score, 4)}
    return flags
