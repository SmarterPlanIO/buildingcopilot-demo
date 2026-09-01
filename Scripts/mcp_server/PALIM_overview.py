"""
PALIM_overview.py — Fiche synthèse d'une copropriété (lookup direct, pas de génération).

Lit la table `copro_synthese` pré-calculée par 09_copro_synthese.py (narratif Haiku +
faits SQL). Le tool MCP PALIM_copro_overview y ajoute la synthèse assurance LIVE (Assynco).

Fraîcheur : la fiche fige une photo à `generated_at`. On recalcule un watermark live
depuis la DB (nb_documents, dernier_pv_date, dossiers Airtable) et on flague `stale` si
ça diverge du stocké. La dimension Airtable se compare en plus au compte Assynco live
(passé par le serveur via `assynco_nb_sinistres`) : c'est ce qui capte un nouvel incident
déclaré dans Airtable entre deux runs du pipeline.

Module read-only, self-contained (aucune dépendance au pipeline d'ingestion).
"""


def _scalar(cur, sql, params):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _collect_live(conn, code):
    """Faits + watermark recalculés en live depuis la DB (read-only)."""
    with conn.cursor() as cur:
        nom = _scalar(cur, "SELECT MAX(copropriete) FROM documents WHERE code_ncg = %s", (code,))
        nb_documents = int(_scalar(cur,
            "SELECT COUNT(DISTINCT source_file) FROM documents WHERE code_ncg = %s", (code,)) or 0)
        nb_chunks = int(_scalar(cur,
            "SELECT COUNT(*) FROM chunks WHERE code_ncg = %s", (code,)) or 0)

        cur.execute("SELECT MIN(annee), MAX(annee) FROM documents WHERE code_ncg = %s", (code,))
        annee_min, annee_max = cur.fetchone() or (None, None)

        cur.execute("""
            SELECT COALESCE(doc_type_corrige, doc_type) AS dt, COUNT(DISTINCT source_file)
            FROM documents WHERE code_ncg = %s GROUP BY dt ORDER BY 2 DESC
        """, (code,))
        doc_types = {r[0]: int(r[1]) for r in cur.fetchall() if r[0]}

        cur.execute("""
            SELECT date_document, nom_fichier, source_file FROM documents
            WHERE code_ncg = %s AND COALESCE(doc_type_corrige, doc_type) = 'PV_AG'
            ORDER BY date_document DESC NULLS LAST, annee DESC NULLS LAST LIMIT 5
        """, (code,))
        pv_recents = [{"date": str(r[0]) if r[0] else None, "nom_fichier": r[1],
                       "source_file": r[2]} for r in cur.fetchall()]

        dernier_pv_date = _scalar(cur, """
            SELECT MAX(date_document) FROM documents
            WHERE code_ncg = %s AND COALESCE(doc_type_corrige, doc_type) = 'PV_AG'
        """, (code,))

        cur.execute("""
            SELECT statut, type_dossier, (airtable_record_id IS NOT NULL) AS is_at
            FROM dossiers WHERE code_ncg = %s
        """, (code,))
        rows = cur.fetchall()

    nb_dossiers = len(rows)
    nb_sinistres_assynco = sum(1 for r in rows if r[2])
    par_statut, par_type = {}, {}
    for statut, typ, _ in rows:
        if statut:
            par_statut[statut] = par_statut.get(statut, 0) + 1
        if typ:
            par_type[typ] = par_type.get(typ, 0) + 1

    faits = {
        "nom": nom, "nb_documents": nb_documents, "nb_chunks": nb_chunks,
        "annee_min": annee_min, "annee_max": annee_max, "doc_types": doc_types,
        "pv_ag_recents": pv_recents,
        "dossiers": {"total": nb_dossiers, "sinistres_assynco": nb_sinistres_assynco,
                     "par_statut": par_statut, "par_type": par_type},
    }
    watermark = {"nom": nom, "nb_documents": nb_documents, "nb_dossiers": nb_dossiers,
                 "nb_sinistres_assynco": nb_sinistres_assynco, "dernier_pv_date": dernier_pv_date}
    return faits, watermark


def _freshness(stored, live_wm, assynco_nb_sinistres):
    """Compare le watermark stocké au live. Retourne {stale, reasons}.

    stored : ligne copro_synthese (ou None). live_wm : watermark live DB.
    assynco_nb_sinistres : compte sinistres Assynco LIVE (ou None si indisponible).
    """
    reasons = []
    if stored is None:
        return {"stale": True, "reasons": ["non_precalculee"]}

    s_docs, s_pv, s_sin = stored["nb_documents"], stored["dernier_pv_date"], stored["nb_sinistres_assynco"]
    if live_wm["nb_documents"] != s_docs:
        delta = (live_wm["nb_documents"] or 0) - (s_docs or 0)
        reasons.append(f"documents_modifies ({'+' if delta >= 0 else ''}{delta})")
    if live_wm["dernier_pv_date"] != s_pv:
        reasons.append("nouveau_pv_ag")
    # Côté Airtable : DB synchronisée (post-08) ET compte live Assynco si fourni.
    if live_wm["nb_sinistres_assynco"] != s_sin:
        reasons.append("dossiers_assynco_resynchronises")
    if assynco_nb_sinistres is not None and s_sin is not None and assynco_nb_sinistres != s_sin:
        delta = assynco_nb_sinistres - s_sin
        reasons.append(f"sinistres_assynco_live ({'+' if delta >= 0 else ''}{delta})")
    return {"stale": bool(reasons), "reasons": reasons}


def _fetch_immatriculation(conn, code):
    """Attribut RNIC depuis le registre copros. None si table/ligne absente (dégradé)."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT immatriculation FROM copros WHERE code_ncg = %s", (code,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        try:
            conn.rollback()  # ne pas laisser la transaction abortée
        except Exception:
            pass
        return None


# Doctrine servie AVEC la fiche : elle vaut pour la v2 (annuaire) comme pour le
# narratif v1 encore servi aux tenants non migrés — c'est la leçon de l'incident
# du 27/08 (un narratif affirmait l'approbation de comptes en réalité rejetés).
_USAGE_V2 = ("ANNUAIRE : cette fiche oriente, elle n'établit rien. Suivre les pointeurs "
             "(chunk_ids, source_files, dossier_id, resolution_id) et lire les sources "
             "avant toute affirmation. Un sens de vote se vérifie sur le PV.")
_USAGE_V1 = ("Fiche de génération ANCIENNE (narratif rédigé automatiquement) : statut de "
             "source le plus bas. Ne JAMAIS en citer un sens de vote, une décision d'AG, "
             "un montant ni un comptage — revalider par recherche documentaire scopée.")


def _fetch_fiche_v2(conn, code):
    """(faits_v2, fiche_version, generated_at) ou (None, None, None) si le tenant
    n'a pas encore la fiche v2 (colonnes absentes = RDS non migrée)."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT faits_v2, fiche_version, fiche_v2_generated_at
                FROM copro_synthese WHERE code_ncg = %s
            """, (code,))
            row = cur.fetchone()
            return row if row else (None, None, None)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return (None, None, None)


def get_overview(conn, code, assynco_nb_sinistres=None):
    """Fiche d'une copro. Toujours {ok:True} : dégradé utile, jamais d'échec dur.

    Deux régimes selon ce que porte la base du tenant :
    - `fiche_version="v2"` : ANNUAIRE (identité, chiffres calculés, dossiers chauds,
      questions clés, PV récents), tout en pointeurs — champ `fiche`.
    - `fiche_version="v1"` : ancien narratif généré (tenant pas encore migré) —
      champs `narratif`/`faits`, servis avec un `avertissement` explicite.
    - `fiche_version="aucune"` : ni l'une ni l'autre, faits live seulement.
    """
    immatriculation = _fetch_immatriculation(conn, code)
    faits_v2, fiche_version, v2_generated_at = _fetch_fiche_v2(conn, code)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT nom, narratif, faits, nb_documents, nb_chunks, nb_dossiers,
                   nb_sinistres_assynco, dernier_pv_date, pv_sources, model_used,
                   cost_usd, generated_at
            FROM copro_synthese WHERE code_ncg = %s
        """, (code,))
        row = cur.fetchone()

    live_faits, live_wm = _collect_live(conn, code)
    stored = ({"nb_documents": row[3], "dernier_pv_date": row[7],
               "nb_sinistres_assynco": row[6]} if row else None)
    fresh = _freshness(stored, live_wm, assynco_nb_sinistres)
    base = {"ok": True, "code_ncg": code, "immatriculation": immatriculation,
            "nom": (row[0] if row else live_wm["nom"])}

    # ── Régime v2 : annuaire (zéro phrase générée) ──
    if faits_v2 and fiche_version == "v2":
        base.update({
            "fiche_version": "v2", "precomputed": True, "usage": _USAGE_V2,
            "fiche": faits_v2,
            "generated_at": str(v2_generated_at) if v2_generated_at else None,
            "freshness": {"stale": fresh["stale"], "reasons": fresh["reasons"],
                          "note": "les chiffres de la fiche datent de sa génération ; "
                                  "les pointeurs, eux, restent valides"},
        })
        return base

    # ── Régime v1 : ancien narratif, servi AVEC son avertissement ──
    if row and row[1]:
        base.update({
            "fiche_version": "v1", "precomputed": True,
            "avertissement": _USAGE_V1,
            "narratif": row[1], "faits": row[2],
            "generated_at": str(row[11]) if row[11] else None,
            "model_used": row[9],
            "freshness": {"stale": fresh["stale"], "reasons": fresh["reasons"],
                          "generated_at": str(row[11]) if row[11] else None},
        })
        return base

    # ── Ni v2 ni narratif : faits live seuls ──
    base.update({
        "fiche_version": "aucune", "precomputed": False,
        "narratif": None, "faits": (row[2] if row else live_faits),
        "generated_at": None,
        "freshness": {"stale": True, "reasons": fresh["reasons"],
                      "note": "Fiche non pré-calculée : faits live uniquement. "
                              "Lancer 09_copro_synthese.py --copro pour la générer."},
    })
    return base
