"""
09 — dossiers_api.py — Couche d'accès aux données dossiers (sinistres Assynco/Airtable)
Lance standalone : python dossiers_api.py (test de connexion)
Importé par : streamlit_app.py

Contient toute la logique métier liée aux dossiers de sinistres.
Indépendant du framework UI — remplacer streamlit_app.py sans perdre cette logique.
"""
import re
import unicodedata
from typing import Optional, List, Dict, Tuple, Any, Union


# ──────────────────────────────────────────────────────────────
# LECTURE DB
# ──────────────────────────────────────────────────────────────

def _code_ncg_predicate(copropriete, col: str = "code_ncg") -> Tuple[str, list]:
    """Prédicat SQL de filtrage copro (sans mot-clé WHERE/AND).

    copropriete : None/[] = toutes (prédicat vide) ; str = une ; list/tuple = IN.
    Retourne (predicat, params).
    """
    if not copropriete:
        return "", []
    codes = [copropriete] if isinstance(copropriete, str) else [c for c in copropriete if c]
    if not codes:
        return "", []
    if len(codes) == 1:
        return f"{col} = %s", [codes[0]]
    return f"{col} IN (" + ",".join(["%s"] * len(codes)) + ")", codes


def get_dossiers(conn, copropriete: Optional[Union[str, List[str]]] = None) -> List[Tuple]:
    """Retourne les dossiers triés par statut puis date d'ouverture.

    Args:
        conn: Connexion psycopg2 active.
        copropriete: code_ncg à filtrer (ex: "5390"). None = toutes copros.

    Returns:
        Liste de tuples (dossier_id, nom_dossier, type_dossier, statut,
        date_ouverture, etapes, pieces_requises, pieces_fournies,
        lese_nom, expert_nom, assureur, montant_estime, ref_assynco, ref_cie).
    """
    try:
        with conn.cursor() as cur:
            _pred, _params = _code_ncg_predicate(copropriete, "code_ncg")
            where_sql = ("WHERE " + _pred) if _pred else ""
            cur.execute(f"""
                SELECT dossier_id, nom_dossier, type_dossier, statut,
                       date_ouverture, etapes, pieces_requises, pieces_fournies,
                       lese_nom, expert_nom, assureur, montant_estime,
                       ref_assynco, ref_cie
                FROM dossiers {where_sql}
                ORDER BY
                    CASE statut WHEN 'EN_ATTENTE' THEN 1 WHEN 'EN_COURS' THEN 2 ELSE 3 END,
                    date_ouverture DESC
            """, _params)
            return cur.fetchall()
    except Exception:
        return []


def get_dossier_detail(conn, dossier_id: str) -> Optional[Dict]:
    """Retourne un dossier complet (toutes colonnes) par ID.

    Args:
        conn: Connexion psycopg2 active.
        dossier_id: PK du dossier (ex: "AT_recXXX").

    Returns:
        Dict colonne→valeur ou None si non trouvé.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM dossiers WHERE dossier_id = %s", [dossier_id])
            row = cur.fetchone()
            if row:
                cols = [desc[0] for desc in cur.description]
                return dict(zip(cols, row))
            return None
    except Exception:
        return None


# Haystack accent-normalisé (translate, car l'extension unaccent n'est pas installée)
# pour le match langage naturel. Inclut garantie_impactee (text[], "DDE - Dégâts des
# Eaux") et cause, donc une requête "dégât des eaux" matche les dossiers de type DDE.
_ACCENT_FROM = "àáâãäçèéêëìíîïñòóôõöùúûüýÿ"
_ACCENT_TO = "aaaaaceeeeiiiinooooouuuuyy"
_DOSSIER_HAYSTACK_SQL = (
    "translate(lower("
    "coalesce(nom_dossier,'')||' '||coalesce(array_to_string(garantie_impactee,' '),'')"
    "||' '||coalesce(cause,'')||' '||coalesce(lese_nom,'')||' '||coalesce(type_dossier,'')"
    f"), '{_ACCENT_FROM}', '{_ACCENT_TO}')"
)
_DOSSIER_STOPWORDS = {"des", "les", "aux", "une", "sur", "par", "pour", "avec", "dans"}


def _nl_tokens(query: str) -> List[str]:
    """Tokens langage naturel d'une requête : minuscules, sans accents, >=3 car, hors stopwords."""
    out = []
    for w in query.split():
        t = "".join(c for c in unicodedata.normalize("NFKD", w) if not unicodedata.combining(c)).lower()
        if len(t) >= 3 and t not in _DOSSIER_STOPWORDS:
            out.append(t)
    return out


def search_dossiers_for_query(conn, query: str, copropriete: Optional[Union[str, List[str]]] = None) -> List[Dict]:
    """Recherche hybride keyword+regex dans la table dossiers.

    Extrait les références (A/I + digits) et noms propres de la requête, puis matche sur
    nom_dossier, lese_nom, ref_cie, ref_assynco, ref_sinistre_client. Les autres mots
    (langage naturel) sont matchés en ET, accents normalisés, sur garantie/cause/type
    en plus du nom : "dégât des eaux" trouve les dossiers de garantie "DDE".

    Args:
        conn: Connexion psycopg2 active.
        query: Texte de la requête utilisateur.
        copropriete: code_ncg à filtrer. None = toutes copros.

    Returns:
        Liste de dicts (jusqu'à 5 dossiers correspondants), ordre antichronologique.
    """
    try:
        with conn.cursor() as cur:
            where_parts = []
            params = []
            search_term = f"%{query}%"

            # Références : A/I suivis de chiffres (ex: A2110292, I24013811)
            refs = re.findall(r'\b[AI]\d{5,}[A-Z]*\b', query, re.IGNORECASE)
            # Noms propres : mots capitalisés avec accents
            names = re.findall(r'\b[A-Z][a-zéèêëàâùûôîïç]{2,}\b', query)

            if refs:
                for ref in refs:
                    where_parts.append(
                        "(nom_dossier ILIKE %s OR ref_cie ILIKE %s "
                        "OR ref_assynco ILIKE %s OR ref_sinistre_client ILIKE %s)"
                    )
                    rp = f"%{ref}%"
                    params.extend([rp, rp, rp, rp])

            if names:
                for name in names:
                    where_parts.append("(lese_nom ILIKE %s OR nom_dossier ILIKE %s)")
                    np = f"%{name}%"
                    params.extend([np, np])

            # Langage naturel : tokens (ET) sur un haystack accent-insensible incluant
            # garantie/cause/type, pour matcher p.ex. "dégât des eaux" -> garantie "DDE".
            nl = _nl_tokens(query)
            if nl:
                token_clause = " AND ".join(f"{_DOSSIER_HAYSTACK_SQL} ILIKE %s" for _ in nl)
                where_parts.append("(" + token_clause + ")")
                params.extend(f"%{t}%" for t in nl)

            # Recherche générale sur nom_dossier (toujours)
            where_parts.append("nom_dossier ILIKE %s")
            params.append(search_term)

            where_sql = " OR ".join(where_parts)
            copro_filter_sql = ""
            _pred, _pparams = _code_ncg_predicate(copropriete, "code_ncg")
            if _pred:
                copro_filter_sql = " AND " + _pred
                params.extend(_pparams)

            cur.execute(f"""
                SELECT *
                FROM dossiers
                WHERE ({where_sql}){copro_filter_sql}
                ORDER BY date_ouverture DESC NULLS LAST
                LIMIT 5
            """, params)

            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


# ──────────────────────────────────────────────────────────────
# CONSTRUCTION DU CHUNK VIRTUEL RAG
# ──────────────────────────────────────────────────────────────

def dossier_to_virtual_chunk(dossier: Dict, source_index: int = 1) -> Tuple:
    """Convertit un dossier Airtable en chunk virtuel au format RAG.

    Produit un tuple (10 éléments) identique au format retourné par search_chunks(),
    avec RRF score = 0.99 (priorité maximale, légèrement sous 1.0 pour distinguer
    des chunks parfaitement identiques à la requête).

    Args:
        dossier: Dict complet d'un enregistrement de la table dossiers.
        source_index: Indice de position (utilisé pour le chunk_id).

    Returns:
        Tuple (chunk_id, copro, source_file, nom_fichier, doc_type, text,
               vec_similarity, bm25, rrf, chunk_idx).
    """
    d = dossier
    sections = []

    # ── HEADER ──
    sections.append("=== DOSSIER SINISTRE — BASE ASSYNCO/AIRTABLE (SOURCE PRIORITAIRE) ===")
    sections.append(f"Nom du dossier : {d.get('nom_dossier', 'N/A')}")
    sections.append(f"Type : {d.get('type_dossier', 'N/A')}")
    sections.append(f"Statut : {d.get('at_situation') or d.get('statut', 'N/A')}")
    if d.get('statut_detail'):
        sections.append(f"Statut detail : {d['statut_detail']}")
    if d.get('triage'):
        sections.append(f"Priorite (triage) : {d['triage']}")

    # ── ALERTES & FLAGS ──
    flags = []
    if d.get('important'):
        flags.append("IMPORTANT")
    if d.get('judiciaire'):
        flags.append("JUDICIAIRE")
    if d.get('en_carence'):
        flags.append("EN CARENCE")
    if d.get('a_relancer') and str(d['a_relancer']).lower() not in ('non', 'no', 'false'):
        flags.append("A RELANCER")
    if d.get('prescription_status') and str(d['prescription_status']).lower() not in ('no', 'non', 'false'):
        flags.append(f"PRESCRIPTION: {d['prescription_status']}")
    if flags:
        sections.append(f"ALERTES : {' | '.join(flags)}")
    if d.get('elements_manquants'):
        els = d['elements_manquants'] if isinstance(d['elements_manquants'], list) else [d['elements_manquants']]
        sections.append(f"Elements manquants : {', '.join(els)}")

    # ── IDENTIFICATION DU SINISTRE ──
    sections.append("")
    sections.append("--- Identification ---")
    if d.get('date_ouverture'):
        sections.append(f"Date survenance : {d['date_ouverture']}")
    if d.get('date_cloture'):
        sections.append(f"Date cloture : {d['date_cloture']}")
    if d.get('cause'):
        sections.append(f"Cause : {d['cause']}")
    if d.get('irsi') is not None:
        sections.append(f"Convention IRSI : {'Oui' if d['irsi'] else 'Non'}")
    if d.get('cause_identifiee') is not None:
        sections.append(f"Cause identifiee : {'Oui' if d['cause_identifiee'] else 'Non'}")
    if d.get('cause_reparee') is not None:
        sections.append(f"Cause reparee : {'Oui' if d['cause_reparee'] else 'Non'}")
    if d.get('garantie_impactee'):
        g = d['garantie_impactee'] if isinstance(d['garantie_impactee'], list) else [d['garantie_impactee']]
        sections.append(f"Garantie : {', '.join(g)}")
    if d.get('dommage_copro') is not None:
        sections.append(f"Dommage copropriete : {'Oui' if d['dommage_copro'] else 'Non'}")
    if d.get('adresse_sinistre'):
        sections.append(f"Adresse sinistre : {d['adresse_sinistre']}")
    if d.get('adresse_copro'):
        sections.append(f"Adresse copropriete : {d['adresse_copro']}")

    # ── REFERENCES ──
    refs = []
    if d.get('ref_cie'):
        refs.append(f"Ref Compagnie: {d['ref_cie']}")
    if d.get('ref_expert'):
        refs.append(f"Ref Expert: {d['ref_expert']}")
    if d.get('ref_sinistre_client'):
        refs.append(f"Ref Client: {d['ref_sinistre_client']}")
    if d.get('ref_assynco'):
        refs.append(f"Ref Assynco: {d['ref_assynco']}")
    if d.get('ref_inch'):
        refs.append(f"Ref Inch: {d['ref_inch']}")
    if refs:
        sections.append(f"References : {' | '.join(refs)}")
    if d.get('airtable_url'):
        sections.append(f"Lien dossier Airtable : {d['airtable_url']}")

    # ── PARTIES PRENANTES (section omise si vide) ──
    _pp = []
    if d.get('lese_nom'):
        lese = f"Lese : {d['lese_nom']}"
        if d.get('lese_tel'):
            lese += f" | Tel: {d['lese_tel']}"
        if d.get('lese_email'):
            lese += f" | Email: {d['lese_email']}"
        if d.get('appt_origine'):
            lese += f" | Appt: {d['appt_origine']}"
        _pp.append(lese)
    if d.get('expert_nom'):
        _pp.append(f"Expert : {d['expert_nom']}")
    if d.get('etat_expert'):
        _pp.append(f"Etat expert : {d['etat_expert']}")
    if d.get('assureur'):
        _pp.append(f"Assureur : {d['assureur']}")
    if d.get('gestionnaire_syndic'):
        _pp.append(f"Gestionnaire syndic : {d['gestionnaire_syndic']}")
    if d.get('email_gestionnaire'):
        _pp.append(f"Email gestionnaire syndic : {d['email_gestionnaire']}")
    if d.get('tel_syndic'):
        _pp.append(f"Tel syndic : {d['tel_syndic']}")
    if d.get('adresse_syndic'):
        _pp.append(f"Adresse syndic : {d['adresse_syndic']}")
    if d.get('email_gestionnaire_sinistre'):
        _pp.append(f"Email gestionnaire sinistre (assureur) : {d['email_gestionnaire_sinistre']}")
    if d.get('tel_gestionnaire_sinistre'):
        _pp.append(f"Tel gestionnaire sinistre (assureur) : {d['tel_gestionnaire_sinistre']}")
    if _pp:
        sections.append("")
        sections.append("--- Parties prenantes ---")
        sections.extend(_pp)

    # ── PIPELINE / AVANCEMENT (section omise si vide) ──
    _pl = []
    pipeline_fields = [
        ('at_declaration', 'Declaration'),
        ('at_expertise', 'Expertise'),
        ('at_accord', 'Accord'),
        ('at_reglement', 'Reglement'),
        ('at_mise_en_cause', 'Mise en cause'),
    ]
    for key, label in pipeline_fields:
        if d.get(key):
            _pl.append(f"  {label} : {d[key]}")
    if d.get('at_attente'):
        _pl.append(f"En attente de : {d['at_attente']}")
    if d.get('situation_sinistre'):
        _pl.append(f"Situation sinistre : {d['situation_sinistre']}")
    if _pl:
        sections.append("")
        sections.append("--- Pipeline ---")
        sections.extend(_pl)

    # ── DATES CLES (section omise si vide) ──
    date_fields = [
        ('date_declaration', 'Declaration'),
        ('date_mission_expert', 'Mission expert'),
        ('date_invitation_expertise', 'Invitation expertise'),
        ('date_premiere_visite', 'Premiere visite'),
        ('date_pv', 'PV'),
        ('date_lettre_acceptation', 'Lettre acceptation'),
        ('date_depot_rapport', 'Depot rapport'),
        ('date_reglement', 'Reglement'),
        ('date_derniere_relance', 'Derniere relance'),
        ('date_relance_expert', 'Relance expert'),
        ('date_relance_compagnie', 'Relance compagnie'),
        ('date_relance_client', 'Relance client'),
        ('date_rappel', 'Rappel'),
        ('date_prescription', 'Prescription'),
        ('date_prescription_estimate', 'Prescription estimee'),
    ]
    dates_found = [(label, d[key]) for key, label in date_fields if d.get(key)]
    if dates_found:
        sections.append("")
        sections.append("--- Dates cles ---")
        for label, val in dates_found:
            sections.append(f"  {label} : {val}")

    # ── FINANCIER (section omise si vide) ──
    fin_fields = [
        ('montant_estime', 'Estimation'),
        ('montant_reel', 'Cout assureur'),
        ('franchise', 'Franchise'),
        ('provisions', 'Provisions'),
        ('reglement_realise', 'Reglement realise'),
        ('reglement_frais', 'Reglement frais'),
        ('recours_en_cours', 'Recours en cours'),
        ('recours_realise', 'Recours realise'),
        ('cout_client', 'Cout client'),
        ('honoraire_syndic', 'Honoraire syndic'),
        ('dommages', 'Dommages (montant)'),
        ('indemnite_immediate', 'Indemnite immediate'),
        ('indemnite_differee', 'Indemnite differee'),
        ('total_regle', 'Total regle'),
    ]
    fins_found = [(label, d[key]) for key, label in fin_fields if d.get(key)]
    if fins_found:
        sections.append("")
        sections.append("--- Financier ---")
        for label, val in fins_found:
            sections.append(f"  {label} : {val} EUR")

    # ── TEXTES DESCRIPTIFS ──
    text_fields = [
        ('circonstances', 'Circonstances', 1500),
        ('dommages_description', 'Description des dommages', 1500),
        ('conclusion_expert', 'Conclusion de l expert', 2000),
        ('observations_declaration', 'Observations declaration', 1000),
        ('commentaire_assureur', 'Commentaire assureur', 1500),
        ('commentaire_assynco', 'Commentaire Assynco', 1000),
        ('motif_rappel', 'Motif rappel', 600),
        ('commentaire_relance_expert', 'Commentaire relance expert', 600),
        ('commentaire_relance_compagnie', 'Commentaire relance compagnie', 600),
        ('commentaire_relance_client', 'Commentaire relance client', 600),
    ]
    texts_found = [(label, d[key][:maxlen]) for key, label, maxlen in text_fields if d.get(key)]
    if texts_found:
        sections.append("")
        sections.append("--- Textes ---")
        for label, val in texts_found:
            sections.append(f"{label} : {val}")

    text = "\n".join(sections)

    # Format identique aux résultats de search_chunks() :
    # (chunk_id, copro, source_file, nom_fichier, doc_type, text,
    #  vec_similarity, bm25, rrf, chunk_idx)
    return (
        f"airtable_{d['dossier_id']}",
        d.get('copropriete', ''),
        "AIRTABLE_ASSYNCO",
        f"Dossier Assynco: {d.get('nom_dossier', 'N/A')[:60]}",
        "SINISTRE_AIRTABLE",
        text,
        1.0,   # vec_similarity max (correspondance directe)
        1.0,   # bm25
        0.99,  # rrf (priorité maximale sans être exactement 1.0)
        0,     # chunk_idx
    )


# ──────────────────────────────────────────────────────────────
# ENRICHISSEMENT REQUÊTE ET FUSION
# ──────────────────────────────────────────────────────────────

def enrich_query_with_dossier(query: str, dossier_data: Dict) -> Tuple[str, Dict]:
    """Enrichit la requête RAG avec les identifiants uniques du dossier sélectionné.

    N'ajoute QUE les références uniques du dossier (ref_assynco, ref_cie) pour
    que BM25 retrouve les documents d'archives mentionnant explicitement ce dossier.
    On n'injecte PAS le nom du lésé ni les circonstances : ces termes sont trop
    génériques et contamineraient la recherche avec d'autres sinistres similaires
    (même lésé sur d'autres dossiers, même type de sinistre, etc.).
    Le chunk Airtable virtuel (Source 1) couvre déjà tout le contexte métier du dossier.

    Args:
        query: Requête originale de l'utilisateur.
        dossier_data: Dict complet du dossier (retourné par get_dossier_detail).

    Returns:
        (query_enrichie, overrides) où overrides = {
            "MCL": 3,        # max_chunks_llm — réduit : Airtable est la source principale
            "CPS": 1,        # chunks_per_source — 1 seul chunk par doc RAG
            "doc_type": str  # "SINISTRE" si c'est un sinistre, None sinon
        }
    """
    d = dossier_data
    parts = [query]

    # Enrichissement UNIQUEMENT par identifiants uniques du dossier.
    # Ces refs apparaissent dans les courriers/rapports archivés → aide BM25.
    # NE PAS ajouter lese_nom ni circonstances : trop génériques → pollution croisée.
    ref_assynco = d.get("ref_assynco") or ""
    if not ref_assynco and d.get("nom_dossier"):
        m = re.search(r'Ref:\s*(\w+)', d["nom_dossier"])
        if m:
            ref_assynco = m.group(1)
    if ref_assynco:
        parts.append(ref_assynco)

    if d.get("ref_cie"):
        parts.append(d["ref_cie"])

    query_enriched = " ".join(parts)

    # MCL=3 : le chunk Airtable (Source 1) fournit l'essentiel ; 3 chunks RAG suffisent
    overrides: Dict[str, Any] = {"MCL": 3, "CPS": 1, "doc_type": None}
    if d.get("type_dossier") and "SINISTRE" in (d["type_dossier"] or "").upper():
        overrides["doc_type"] = "SINISTRE"

    return query_enriched, overrides


def enrich_query_contextual(query: str, dossier_data: Dict) -> str:
    """Version élargie de l'enrichissement pour le retrieval contextuel.

    Ajoute les identifiants uniques (comme enrich_query_with_dossier) PLUS
    le nom du lésé et les circonstances, afin de retrouver des documents
    connexes : même lésé sur d'autres sinistres, même type de dommage,
    même zone du bâtiment, etc.

    Utilisée en tandem avec enrich_query_with_dossier() pour le double retrieval.
    Le résultat est passé à une deuxième requête search_decomposed() parallèle.
    Les chunks résultants sont étiquetés [CONTEXTE CONNEXE] dans le prompt LLM.

    Args:
        query: Requête originale de l'utilisateur.
        dossier_data: Dict complet du dossier (retourné par get_dossier_detail).

    Returns:
        Requête enrichie (str) pour le retrieval contextuel.
    """
    d = dossier_data
    parts = [query]

    ref_assynco = d.get("ref_assynco") or ""
    if not ref_assynco and d.get("nom_dossier"):
        m = re.search(r'Ref:\s*(\w+)', d["nom_dossier"])
        if m:
            ref_assynco = m.group(1)
    if ref_assynco:
        parts.append(ref_assynco)
    if d.get("ref_cie"):
        parts.append(d["ref_cie"])

    # Termes contextuels — volontairement plus larges que enrich_query_with_dossier
    # pour retrouver des sinistres connexes (même lésé, même nature de dommage)
    if d.get("lese_nom"):
        parts.append(d["lese_nom"])
    if d.get("circonstances"):
        parts.append(d["circonstances"][:40])

    return " ".join(parts)


def merge_with_airtable_chunks(
    results: List[Tuple],
    query: str,
    selected_dossier_data: Optional[Dict],
    copro_filter: Optional[Union[str, List[str]]],
    conn,
) -> List[Tuple]:
    """Fusionne les résultats RAG avec les chunks virtuels Airtable.

    1. Le dossier sélectionné est injecté en priorité absolue (Source 1).
    2. Les dossiers matchant textuellemement la requête sont ajoutés ensuite.
    3. Les doublons avec le dossier sélectionné sont supprimés.

    Args:
        results: Liste de tuples retournée par search_chunks/search_decomposed.
        query: Requête originale de l'utilisateur (pour search_dossiers_for_query).
        selected_dossier_data: Dict du dossier sélectionné dans la sidebar (ou None).
        copro_filter: code_ncg du filtre copropriété actif (ou None).
        conn: Connexion psycopg2 active.

    Returns:
        Nouvelle liste avec les chunks Airtable prépendés aux résultats RAG.
    """
    airtable_chunks = []

    # 1. Dossier sélectionné → toujours en Source 1
    if selected_dossier_data:
        airtable_chunks.append(dossier_to_virtual_chunk(selected_dossier_data, 1))

    # 2. Dossiers matchant la requête par texte (sans sélection manuelle)
    text_dossiers = search_dossiers_for_query(conn, query, copropriete=copro_filter)
    selected_id = selected_dossier_data.get("dossier_id") if selected_dossier_data else None
    for ad in text_dossiers:
        if ad.get("dossier_id") == selected_id:
            continue  # éviter le doublon avec le dossier sélectionné
        idx = len(results) + len(airtable_chunks) + 1
        airtable_chunks.append(dossier_to_virtual_chunk(ad, idx))

    if airtable_chunks:
        return airtable_chunks + list(results)
    return list(results)


# ──────────────────────────────────────────────────────────────
# TEST STANDALONE
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    import psycopg2

    # Creds via env (jamais en clair dans le code). Defaut = reader read-only.
    #   PYTHONIOENCODING=utf-8 DB_PASSWORD=... python dossiers_api.py
    DB_HOST = os.environ.get("DB_HOST", "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com")
    DB_PORT = int(os.environ.get("DB_PORT", "5432"))
    DB_NAME = os.environ.get("DB_NAME", "postgres")
    DB_USER = os.environ.get("DB_USER", "mcp_ncg_reader")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    if not DB_PASSWORD:
        raise SystemExit("DB_PASSWORD requis en variable d'environnement.")

    print("09_dossiers_api.py — Test de connexion")
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD, sslmode="require"
    )

    dossiers = get_dossiers(conn)
    print(f"Total dossiers : {len(dossiers)}")

    if dossiers:
        first_id = dossiers[0][0]
        detail = get_dossier_detail(conn, first_id)
        print(f"Premier dossier : {detail.get('nom_dossier')} ({detail.get('ref_assynco')})")

        chunk = dossier_to_virtual_chunk(detail, 1)
        print(f"Chunk virtuel : {len(chunk[5])} chars")

    results = search_dossiers_for_query(conn, "A2410592")
    print(f"Recherche 'A2410592' : {len(results)} résultats")

    conn.close()
    print("OK")
