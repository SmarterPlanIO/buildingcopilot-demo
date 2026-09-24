"""
Exemplaires d'un même PV d'AG — sélection pour la rubrique « PV récents » de la fiche
(09_copro_synthese.py). Module pur, sans DB.

CONTEXTE
--------
Une même AG est souvent déposée en plusieurs exemplaires : PDF signé, .doc de travail,
PDF non signé (45 rue de l'Alma : trois fichiers pour l'AG du 27/05/2025). La fiche
listait chaque exemplaire comme un PV distinct, et l'ordre entre exemplaires de même
date changeait d'un calcul à l'autre.

RÈGLE
-----
Deux fichiers sont des exemplaires de la même AG s'ils ont la MÊME date d'AG ET, soit
des résolutions qui se recouvrent (Jaccard >= SEUIL_RECOUVREMENT sur les objets
normalisés), soit le même nom de fichier une fois retirés extension, « signé » et
séparateurs. La date seule ne suffit pas : une AGO et une AGE tenues le même jour sont
deux PV distincts, avec des ordres du jour différents, et doivent rester visibles tous
les deux. Rien n'est supprimé : les exemplaires écartés restent pointés.

Exemplaire retenu, par ordre de préférence : nom de fichier « signé », puis PDF, puis
le plus de résolutions à résultat établi, puis le chemin (ordre total = déterministe).
"""
import os
import re
import unicodedata

SEUIL_RECOUVREMENT = 0.5
_PREFIXE_RES = re.compile(r"^\s*\|?\s*r[ée]solution\s*n?[°o]?\s*\d+\s*[:.\-]?\s*", re.IGNORECASE)


def _norm_objet(objet):
    """Objet de résolution comparable d'un exemplaire à l'autre : sans numéro (la
    numérotation extraite diffère entre PDF et .doc), sans accents ni ponctuation,
    tronqué (objet_court est coupé à des longueurs variables)."""
    s = _PREFIXE_RES.sub("", objet or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s[:30]


def _jaccard(a, b):
    return len(a & b) / len(a | b) if (a or b) else 0.0


def _norm_nom(source_file):
    """Nom de fichier sans chemin, extension, mention de signature, accents ni
    séparateurs : « Procès verbal ... 14 mai 2024  signé.pdf » et « Procès verbal ...
    14 mai 2024.pdf » donnent la même clé."""
    nom = os.path.splitext(os.path.basename(source_file.replace("\\", "/")))[0]
    nom = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode().lower()
    nom = re.sub(r"\bsigne\b", "", nom)
    return re.sub(r"[^a-z0-9]+", "", nom)


def _meme_ag(f, g):
    """Même date garantie par l'appelant. Recouvrement des résolutions OU même nom
    normalisé : l'OCR bruite trop les objets pour que le premier signal suffise
    (recouvrement nul mesuré entre les deux exemplaires du PV du 14/05/2024 du 60 bd
    Magenta), et le second ne regroupe que des fichiers de même nom."""
    return (_jaccard(f["_objets"], g["_objets"]) >= SEUIL_RECOUVREMENT
            or _norm_nom(f["source_file"]) == _norm_nom(g["source_file"]))


def _preference(f):
    nom = os.path.basename(f["source_file"].replace("\\", "/")).lower()
    nom_ascii = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    return (0 if "sign" in nom_ascii else 1,
            0 if nom.endswith(".pdf") else 1,
            -f["n_etablies"],
            f["source_file"])


def selectionner_pv(fichiers, limite):
    """fichiers : [{source_file, date, n_etablies, objets: [objet_court, ...]}].
    Retourne au plus `limite` PV, date décroissante, un par AG :
    [{source_file, date, autres_exemplaires: [source_file, ...]}]."""
    par_date = {}
    for f in fichiers:
        par_date.setdefault(f["date"], []).append(
            {**f, "_objets": {o for o in map(_norm_objet, f.get("objets") or []) if o}})

    retenus = []
    for d in sorted(par_date, reverse=True):
        groupes = []  # regroupement glouton, fichiers pris dans l'ordre de préférence
        for f in sorted(par_date[d], key=_preference):
            for g in groupes:
                if _meme_ag(f, g[0]):
                    g.append(f)
                    break
            else:
                groupes.append([f])
        for g in groupes:
            retenus.append({"source_file": g[0]["source_file"], "date": d,
                            "autres_exemplaires": [x["source_file"] for x in g[1:]]})
        if len(retenus) >= limite:
            break
    return retenus[:limite]
