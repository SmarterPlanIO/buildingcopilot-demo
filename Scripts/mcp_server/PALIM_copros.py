"""
PALIM_copros.py — Annuaire des copropriétés (identité, pas retrieval).

Lit un registre copro (table `copros` si présente : nom_residence, adresse,
rue, aliases) + statistiques agrégées depuis documents/chunks/dossiers.
Fallback dégradé sur MAX(copropriete) de documents si la table copros est
absente (adresse/aliases alors omis).

query (optionnel) : fuzzy-match sur code/nom/rue/adresse/alias. Retourne des
CANDIDATS classés — jamais une résolution 1:1 (alias non uniques, cf.
08_airtable_sync.py COPRO_FILTERS l.32-34).
"""
import unicodedata

# copro_id : module produit partagé (Scripts/copro_id.py), vendorisé dans
# l'image Lambda par build_and_push.sh (copro_id_vendored.py).
try:
    from copro_id import display as _immat_display  # dev : module produit
except ImportError:  # pragma: no cover
    from copro_id_vendored import display as _immat_display  # type: ignore


def _norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def _fetch_doc_stats(conn):
    """Stats par copro depuis documents (toujours disponible)."""
    sql = """
        SELECT code_ncg,
               MAX(copropriete) AS nom,
               COUNT(DISTINCT source_file) AS nb_documents,
               array_remove(array_agg(DISTINCT COALESCE(doc_type_corrige, doc_type)), NULL) AS doc_types,
               MIN(annee) AS annee_min, MAX(annee) AS annee_max
        FROM documents
        WHERE code_ncg IS NOT NULL
        GROUP BY code_ncg
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return {r[0]: {"nom": r[1], "nb_documents": int(r[2]),
                       "doc_types": sorted([t for t in (r[3] or []) if t]),
                       "annee_min": r[4], "annee_max": r[5]}
                for r in cur.fetchall()}


def _fetch_chunk_counts(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT code_ncg, COUNT(*) FROM chunks WHERE code_ncg IS NOT NULL GROUP BY code_ncg")
        return {r[0]: int(r[1]) for r in cur.fetchall()}


def _fetch_dossier_copros(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT code_ncg FROM dossiers WHERE code_ncg IS NOT NULL")
            return {r[0] for r in cur.fetchall()}
    except Exception:
        return set()


def _fetch_registry(conn):
    """Registre copro optionnel. {} si la table est absente."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT code_ncg, nom_residence, adresse, rue, aliases, immatriculation FROM copros")
            return {r[0]: {"nom_residence": r[1], "adresse": r[2], "rue": r[3],
                           "aliases": list(r[4] or []), "immatriculation": r[5]}
                    for r in cur.fetchall()}
    except Exception:
        return {}


def _score(entry, qn):
    """Score de pertinence d'une copro vs requête normalisée qn."""
    if not qn:
        return 0
    haystacks = [_norm(entry["code_ncg"]), _norm(entry.get("nom")),
                 _norm(entry.get("adresse")), _norm(entry.get("rue"))]
    haystacks += [_norm(a) for a in entry.get("aliases", [])]
    if entry.get("immatriculation"):  # les 2 graphies : AA0000000 et AA0-000-000
        haystacks += [_norm(entry["immatriculation"]),
                      _norm(_immat_display(entry["immatriculation"]))]
    score = 0
    for h in haystacks:
        if not h:
            continue
        if h == qn:
            score += 10
        elif qn in h or h in qn:
            score += 5
        elif set(qn.split()) & set(h.split()):
            score += 1
    return score


def list_copros(conn, query=None):
    """Retourne {ok, copros:[...]} (candidats classés si query)."""
    stats = _fetch_doc_stats(conn)
    chunk_counts = _fetch_chunk_counts(conn)
    dossier_copros = _fetch_dossier_copros(conn)
    registry = _fetch_registry(conn)

    entries = []
    for code, s in stats.items():
        reg = registry.get(code, {})
        doc_types = s["doc_types"]
        entry = {
            "code_ncg": code,
            "nom": reg.get("nom_residence") or s["nom"],
            "nb_documents": s["nb_documents"],
            "nb_chunks": chunk_counts.get(code, 0),
            "doc_types_available": doc_types,
            "annee_min": s["annee_min"],
            "annee_max": s["annee_max"],
            "has_rcp": "RCP" in doc_types,
            "has_pv_ag": "PV_AG" in doc_types,
            "has_dossiers": code in dossier_copros,
        }
        # adresse/aliases/immatriculation : optionnels (omis si registre absent)
        if reg.get("adresse"):
            entry["adresse"] = reg["adresse"]
        if reg.get("rue"):
            entry["rue"] = reg["rue"]
        if reg.get("aliases"):
            entry["aliases"] = reg["aliases"]
        if reg.get("immatriculation"):
            entry["immatriculation"] = reg["immatriculation"]
        entries.append(entry)

    qn = _norm(query)
    if qn:
        scored = [(_score({**e, "aliases": e.get("aliases", [])}, qn), e) for e in entries]
        matched = [e for sc, e in sorted(scored, key=lambda x: x[0], reverse=True) if sc > 0]
        # Si aucun match, renvoyer la liste complète (Claude reste informé)
        result = matched if matched else sorted(entries, key=lambda e: e["code_ncg"])
    else:
        result = sorted(entries, key=lambda e: e["code_ncg"])

    return {"ok": True, "copros": result}
