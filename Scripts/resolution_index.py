"""
resolution_index.py — Détecteur de résultat de résolution d'AG (module pur, zéro LLM).

C1+C2 de PLAN_FIABILITE_SYNTHESE.md. Une résolution de PV contient trois objets :
- le DISPOSITIF (« L'assemblée approuve… ») : texte soumis au vote — jamais utilisé
  pour le résultat (c'est lui qui a produit l'incident du 27/08) ;
- le DÉCOMPTE (pour/contre/abstentions, tantièmes) → canal A : résultat CALCULÉ ;
- la PROCLAMATION (conclusion qui constate) → canal B : résultat LU dans le texte.

Le critère qui sépare dispositif et proclamation est POSITIONNEL, pas grammatical :
ce qui précède le décompte est proposé au vote ; ce qui le suit, ou clôt le bloc,
est le constat. Le décompte sert d'ANCRE même illisible. La zone de constat est
BORNÉE au prochain en-tête de résolution (garde-fou 600 chars) : sans borne, la
proclamation de la résolution suivante du même chunk contamine le verdict
(source des « contradictoires fantômes » et de 2 faux rejets de la revue 01/09).

Subtilité juridique (loi du 10/07/1965) : en article 25/26, « pour > contre » ne
suffit PAS à conclure l'adoption (majorité absolue) — seul le rejet est calculable.

Correctifs validés par audit corpus (33 152 groupes, 01/09) :
- formats forts « VOTENT POUR » (3 292 groupes) et « totalisant N tantièmes/voix »
  (3 297) — le compte est le nombre TOTALISÉ, pas le nombre de copropriétaires ;
- plusieurs mots-clés du même sens dans un cluster : les formes FORTES priment,
  puis le DERNIER gagne (les « 1er avril pour 40 % » d'un appel de fonds ne sont
  pas le décompte) ;
- « Néant/Aucun » (254) = 0, y compris collé/désaccentué par l'OCR (« CONTRENEANT »,
  « Nant ») ; participes désaccentués (« EST ADOPTE LA MAJORIT », 125) acceptés en
  zone post-ancre avec auxiliaire ou préfixe RÉSOLUTION obligatoire ;
- formulaire vierge « ADOPTEE/REJETEE … MAJORITE/UNANIMITE » (25) : jamais un
  constat → flag formulaire_vierge ;
- « Résolution REVOTÉE à l'article 25.1 » (108, procédure légale standard) : le
  résultat n'est PAS dans ce bloc → indetermine + flag revote_25_1 ;
- « N voix POUR » (nombre avant le mot-clé, 20 cas) en repli.

API :
    index_resolution(text) -> dict (une résolution)
    index_document(doc_chunks) -> résolutions RECONSTITUÉES d'un document
    group_chunks / index_chunks : utilitaires C2.
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
# "POUR" seul est trop ambigu en français : l'ancre exige au moins 2 mots-clés
# distincts dans une même fenêtre, ou 1 mot-clé pour/contre + "TANTIEMES".
_KW_POUR_STRONG = r"ONT\s+VOTE\s+POUR|VOTENT\s+POUR|VOTES?\s+POUR|VOIX\s+POUR"
_KW_CONTRE_STRONG = r"ONT\s+VOTE\s+CONTRE|VOTENT\s+CONTRE|VOTES?\s+CONTRE|VOIX\s+CONTRE"
_KW_POUR = re.compile(rf"\b(?:{_KW_POUR_STRONG}|POUR\s*[:\-–]|POUR\s+\d)")
_KW_CONTRE = re.compile(
    rf"\b(?:{_KW_CONTRE_STRONG}|CONTRE\s*[:\-–]|CONTRE\s+\d|\d\s+CONTRE\b|CONTRE(?=\s*:?\s*N[E]?ANT\b))")
_KW_STRONG = re.compile(rf"\b(?:{_KW_POUR_STRONG}|{_KW_CONTRE_STRONG})")
_KW_ABST = re.compile(
    r"\bABSTENTIONS?(?=N[E]?ANT\b)|\bABSTENTIONS?\b|\bS['’]ABSTIEN\w*|\bABSTENANTS?\b")
_KW_TANT = re.compile(r"\bTANTIEMES?\b|\bMILLIEMES?\b|/\s*10\s*000|\bVOIX\b")
# Marqueurs des PV tabulaires (ATHOME et similaires) : vote détaillé PAR
# copropriétaire en colonnes — les nombres proches d'un POUR/CONTRE nu sont des
# miettes de tableau, pas les totaux.
_TABULAR_RE = re.compile(r"BASE\s+DE\s+CALCUL|TYPE\s+DE\s+VOTE")
_ANCHOR_WINDOW = 320   # deux mots-clés à moins de N chars = même décompte
_NUM_AFTER = 80        # distance max mot-clé -> nombre associé
# NB : utilisées via .match(s, pos) qui ancre déjà à pos — PAS de ^ (avec
# .match(pos), ^ n'ancre qu'à la position 0 de la chaîne : piège Python).
_NEANT_RE = re.compile(r"[\s:—\-–]{0,8}(?:N[E]?ANTS?|AUCUNE?)\b")
_NUM_RE = re.compile(r"\d[\d\s., ]*\d|\d")
# c5 : « <n> copropriétaires … totalisant <N> [tantièmes|voix] » -> le compte = N
_QUALIF_RE = re.compile(r"\s*(?:COPROPRI[E]?TAIRES?|MEMBRES?)\b")
_TOTALISANT_RE = re.compile(r"TOTALISANT\s*:?\s*(\d[\d\s., ]*\d|\d)")
# c6 (repli) : « <N> voix POUR »
_VOIX_AVANT = {
    "pour": re.compile(r"(\d[\d\s]{0,9})\s*VOIX\s+POUR\b"),
    "contre": re.compile(r"(\d[\d\s]{0,9})\s*VOIX\s+CONTRE\b"),
    "abstention": re.compile(r"(\d[\d\s]{0,9})\s*VOIX\s+ABSTENTIONS?\b"),
}


def _parse_num(raw):
    """'2 606' / '2.606' / '2606,50' -> float. None si rien d'exploitable."""
    s = raw.replace(" ", " ").strip()
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
    """Positions des mots-clés de décompte : (start, end, kind, fort)."""
    anchors = []
    for kind, rx in (("pour", _KW_POUR), ("contre", _KW_CONTRE),
                     ("abstention", _KW_ABST), ("tantiemes", _KW_TANT)):
        for m in rx.finditer(tn):
            fort = bool(kind in ("pour", "contre") and _KW_STRONG.match(tn, m.start()))
            anchors.append((m.start(), m.end(), kind, fort))
    return sorted(anchors)


def _cluster_anchors(anchors):
    """Groupes de mots-clés proches ; retenu = le DERNIER cluster portant
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
        return {k for _, _, k, _ in c}

    valid = [c for c in clusters
             if len(kinds(c) - {"tantiemes"}) >= 2 or
             (kinds(c) & {"pour", "contre"} and "tantiemes" in kinds(c))]
    return valid[-1] if valid else None


def _value_after(tn, end, limit):
    """Valeur de décompte après un mot-clé : Néant/Aucun = 0, sinon premier nombre
    (borné), avec redirection « totalisant » (le compte est le nombre TOTALISÉ,
    pas le nombre de copropriétaires/membres) et garde « base de calcul »."""
    if _NEANT_RE.match(tn, end):
        return 0.0
    m = _NUM_RE.search(tn, end, limit)
    if not m:
        return None
    ctx = tn[max(0, m.start() - 45):m.start()]
    if "BASE DE CALCUL" in ctx or "BASE DE" in ctx[-12:]:
        return None
    if _QUALIF_RE.match(tn, m.end()):
        t = _TOTALISANT_RE.search(tn, m.end(), min(len(tn), m.end() + 160))
        if t:
            return _parse_num(t.group(1))
    return _parse_num(m.group(0))


def _extract_counts(tn, cluster):
    """Compte par sens de vote. Par kind : les mots-clés FORTS priment sur les
    faibles, puis le DERNIER gagne (élimine les « pour 40 % » d'appels de fonds
    en amont du vrai décompte). Repli « N voix POUR » si un sens manque."""
    by_kind = {}
    for i, (start, end, kind, fort) in enumerate(cluster):
        if kind == "tantiemes":
            continue
        by_kind.setdefault(kind, []).append((i, start, end, fort))
    counts = {}
    for kind, cands in by_kind.items():
        if any(f for _, _, _, f in cands):
            cands = [c for c in cands if c[3]]
        i, start, end, _f = cands[-1]           # le dernier gagne
        limit = min(cluster[i + 1][0], end + _NUM_AFTER) if i + 1 < len(cluster) \
            else min(len(tn), end + 20)
        val = _value_after(tn, end, limit)
        if val is not None:
            counts[kind] = val
    if cluster:
        span_a = max(0, cluster[0][0] - 40)
        span_b = min(len(tn), cluster[-1][1] + 40)
        for kind, rx in _VOIX_AVANT.items():
            if kind not in counts:
                m = None
                for m in rx.finditer(tn, span_a, span_b):
                    pass
                if m:
                    val = _parse_num(m.group(1))
                    if val is not None:
                        counts[kind] = val
    return counts


# ── Article de majorité ──
_ARTICLE_RE = re.compile(r"ART(?:ICLE)?\.?\s*(2[456])(?:\s*[-.]\s*1)?")


def _find_article(tn):
    m = None
    for m in _ARTICLE_RE.finditer(tn):
        pass
    return m.group(1) if m else None


# ── Proclamation (canal B) ──
# Deux registres :
# - APRÈS une ancre décompte, le POSITIONNEL prime : toute forme vaut constat,
#   y compris ACTIVE (« en conséquence, l'assemblée approuve… ») et les formes
#   DÉSACCENTUÉES par l'OCR (avec auxiliaire ou préfixe RÉSOLUTION obligatoire) ;
# - SANS ancre (clôture de bloc), seules les formes de CONSTAT participiales
#   strictes sont retenues — un verbe actif y serait le dispositif.
_NEG = r"(?:N['’]EST\s+PAS|NON|PAS)"
_P_REJ_PASSIF = (rf"(?:EST\s+|ETE\s+)?(?:{_NEG}\s+ADOPTEE?S?|{_NEG}\s+APPROUVEE?S?|"
                 r"REJETEES?|REFUSEES?)")
_P_REJ_ACTIF = r"L['’]ASSEMBLEE(?:\s+GENERALE)?\s+(?:REJETTE|REFUSE|N['’]APPROUVE\s+PAS)"
_P_REJ_DESACC = (r"(?:RE?SOLUTION\s+(?:EST\s+|ETE\s+)?|(?:EST|ETE|A\s+ETE|A\s+T)\s+)"
                 r"(?:REJETEE?|REFUSEE?)S?\b")
_P_ADO_PASSIF = r"(?:EST\s+|ETE\s+)?(?:ADOPTEES?|APPROUVEES?|(?<![A-Z])VOTEES?|ENTERINEES?)"
_P_ADO_ACTIF = r"L['’]ASSEMBLEE(?:\s+GENERALE)?\s+(?:APPROUVE|ADOPTE|DECIDE|ENTERINE|AUTORISE)"
_P_ADO_DESACC = (r"(?:RE?SOLUTION\s+(?:EST\s+|ETE\s+)?|(?:EST|ETE|A\s+ETE|A\s+T)\s+)"
                 r"(?:ADOPTEE?|APPROUVEE?|ENTERINEE?)S?\b")
_PREFIX = r"(?:RE?SOLUTION|DELIBERATION|PROPOSITION)?\s*"
_PROC_REJET_FULL = re.compile(_PREFIX + rf"(?:{_P_REJ_PASSIF}|{_P_REJ_ACTIF}|{_P_REJ_DESACC})")
_PROC_ADOPT_FULL = re.compile(_PREFIX + rf"(?:{_P_ADO_PASSIF}|{_P_ADO_ACTIF}|{_P_ADO_DESACC})")
_PROC_REJET_CONSTAT = re.compile(_PREFIX + _P_REJ_PASSIF)
_PROC_ADOPT_CONSTAT = re.compile(_PREFIX + _P_ADO_PASSIF)
_PROC_UNANIME = re.compile(r"A\s+L['’]UNANIMITE")
_RETIREE_RE = re.compile(
    r"RETIREE?\s+DE\s+L['’]ORDRE\s+DU\s+JOUR|N['’]EST\s+PAS\s+SOUMISE?\s+AU\s+VOTE|"
    r"IL\s+N['’]EST\s+PAS\s+PROCEDE\s+AU\s+VOTE|SANS\s+OBJET|NON\s+SOUMISE?\s+AU\s+VOTE")
# c1 : formulaire vierge — les deux sens séparés par « / » ne sont jamais un constat
_FORMULAIRE_RE = re.compile(
    r"ADOPTEE?\s*/\s*REJETEE?|REJETEE?\s*/\s*ADOPTEE?|"
    r"(?:LA\s+)?MAJORITE?\s*/\s*L?['’]?UNANIMITE?|UNANIMITE?\s*/\s*(?:LA\s+)?MAJORITE?")
# c2 : revote art. 25-1 — le résultat n'est pas dans ce bloc
_REVOTE_RE = re.compile(r"\bREVOTEE?S?\b")
_CLOSING_ZONE = 260    # sans ancre : la proclamation se cherche en clôture du bloc

# c7 : borne SÉMANTIQUE de la zone de constat — le prochain en-tête de résolution
# (numéro + titre en majuscules en début de ligne, ou après un saut de page),
# hors mots de décompte et dates ; garde-fou dur à _ZONE_MAX chars.
_HEADER_RE = re.compile(
    r"(?:\n[\s>]{0,6}|PAGE\s+\d{1,3}\s*(?:/|SUR)\s*\d{1,3}\D{0,8})"
    r"\d{1,2}(?:[-.]\d{1,2})?\s*[-–—.:/]?\s+"
    r"(?!(?:COPROPRI[E]?TAIRES?|MEMBRES?|TANTIEMES?|VOIX|PRESENTS?|BATIMENTS?|SUR|"
    r"JANVIER|FEVRIER|MARS|AVRIL|MAI|JUIN|JUILLET|AOUT|SEPTEMBRE|OCTOBRE|NOVEMBRE|DECEMBRE)\b)"
    r"[A-Z]{4,}")
_ZONE_MAX = 600


def _zone_end(tn, zone_start):
    m = _HEADER_RE.search(tn, zone_start, min(len(tn), zone_start + _ZONE_MAX + 80))
    if m:
        return m.start()
    return min(len(tn), zone_start + _ZONE_MAX)


def _find_proclamation(tn, zone_start, zone_stop=None, allow_active=True):
    """Constat dans tn[zone_start:zone_stop]. Le rejet se teste AVANT l'adoption
    ("n'est pas adoptée" matcherait aussi ADOPTEE). Retourne (sens, pos) ou None."""
    zone = tn[zone_start:zone_stop]
    rej_rx = _PROC_REJET_FULL if allow_active else _PROC_REJET_CONSTAT
    ado_rx = _PROC_ADOPT_FULL if allow_active else _PROC_ADOPT_CONSTAT
    m_rej = rej_rx.search(zone)
    m_ado = ado_rx.search(zone)
    if m_rej and m_ado:
        if abs(m_rej.start() - m_ado.start()) < 20 or m_rej.start() <= m_ado.start():
            return ("rejetee", zone_start + m_rej.start())
        return ("adoptee", zone_start + m_ado.start())
    if m_rej:
        return ("rejetee", zone_start + m_rej.start())
    if m_ado:
        return ("adoptee", zone_start + m_ado.start())
    if _PROC_UNANIME.search(zone):
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

    calc, calc_note, proc, revote = None, None, None, False
    if cluster:
        counts = _extract_counts(tn, cluster)
        # Plausibilité : (a) en format tabulaire, un décompte issu de POUR/CONTRE
        # nus est une miette de tableau -> formes fortes exigées ;
        # (b) pour+contre = 0 n'est jamais un vote réel.
        if counts and _TABULAR_RE.search(tn):
            strong_zone = any(f for _s, _e, k, f in cluster if k in ("pour", "contre"))
            if not strong_zone:
                counts = {}
        if counts and (counts.get("pour") is not None and counts.get("contre") is not None
                       and counts["pour"] + counts["contre"] == 0):
            counts = {}
        cluster_end = cluster[-1][1]
        # la zone de constat commence après le dernier mot-clé ; seul le nombre
        # IMMÉDIATEMENT adjacent est sauté (une fenêtre large avalerait le « 24 »
        # de « l'article 24 » de la proclamation)
        tail = _NUM_RE.match(tn, cluster_end) or (
            re.compile(r"[\s:—\-–]{1,6}").match(tn, cluster_end) and
            _NUM_RE.match(tn, re.compile(r"[\s:—\-–]{1,6}").match(tn, cluster_end).end()))
        zone_start = tail.end() if tail else cluster_end
        zone_stop = _zone_end(tn, zone_start)
        if counts:
            res["decompte"] = counts
            calc, calc_note = _compute_result(counts, res["article_majorite"])
        else:
            res["flags"].append("decompte_illisible")   # ancre présente, nombres morts
        zone_txt = tn[zone_start:zone_stop]
        if _FORMULAIRE_RE.search(zone_txt):
            res["flags"].append("formulaire_vierge")    # gabarit à trous, pas un constat
        elif _REVOTE_RE.search(zone_txt):
            revote = True
            res["flags"].append("revote_25_1")          # le résultat est au bloc suivant
        else:
            proc = _find_proclamation(tn, zone_start, zone_stop)
        if proc is None and not counts and not revote \
                and _find_proclamation(tn, 0, zone_start):
            res["flags"].append("ordre_anormal")        # constat avant l'ancre (colonnes)
        if len(tn) - zone_start < 5 and proc is None and not counts:
            res["flags"].append("resolution_tronquee")  # le texte meurt sur l'ancre
    else:
        close_start = max(0, len(tn) - _CLOSING_ZONE)
        if _FORMULAIRE_RE.search(tn, close_start):
            res["flags"].append("formulaire_vierge")
        elif _REVOTE_RE.search(tn, close_start):
            revote = True
            res["flags"].append("revote_25_1")
        else:
            # pas d'ancre : formes de CONSTAT strictes seulement, en clôture
            proc = _find_proclamation(tn, close_start, allow_active=False)

    if proc:
        res["proclamation_detectee"] = proc[0]
    if calc_note and calc_note != "article_inconnu":
        res["flags"].append(calc_note)

    # ── Réconciliation ──
    if revote:
        return res                                       # indetermine, flag revote_25_1
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
    """rows : iterable de (chunk_id, source_file, date, texte). Analyses enrichies
    des métadonnées."""
    out = []
    for chunk_id, source_file, date, text in rows:
        r = index_resolution(text)
        r.update(chunk_id=chunk_id, source_file=source_file, date=str(date) if date else None)
        out.append(r)
    return out


# ════════════════════════════════════════════════════════════════
# C2 — Regroupement des fragments par RÉSOLUTION (les longues résolutions sont
# éclatées par 03_chunking, qui préfixe chaque sous-chunk de « [Suite résolution
# {header}] » — marqueur généré par NOTRE chunker, donc fiable). Le KPI de
# détection se mesure par résolution reconstituée, pas par chunk.
# ════════════════════════════════════════════════════════════════

_SUITE_RE = re.compile(r"^\s*\[SUITE RESOLUTION\b")  # à tester sur _norm(texte)

_ORDINAUX = {
    "PREMIERE": 1, "DEUXIEME": 2, "SECONDE": 2, "TROISIEME": 3, "QUATRIEME": 4,
    "CINQUIEME": 5, "SIXIEME": 6, "SEPTIEME": 7, "HUITIEME": 8, "NEUVIEME": 9,
    "DIXIEME": 10, "ONZIEME": 11, "DOUZIEME": 12, "TREIZIEME": 13,
    "QUATORZIEME": 14, "QUINZIEME": 15, "SEIZIEME": 16, "DIX-SEPTIEME": 17,
    "DIX-HUITIEME": 18, "DIX-NEUVIEME": 19, "VINGTIEME": 20,
    "VINGT-ET-UNIEME": 21, "VINGT ET UNIEME": 21, "VINGT-DEUXIEME": 22,
    "VINGT-TROISIEME": 23, "VINGT-QUATRIEME": 24, "VINGT-CINQUIEME": 25,
    "VINGT-SIXIEME": 26, "VINGT-SEPTIEME": 27, "VINGT-HUITIEME": 28,
    "VINGT-NEUVIEME": 29, "TRENTIEME": 30,
}
_ORD_RE = re.compile(r"\b(" + "|".join(sorted(_ORDINAUX, key=len, reverse=True))
                     + r")\s+RESOLUTION")
_NUMDOT_RE = re.compile(r"^\s*(\d{1,3}(?:[-.]\d{1,2})?)\s*[-–—.: ]\s*\S")
_RESNUM_RE = re.compile(r"RESOLUTION\s*(?:N\s*°|NO|NUM(?:ERO)?)?\s*[.:]?\s*(\d{1,3})")
_NUMRES_RE = re.compile(r"\b(\d{1,3})\s*(?:E|EME|ERE)?\s*RESOLUTION")
# c3 : un nombre suivi d'un mot de décompte n'est pas un numéro de résolution
_NUM_EXCLU_RE = re.compile(
    r"\s*(?:COPROPRI[E]?TAIRES?|MEMBRES?|TANTIEMES?|VOIX|PRESENTS?|BATIMENTS?)\b")


def _extract_numero(head_norm):
    """Numéro de résolution depuis la tête du texte normalisé (best effort)."""
    m = _ORD_RE.search(head_norm[:160])
    if m:
        return str(_ORDINAUX[m.group(1)])
    for rx in (_RESNUM_RE, _NUMRES_RE):
        m = rx.search(head_norm[:160])
        if m:
            return m.group(1)
    m = _NUMDOT_RE.match(head_norm)          # formats tabulaires « 17-1 SONDAGE… »
    if m and not _NUM_EXCLU_RE.match(head_norm, m.end(1)):
        return m.group(1)
    return None


def _objet_court(text):
    """Première ligne significative du groupe, marqueur de suite retiré. Déterministe
    (pas de titrage LLM en C2 : suffisant pour l'annuaire, zéro risque)."""
    for line in text.split("\n"):
        line = re.sub(r"^\s*\[Suite résolution[^\]]*\]\s*", "", line,
                      flags=re.IGNORECASE).strip()
        if len(line) >= 8:
            return line[:140]
    return (text.strip()[:140] or None)


def group_chunks(doc_chunks):
    """doc_chunks : [(chunk_id, chunk_index, text)] d'UN document, triés par
    chunk_index. Un chunk « [Suite résolution …] » se rattache au précédent."""
    groups = []
    for row in sorted(doc_chunks, key=lambda r: (r[1] if r[1] is not None else 0)):
        text = row[2] or ""
        if groups and _SUITE_RE.match(_norm(text[:60])):
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups


def index_document(doc_chunks):
    """Regroupe les chunks d'un document PV et indexe chaque RÉSOLUTION reconstituée
    (texte concaténé du groupe)."""
    out = []
    for group in group_chunks(doc_chunks):
        full_text = "\n".join((r[2] or "") for r in group)
        r = index_resolution(full_text)
        r["chunk_ids"] = [g[0] for g in group]
        r["numero"] = _extract_numero(_norm(full_text))
        r["objet_court"] = _objet_court(full_text)
        if _SUITE_RE.match(_norm((group[0][2] or "")[:60])):
            r["flags"].append("groupe_orphelin")
        out.append(r)
    return out
