"""layout_text.py — assemblage d'une ligne de mots Textract en preservant les colonnes.

Probleme (constate le 27/08 sur les tableaux de tantiemes, balances, releves) :
Textract rend des WORD blocks avec leurs coordonnees, et notre reconstruction les
recollait avec un simple espace. Un tableau devenait une soupe ou plus rien ne
rattache un montant a sa colonne :

    M. AMZALLAG Jean Pierre 2710 12 Rue Felicien David 75016 PARIS 16 P

Le pipeline jetait pourtant l'information : l'ECART HORIZONTAL entre deux mots.
Un espace inter-mots vaut ~0,25-0,5 largeur de caractere ; un saut de colonne en
vaut plusieurs. On insere donc un separateur au-dela d'un seuil exprime en
largeurs de caractere (unite homogene : tout est en fraction de largeur de page).

    M. AMZALLAG Jean Pierre | 2710 | 12 Rue Felicien David | 75016 PARIS 16 | P

Aucun cout supplementaire : les coordonnees sont deja dans la reponse Textract.
"""
from __future__ import annotations

SEPARATEUR = " | "
SEUIL_LARGEURS_CAR = 3.0   # ecart > 3 largeurs de caractere => colonne
_LARGEUR_CAR_DEFAUT = 0.008  # fraction de largeur de page, repli si width absent


def largeur_caractere(words) -> float:
    """Largeur mediane d'un caractere sur la ligne (fraction de largeur de page)."""
    ech = [w["width"] / len(w["text"]) for w in words
           if w.get("width") and w.get("text")]
    if not ech:
        return _LARGEUR_CAR_DEFAUT
    ech.sort()
    return ech[len(ech) // 2] or _LARGEUR_CAR_DEFAUT


def assembler_ligne(words, seuil_largeurs_car: float = SEUIL_LARGEURS_CAR) -> str:
    """Assemble des mots (deja tries par `left`) en preservant les colonnes.

    words : [{"text", "left", "width"(optionnel)}]. Sans `width`, le comportement
    reste l'ancien (simple espace) : aucune regression sur les reponses partielles.
    """
    if not words:
        return ""
    if not any(w.get("width") for w in words):
        return " ".join(w["text"] for w in words)

    seuil = largeur_caractere(words) * seuil_largeurs_car
    morceaux = [words[0]["text"]]
    for prec, w in zip(words, words[1:]):
        ecart = w["left"] - (prec["left"] + prec.get("width", 0))
        morceaux.append(SEPARATEUR if ecart > seuil else " ")
        morceaux.append(w["text"])
    return "".join(morceaux)
