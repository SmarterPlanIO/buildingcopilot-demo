"""
resolution_index.py — Détecteur de résultat de résolution d'AG (module pur, zéro LLM).

C1 de PLAN_FIABILITE_SYNTHESE.md. Une résolution de PV contient trois objets de
statuts différents :
- le DISPOSITIF (« L'assemblée approuve… ») : texte soumis au vote — jamais utilisé
  pour le résultat (c'est lui qui a produit l'incident du 27/08) ;
- le DÉCOMPTE (pour/contre/abstentions, tantièmes) → canal A : résultat CALCULÉ ;
- la PROCLAMATION (conclusion qui constate) → canal B : résultat LU dans le texte.

Le critère qui sépare dispositif et proclamation est POSITIONNEL, pas grammatical
(des PV réels concluent par « l'assemblée approuve… » ACTIF après le décompte) :
ce qui précède le décompte est proposé au vote ; ce qui le suit, ou clôt le bloc,
est le constat. Le décompte sert d'ANCRE même illisible : un tableau massacré par
le scan laisse des traces localisables (POUR/CONTRE/tantièmes) qui partagent le
texte, même quand les nombres sont incalculables.

Subtilité juridique (loi du 10/07/1965) : en article 25/26, « pour > contre » ne
suffit PAS à conclure l'adoption (majorité absolue de tous les copropriétaires,
dont on n'a pas le total dans le chunk) — seul le rejet est calculable sûrement.
Le canal A n'affirme donc l'adoption par calcul qu'en majorité simple (art. 24 ou
article inconnu, confiance dégradée si inconnu).

Réconciliation (cf. plan) : A+B concordants → confiance haute ; A seul → calculé ;
B seul → proclamé (+ flag decompte_illisible si traces d'ancre) ; A et B
discordants → "contradictoire" (jamais tranché en silence) ; ni A ni B →
"indetermine". Statut à part : "retiree" (résolution retirée / non soumise au vote).

API :
    index_resolution(text) -> dict (une résolution = un chunk PV_AG)
    index_chunks(rows)     -> list[dict] ; rows = iterables (chunk_id, source_file,
                              date, texte) — enrichit chaque résultat des métadonnées.
"""
import re
import unicodedata

# ── Normalisation à LONGUEUR PRÉSERVÉE (les offsets restent valables) ──


def _norm(text):
    """Majuscules sans accents, longueur strictement identique à l'entrée."""
    out = []
    for ch in text:
        d = unicodedata.normalize("NFKD", ch)
        base = d[0] if d else ch
        out.append(base.upper() if base.isascii() else ch.upper())
    return "".join(out)


# ── Ancre décompte ──
# Mots-clés d'un décompte. "POUR" seul est trop ambigu en français : l'ancre exige
# au moins 2 mots-clés distincts dans une même fenêtre, ou 1 mot-clé + "TANTIEMES".
_KW_POUR = re.compile(r"\b(?:ONT\s+VOTE\s+POUR|VOTES?\s+POUR|VOIX\s+POUR|POUR\s*[:\-–]|POUR\s+\d)")
_KW_CONTRE = re.compile(r"\b(?:ONT\s+VOTE\s+CONTRE|VOTES?\s+CONTRE|VOIX\s+CONTRE|CONTRE\s*[:\-–]|CONTRE\s+\d|\d\s+CONTRE\b)")
_KW_ABST = re.compile(r"\bABSTENTIONS?\b|\bS'ABSTIEN\w*|\bABSTENANTS?\b")
_KW_TANT = re.compile(r"\bTANTIEMES?\b|\bMILLIEMES?\b|/\s*10\s*000|\bVOIX\b")
_ANCHOR_WINDOW = 320   # deux mots-clés à moins de N chars = même décompte
_NUM_AFTER = 80        # distance max mot-clé -> nombre associé

_NUM_RE = re.compile(r"\d[\d\s., ]*\d|\d")


def _parse_num(raw):
    """'2 606' / '2.606' / '2606,50' -> float. None si rien d'exploitable."""
    s = raw.replace(" ", " ").strip()
    s = re.sub(r"[.\s](?=\d{3}\b)", "", s)   # séparateurs de milliers
    s = s.replace(",", ".")
    s = re.sub(r"[^\d.]", "", s)
    if not s or s == ".":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _find_anchors(tn):
    """Positions des mots-clés de décompte dans le texte normalisé."""
    anchors = []
    for kind, rx in (("pour", _KW_POUR), ("contre", _KW_CONTRE),
                     ("abstention", _KW_ABST), ("tantiemes", _KW_TANT)):
        for m in rx.finditer(tn):
            anchors.append((m.start(), m.end(), kind))
    return sorted(anchors)


def _cluster_anchors(anchors):
    """Groupes de mots-clés proches (fenêtre) ; retenu = le DERNIER cluster portant
    pour+contre (un chunk peut contenir un vote d'amendement avant le vote final)."""
    clusters, cur = [], []
    for a in anchors:
        if cur and a[0] - cur[-1][1] > _ANCHOR_WINDOW:
            clusters.append(cur)
            cur = []
        cur.append(a)
    if cur:
        clusters.append(cur)

    def kinds(c):
        return {k for _, _, k in c}

    valid = [c for c in clusters
             if len(kinds(c) - {"tantiemes"}) >= 2 or
             (kinds(c) & {"pour", "contre"} and "tantiemes" in kinds(c))]
    return valid[-1] if valid else None


def _extract_counts(tn, cluster):
    """Nombre associé à chaque mot-clé du cluster (premier nombre qui suit).
    La recherche est BORNÉE par le mot-clé suivant du cluster, et à courte
    distance (20 chars) après le dernier — sinon la fenêtre avale des nombres
    étrangers au décompte (le « 25 » de « l'article 25 » d'une proclamation)."""
    counts = {}
    for i, (start, end, kind) in enumerate(cluster):
        if kind == "tantiemes":
            continue
        if i + 1 < len(cluster):
            limit = min(cluster[i + 1][0], end + _NUM_AFTER)
        else:
            limit = min(len(tn), end + 20)
        m = _NUM_RE.search(tn, end, limit)
        if m:
            # faux ami des PV tabulaires : « base de calcul de : 63 527 tantièmes »
            # est l'assiette du vote, pas un compte de voix
            ctx = tn[max(0, m.start() - 45):m.start()]
            if "BASE DE CALCUL" in ctx or "BASE DE" in ctx[-12:]:
                continue
            val = _parse_num(m.group(0))
            if val is not None and kind not in counts:
                counts[kind] = val
    return counts


# ── Article de majorité ──
_ARTICLE_RE = re.compile(r"ART(?:ICLE)?\.?\s*(2[456])(?:\s*-\s*1)?")


def _find_article(tn):
    m = None
    for m in _ARTICLE_RE.finditer(tn):
        pass
    return m.group(1) if m else None


# ── Proclamation (canal B) ──
# Deux registres, parce que le contexte change le discriminant :
# - APRÈS une ancre décompte, le POSITIONNEL prime : toute forme vaut constat,
#   y compris ACTIVE (« en conséquence, l'assemblée approuve… » — cas réel Thai) ;
# - SANS ancre (clôture de bloc), pas de référence positionnelle : seules les
#   formes de CONSTAT (participiales : « est adoptée », « Rejetée. », unanimité)
#   sont retenues — les verbes actifs sont la signature du dispositif, les
#   accepter ici recréerait l'incident (« l'assemblée approuve » = proposé).
_NEG = r"(?:N['’]EST\s+PAS|NON|PAS)"
_P_REJ_PASSIF = (rf"(?:EST\s+|ETE\s+)?(?:{_NEG}\s+ADOPTEE?S?|{_NEG}\s+APPROUVEE?S?|"
                 r"REJETEES?|REFUSEES?)")
_P_REJ_ACTIF = r"L['’]ASSEMBLEE(?:\s+GENERALE)?\s+(?:REJETTE|REFUSE|N['’]APPROUVE\s+PAS)"
_P_ADO_PASSIF = r"(?:EST\s+|ETE\s+)?(?:ADOPTEES?|APPROUVEES?|VOTEES?|ENTERINEES?)"
_P_ADO_ACTIF = r"L['’]ASSEMBLEE(?:\s+GENERALE)?\s+(?:APPROUVE|ADOPTE|DECIDE|ENTERINE|AUTORISE)"
_PREFIX = r"(?:RESOLUTION|DELIBERATION|PROPOSITION)?\s*"
_PROC_REJET_FULL = re.compile(_PREFIX + rf"(?:{_P_REJ_PASSIF}|{_P_REJ_ACTIF})")
_PROC_ADOPT_FULL = re.compile(_PREFIX + rf"(?:{_P_ADO_PASSIF}|{_P_ADO_ACTIF})")
_PROC_REJET_CONSTAT = re.compile(_PREFIX + _P_REJ_PASSIF)
_PROC_ADOPT_CONSTAT = re.compile(_PREFIX + _P_ADO_PASSIF)
_PROC_UNANIME = re.compile(r"A\s+L['’]UNANIMITE")
_RETIREE_RE = re.compile(
    r"RETIREE?\s+DE\s+L['’]ORDRE\s+DU\s+JOUR|N['’]EST\s+PAS\s+SOUMISE?\s+AU\s+VOTE|"
    r"IL\s+N['’]EST\s+PAS\s+PROCEDE\s+AU\s+VOTE|SANS\s+OBJET|NON\s+SOUMISE?\s+AU\s+VOTE")
_CLOSING_ZONE = 260    # sans ancre : la proclamation se cherche en clôture du bloc


def _find_proclamation(tn, zone_start, allow_active=True):
    """Constat dans tn[zone_start:]. Le rejet se teste AVANT l'adoption ("n'est pas
    adoptée" matcherait aussi ADOPTEE). Retourne (sens, position_absolue) ou None."""
    zone = tn[zone_start:]
    rej_rx = _PROC_REJET_FULL if allow_active else _PROC_REJET_CONSTAT
    ado_rx = _PROC_ADOPT_FULL if allow_active else _PROC_ADOPT_CONSTAT
    m_rej = rej_rx.search(zone)
    m_ado = ado_rx.search(zone)
    if m_rej and m_ado:
        # un même constat ne porte qu'un sens : le motif le plus PRÉCOCE de la zone,
        # sauf chevauchement (négation captée par les deux) où le rejet prime
        if abs(m_rej.start() - m_ado.start()) < 20 or m_rej.start() <= m_ado.start():
            return ("rejetee", zone_start + m_rej.start())
        return ("adoptee", zone_start + m_ado.start())
    if m_rej:
        return ("rejetee", zone_start + m_rej.start())
    if m_ado:
        return ("adoptee", zone_start + m_ado.start())
    if _PROC_UNANIME.search(zone):
        # « à l'unanimité » seul, sans verbe : adoption (le rejet unanime, rarissime,
        # porte toujours son verbe et est capté ci-dessus)
        return ("adoptee", zone_start + _PROC_UNANIME.search(zone).start())
    return None


# ── Canal A : calcul depuis les nombres ──


def _compute_result(counts, article):
    """(resultat|None, note). Art. 25/26 : l'adoption n'est PAS calculable sans le
    total des tantièmes du syndicat — seul le rejet (contre >= pour) est sûr."""
    pour, contre = counts.get("pour"), counts.get("contre")
    if pour is None or contre is None:
        return None, "decompte_incomplet"
    if contre >= pour:
        return "rejetee", None if contre > pour else "egalite"
    if article in ("25", "26"):
        return None, "majorite_absolue_requise"
    return "adoptee", None if article == "24" else "article_inconnu"


# ── API ──


def index_resolution(text):
    """Analyse le texte d'UNE résolution. Retourne le dict du contrat C1."""
    res = {"decompte": None, "article_majorite": None, "proclamation_detectee": None,
           "resultat": "indetermine", "source_resultat": None,
           "confiance": "basse", "flags": []}
    if not text or not text.strip():
        res["flags"].append("texte_vide")
        return res
    tn = _norm(text)

    if _RETIREE_RE.search(tn):
        res.update(resultat="retiree", source_resultat="proclamation", confiance="haute")
        return res

    res["article_majorite"] = _find_article(tn)
    anchors = _find_anchors(tn)
    cluster = _cluster_anchors(anchors)

    calc, calc_note, proc = None, None, None
    if cluster:
        counts = _extract_counts(tn, cluster)
        cluster_end = cluster[-1][1]
        # la zone de constat commence après le dernier mot-clé du décompte ; on ne
        # saute que le nombre IMMÉDIATEMENT adjacent (ex. « contre : 4 867 ») — une
        # fenêtre large avalerait le « 24 » de « l'article 24 » de la proclamation
        tail = _NUM_RE.match(tn, cluster_end) or (
            re.compile(r"[\s:—\-–]{1,6}").match(tn, cluster_end) and
            _NUM_RE.match(tn, re.compile(r"[\s:—\-–]{1,6}").match(tn, cluster_end).end()))
        zone_start = tail.end() if tail else cluster_end
        if counts:
            res["decompte"] = counts
            calc, calc_note = _compute_result(counts, res["article_majorite"])
        else:
            res["flags"].append("decompte_illisible")   # ancre présente, nombres morts
        proc = _find_proclamation(tn, zone_start)
        if proc is None and _find_proclamation(tn, 0) and not counts:
            # constat détecté UNIQUEMENT avant l'ancre : ordre suspect (colonnes OCR)
            res["flags"].append("ordre_anormal")
        if len(tn) - zone_start < 5 and proc is None and not counts:
            res["flags"].append("resolution_tronquee")  # le texte meurt sur l'ancre
    else:
        # pas d'ancre : le constat ne vaut qu'en CLÔTURE du bloc, formes de
        # constat seulement (un verbe actif ici serait le dispositif)
        proc = _find_proclamation(tn, max(0, len(tn) - _CLOSING_ZONE), allow_active=False)

    if proc:
        res["proclamation_detectee"] = proc[0]
    if calc_note and calc_note != "article_inconnu":
        res["flags"].append(calc_note)

    # ── Réconciliation ──
    proclaimed = proc[0] if proc else None
    if calc and proclaimed:
        if calc == proclaimed:
            res.update(resultat=calc, source_resultat="decompte+proclamation", confiance="haute")
        else:
            res.update(resultat="contradictoire", source_resultat=None, confiance="basse")
            res["flags"].append("decompte_et_proclamation_discordants")
    elif calc:
        res.update(resultat=calc, source_resultat="decompte",
                   confiance="moyenne" if calc_note else "haute")
    elif proclaimed:
        res.update(resultat=proclaimed, source_resultat="proclamation", confiance="moyenne")
    return res


def index_chunks(rows):
    """rows : iterable de (chunk_id, source_file, date, texte). Retourne la liste
    des analyses enrichies des métadonnées — l'entrée directe de la table
    `resolutions` (C2)."""
    out = []
    for chunk_id, source_file, date, text in rows:
        r = index_resolution(text)
        r.update(chunk_id=chunk_id, source_file=source_file, date=str(date) if date else None)
        out.append(r)
    return out
