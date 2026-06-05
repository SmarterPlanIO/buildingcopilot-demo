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


def get_overview(conn, code, assynco_nb_sinistres=None):
    """Fiche synthèse d'une copro. Toujours {ok:True} : dégradé utile si non pré-calculée.

    Retourne narratif + faits + fraîcheur. Si la fiche n'existe pas encore, renvoie les
    faits live (SQL) avec precomputed=False et narratif=None (jamais d'échec dur).
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT nom, narratif, faits, nb_documents, nb_chunks, nb_dossiers,
                   nb_sinistres_assynco, dernier_pv_date, pv_sources, model_used,
                   cost_usd, generated_at
            FROM copro_synthese WHERE code_ncg = %s
        """, (code,))
        row = cur.fetchone()

    live_faits, live_wm = _collect_live(conn, code)

    if not row:
        fresh = _freshness(None, live_wm, assynco_nb_sinistres)
        return {"ok": True, "code_ncg": code, "precomputed": False,
                "nom": live_wm["nom"], "narratif": None, "faits": live_faits,
                "generated_at": None,
                "freshness": {"stale": True, "reasons": fresh["reasons"],
                              "note": "Fiche non pré-calculée : faits live, sans narratif. "
                                      "Lancer 09_copro_synthese.py --copro pour générer le narratif."}}

    stored = {"nb_documents": row[3], "dernier_pv_date": row[7], "nb_sinistres_assynco": row[6]}
    fresh = _freshness(stored, live_wm, assynco_nb_sinistres)
    return {
        "ok": True, "code_ncg": code, "precomputed": True,
        "nom": row[0], "narratif": row[1], "faits": row[2],
        "generated_at": str(row[11]) if row[11] else None,
        "model_used": row[9],
        "freshness": {"stale": fresh["stale"], "reasons": fresh["reasons"],
                      "generated_at": str(row[11]) if row[11] else None},
    }
