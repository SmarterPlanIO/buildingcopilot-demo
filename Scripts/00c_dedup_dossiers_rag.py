"""00c — Dédoublonnage intra-RAG des dossiers sinistres (dossiers.jsonl).

Problème : 05c_entity_extraction groupe par (copro, type_dossier, lese_slug). Un même
sinistre se retrouve éclaté en plusieurs dossiers : type-split (DDE + AUTRE pour un même
événement) et variantes de nom (HIM / Leng HIM / M Leng HIM / HIM Leng). Cf copro 8050 :
~132 dossiers pour ~50 réels.

Cette passe fusionne UNIQUEMENT les doublons à haute confiance, de façon généralisable
à toute copro. Conservatrice par conception : sur donnée légale, on préfère sous-fusionner
que fusionner à tort. NON destructive : écrit un nouveau fichier + un rapport, ne touche
pas dossiers.jsonl.

Règle de fusion (deux dossiers de la MÊME copro) :
  - noms : un jeu de tokens normalisés ⊆ l'autre (gère les variantes), ET
  - pas de conflit de lot : deux n° d'appartement présents et différents = STOP, ET
  - les DEUX dossiers sont datés ET leurs dates sont proches (≤ WINDOW jours).
Un sinistre non daté est incomplet (info essentielle manquante) : il ne fusionne avec
personne et reste tel quel. Le lot ne sert que de garde anti-conflit, jamais de
déclencheur seul -> pas de "pont" transitif via une entrée non datée.

Clustering par union-find sur ces arêtes ; chaque cluster fusionné en un dossier
(union des documents_lies / pièces / étapes, meilleur nom, date la plus ancienne, etc.).

Usage :
  PYTHONIOENCODING=utf-8 python 00c_dedup_dossiers_rag.py            # toutes copros
  PYTHONIOENCODING=utf-8 python 00c_dedup_dossiers_rag.py --copro 8050   # focus rapport
Sorties :
  Résultats bruts/dossiers_dedup.jsonl   (fichier dédoublonné, prêt pour load_dossiers_only)
  Résultats bruts/dossiers_dedup_report.txt
"""
import argparse
import json
import os
import re
import unicodedata
from collections import defaultdict
from datetime import date

BASE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts"
INPUT_FILE = os.path.join(BASE, "dossiers.jsonl")
OUTPUT_FILE = os.path.join(BASE, "dossiers_dedup.jsonl")
REPORT_FILE = os.path.join(BASE, "dossiers_dedup_report.txt")

WINDOW_DAYS = 30  # écart max entre dates d'ouverture pour considérer le même sinistre

# Priorité de type pour le dossier fusionné (le plus spécifique gagne).
_TYPE_PRIORITY = {"SINISTRE_DDE": 0, "SINISTRE_INCENDIE": 1, "SINISTRE_MRI": 2, "SINISTRE_AUTRE": 3}
# Priorité de statut (le plus actif gagne) — aligné sur get_dossiers (sidebar).
_STATUT_PRIORITY = {"EN_ATTENTE": 0, "EN_COURS": 1}

_TITLES = re.compile(r'\b(MR|MME|MADAME|MONSIEUR|M|STE|SOCIETE|SCI|SNC|SAS|CONSORT|CONSORTS)\b')


def norm_name(name):
    """Jeu de tokens normalisés (set) : sans accents, MAJ, titres retirés, tokens alpha >=3."""
    if not name:
        return set()
    s = unicodedata.normalize("NFD", name)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").upper()
    s = re.sub(r'\bSYNDICAT\b.*', '', s)
    s = re.sub(r'\b(SINISTRE_DDE|DDE)\b', '', s)
    s = _TITLES.sub('', s)
    return set(re.findall(r'[A-Z]{3,}', s))


def lot_apt(lot):
    """N° d'appartement fiable (2 à 4 chiffres) ou None. Les lots texte ('2ème',
    'Bât 1', 'Étage') sont trop bruités pour servir de clé -> None."""
    if not lot:
        return None
    m = re.search(r'\b(\d{2,4})\b', str(lot))
    return m.group(1) if m else None


def parse_d(v):
    if not v or not isinstance(v, str):
        return None
    try:
        return date.fromisoformat(v[:10])
    except (ValueError, TypeError):
        return None


def copro_code(rec):
    """Code copro pour le groupage (chiffres en tête de 'copropriete', sinon la chaîne)."""
    cop = rec.get("copropriete", "") or ""
    m = re.match(r'\s*(\d{3,6})\b', cop)
    return m.group(1) if m else cop


def same_sinistre(a, b):
    """Prédicat de fusion conservateur (cf docstring module)."""
    na, nb = a["_name"], b["_name"]
    if not na or not nb:
        return False
    if not (na <= nb or nb <= na):  # sous-ensemble dans un sens ou l'autre
        return False

    la, lb = a["_apt"], b["_apt"]
    if la and lb and la != lb:
        return False  # conflit d'appartement

    # Un sinistre non daté est incomplet -> il ne fusionne avec personne.
    # On exige deux dates présentes et proches ; le lot ne déclenche jamais seul,
    # donc aucune entrée non datée ne peut servir de pont transitif.
    da, db = a["_date"], b["_date"]
    if not (da and db):
        return False
    return abs((da - db).days) <= WINDOW_DAYS


# ── Union-find ──
def _find(parent, i):
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _union(parent, i, j):
    ri, rj = _find(parent, i), _find(parent, j)
    if ri != rj:
        parent[max(ri, rj)] = min(ri, rj)


def cluster(records):
    """Retourne une liste de clusters (listes d'indices) pour des dossiers d'une copro."""
    n = len(records)
    parent = list(range(n))
    for i in range(n):
        for j in range(i + 1, n):
            if same_sinistre(records[i], records[j]):
                _union(parent, i, j)
    groups = defaultdict(list)
    for i in range(n):
        groups[_find(parent, i)].append(i)
    return list(groups.values())


def _nonempty(v):
    return v not in (None, "", [], "[]")


def _union_list(values):
    """Union ordonnée et dédoublonnée de plusieurs listes (ou valeurs scalaires ignorées)."""
    out, seen = [], set()
    for v in values:
        if not isinstance(v, list):
            continue
        for x in v:
            key = json.dumps(x, sort_keys=True, ensure_ascii=False) if isinstance(x, (dict, list)) else x
            if key not in seen:
                seen.add(key)
                out.append(x)
    return out


def merge_cluster(recs):
    """Fusionne un cluster de dossiers (>=1) en un seul dict de dossier."""
    if len(recs) == 1:
        return recs[0]

    # Représentant = le plus riche (nb documents_lies), tiebreak : a une date, puis nom long.
    rep = max(recs, key=lambda r: (len(r.get("documents_lies") or []),
                                   1 if r.get("date_ouverture") else 0,
                                   len(r.get("lese_nom") or "")))
    merged = dict(rep)  # conserve les champs non gérés explicitement (du représentant)

    # type : le plus spécifique
    merged["type_dossier"] = min((r.get("type_dossier") for r in recs),
                                 key=lambda t: _TYPE_PRIORITY.get(t, 9))
    # statut : le plus actif
    merged["statut"] = min((r.get("statut") for r in recs if r.get("statut")),
                           key=lambda s: _STATUT_PRIORITY.get(s, 9), default=rep.get("statut"))
    # lese_nom : le plus complet (le plus de tokens, puis le plus long)
    merged["lese_nom"] = max((r.get("lese_nom") for r in recs if _nonempty(r.get("lese_nom"))),
                             key=lambda x: (len(norm_name(x)), len(x)), default=rep.get("lese_nom"))
    # date_ouverture : la plus ancienne connue ; date_cloture : la plus récente connue
    dates = [r.get("date_ouverture") for r in recs if parse_d(r.get("date_ouverture"))]
    merged["date_ouverture"] = min(dates, key=lambda v: parse_d(v)) if dates else rep.get("date_ouverture")
    clos = [r.get("date_cloture") for r in recs if parse_d(r.get("date_cloture"))]
    merged["date_cloture"] = max(clos, key=lambda v: parse_d(v)) if clos else rep.get("date_cloture")

    # Premier non-vide pour les champs simples
    for f in ("lese_lot", "responsable_nom", "responsable_lot", "expert_nom", "assureur",
              "num_sinistre", "num_police", "montant_estime", "montant_reel"):
        merged[f] = next((r.get(f) for r in recs if _nonempty(r.get(f))), rep.get(f))

    # Unions de listes
    merged["documents_lies"] = _union_list([r.get("documents_lies") for r in recs])
    merged["pieces_requises"] = _union_list([r.get("pieces_requises") for r in recs])
    merged["pieces_fournies"] = _union_list([r.get("pieces_fournies") for r in recs])
    # etapes : union par 'nom', statut FAIT prioritaire
    et = {}
    for r in recs:
        for e in (r.get("etapes") or []):
            k = e.get("nom") if isinstance(e, dict) else str(e)
            if k not in et or (isinstance(e, dict) and e.get("statut") == "FAIT"):
                et[k] = e
    merged["etapes"] = list(et.values())

    merged["resume_ia"] = (f"Sinistre {merged.get('nom_dossier','')} - {merged.get('lese_nom') or 'lésé inconnu'} "
                           f"- {len(merged['documents_lies'])} documents - fusion de {len(recs)} dossiers RAG")
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--copro", default="8050", help="Code copro détaillé dans le rapport")
    args = ap.parse_args()

    by_copro = defaultdict(list)
    total_in = 0
    with open(INPUT_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rec["_name"] = norm_name(rec.get("lese_nom") or rec.get("nom_dossier", ""))
            rec["_apt"] = lot_apt(rec.get("lese_lot"))
            rec["_date"] = parse_d(rec.get("date_ouverture"))
            by_copro[copro_code(rec)].append(rec)
            total_in += 1

    out_records = []
    report = []
    focus_lines = []
    per_copro_stats = []

    for code, recs in sorted(by_copro.items()):
        clusters = cluster(recs)
        n_before, n_after = len(recs), len(clusters)
        if n_after < n_before:
            per_copro_stats.append((code, n_before, n_after))
        for idx in clusters:
            members = [recs[i] for i in idx]
            merged = merge_cluster(members)
            if code == args.copro and len(members) > 1:
                names = " + ".join(f"{m.get('lese_nom') or '?'}[{m.get('type_dossier','')[:12]}/"
                                   f"{m.get('date_ouverture') or '?'}/lot {m.get('lese_lot') or '-'}]"
                                   for m in members)
                focus_lines.append(f"  FUSION ({len(members)}) -> {merged.get('lese_nom')} "
                                   f"[{merged.get('type_dossier')}] {len(merged['documents_lies'])} docs\n"
                                   f"        {names}")
            for k in ("_name", "_apt", "_date"):
                merged.pop(k, None)
            out_records.append(merged)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for rec in out_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    report.append(f"DÉDUP DOSSIERS RAG — {total_in} dossiers en entrée -> {len(out_records)} en sortie "
                  f"({total_in - len(out_records)} fusionnés)\n")
    report.append("Copros impactées (avant -> après) :")
    for code, b, a in sorted(per_copro_stats, key=lambda x: x[1] - x[2], reverse=True):
        report.append(f"  {code:8} {b:4} -> {a:4}  (-{b - a})")
    foc = [r for r in out_records if copro_code(r) == args.copro]
    report.append(f"\n=== FOCUS COPRO {args.copro} : {len(foc)} dossiers après dédup ===")
    report.extend(focus_lines or ["  (aucune fusion)"])

    text = "\n".join(report)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)
    print(f"\n-> {OUTPUT_FILE}\n-> {REPORT_FILE}")


if __name__ == "__main__":
    main()
