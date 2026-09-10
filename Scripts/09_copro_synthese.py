"""
ÉTAPE 9 — Fiche de synthèse v2 : ANNUAIRE de la copropriété (C3, zéro LLM).

Remplace le narratif Haiku (incident du 27/08 : une fiche affirmait l'approbation
de comptes en réalité REJETÉS). Principe : **la fiche oriente, les sources
tranchent**. Elle ne raconte rien, elle présente ce qui est CALCULÉ et POINTE vers
ce qui doit être lu — le LLM appelant suit les pointeurs (get_chunks,
search_dossiers, get_full_document) et interprète avec ses garde-fous.

Quatre sections (cf. PLAN_FIABILITE_SYNTHESE.md §2) :
  identite       — nom, immatriculation, adresse + POINTEURS mandat/CS (jamais de
                   nom de personne extrait : ce serait génératif ; on pointe la
                   résolution, sa date, ses chunks) + champs_absents (honnêteté)
  chiffres_cles  — comptes SQL purs (documents, chunks, dossiers, résolutions)
  dossiers_chauds— sélection PAR RÈGLES avec motif_selection explicite + pointeurs
  questions_cles — dérivées PAR RÈGLES du structuré, JAMAIS des affirmations
  pv_recents     — PV datés + résolutions à résultat établi (table `resolutions`)

Écrit dans les colonnes NEUVES `faits_v2` / `fiche_version` / `fiche_v2_generated_at`.
`narratif` et `faits` (v1) ne sont PAS touchés : zéro impact sur la prod tant que
l'image MCP v12 n'est pas déployée (rollback = ignorer les colonnes v2).

Prérequis : 09b_resolutions.py (table `resolutions`) à jour pour cette copro.

Lance :
    DB_PASSWORD=... python 09_copro_synthese.py --copro 8050
    DB_PASSWORD=... python 09_copro_synthese.py --all
"""
import argparse
import json
import os
import re
from collections import Counter
from datetime import date

import psycopg2
from psycopg2.extras import Json

import pipeline_config as pcfg

FICHE_VERSION = "v2"
MAX_DOSSIERS_CHAUDS = 10      # au-delà, l'annuaire cesse d'orienter
MAX_PV_RECENTS = 5
MAX_RES_PAR_PV = 12
MAX_CHUNKS_ENTREE = 3         # pointeurs d'entrée par dossier
SINISTRE_ANCIEN_MOIS = 18     # règle « dossier qui traîne »
AG_MANQUANTE_MOIS = 18        # obligation d'AG annuelle (marge)

parser = argparse.ArgumentParser(description="Fiche de synthèse v2 (annuaire) par copro.")
g = parser.add_mutually_exclusive_group(required=True)
g.add_argument("--copro", help="Code copro (toute graphie)")
g.add_argument("--all", action="store_true", help="Toutes les copros du registre présentes en base")
args, _ = parser.parse_known_args()

DB_PASSWORD = os.environ.get("DB_PASSWORD")
if not DB_PASSWORD:
    raise SystemExit("❌ DB_PASSWORD manquant. Lance : DB_PASSWORD=... python 09_copro_synthese.py --all")

conn = psycopg2.connect(host=pcfg.require_db_host(), port=pcfg.DB_PORT, dbname=pcfg.DB_NAME,
                        user=pcfg.DB_USER_ADMIN, password=DB_PASSWORD)
cur = conn.cursor()

_ANNEE_RE = re.compile(r"\b(20[0-3]\d)\b")
_COMPTES_RE = re.compile(r"APPROBATION\s+DES\s+COMPTES|COMPTES\s+DE\s+L['’ ]?EXERCICE|"
                         r"APPROBATION\s+DU\s+COMPTE", re.IGNORECASE)
# Gouvernance : motifs PRECIS. « SYNDIC » nu attrape « demande du conseil syndical
# d'organiser une réunion avec le syndic » — inutile comme pointeur de mandat.
_SYNDIC_RE = re.compile(
    r"(?:DESIGNATION|NOMINATION|ELECTION|RENOUVELLEMENT|RECONDUCTION|CHOIX)"
    r"[^\n]{0,60}\b(?:SYNDIC|CABINET)\b|MANDAT\s+DU\s+SYNDIC|"
    r"CONTRAT\s+DE\s+SYNDIC|MANDAT\s+DU\s+CABINET", re.IGNORECASE)
_CS_RE = re.compile(
    r"(?:DESIGNATION|NOMINATION|ELECTION|RENOUVELLEMENT|COMPOSITION)"
    r"[^\n]{0,60}CONSEIL\s+SYNDICAL|MEMBRES?\s+DU\s+CONSEIL\s+SYNDICAL",
    re.IGNORECASE)
# NB : le %% est obligatoire — ce fragment est interpolé dans des requêtes
# exécutées AVEC paramètres, où psycopg2 traite un % isolé comme un placeholder.
_OUVERT_SQL = """
    (COALESCE(statut, '') NOT IN ('CLOTURE', 'CLOS', 'CLOTURÉ')
     AND COALESCE(at_situation, '') NOT ILIKE 'clos%%')
"""


def _months_between(d1, d2):
    return (d2.year - d1.year) * 12 + (d2.month - d1.month)


def _pointeur_resolution(row):
    """{resolution_id, date_ag, numero, objet_court, chunk_ids, resultat} — jamais
    le contenu, toujours de quoi aller le lire."""
    rid, dag, num, objet, chunks, res, conf = row
    return {"resolution_id": rid, "date_ag": str(dag) if dag else None,
            "numero": num, "objet_court": objet, "chunk_ids": list(chunks or []),
            "resultat": res, "confiance": conf}


def build_identite(code):
    cur.execute("SELECT MAX(copropriete) FROM documents WHERE code_ncg = %s", (code,))
    nom = (cur.fetchone() or [None])[0]

    immat, adresse, rue = None, None, None
    try:
        cur.execute("SELECT immatriculation, adresse, rue FROM copros WHERE code_ncg = %s", (code,))
        row = cur.fetchone()
        if row:
            immat, adresse, rue = row
    except Exception:
        conn.rollback()

    # Pointeurs de gouvernance : la DERNIÈRE résolution ADOPTÉE sur le sujet.
    # On ne lit NI le nom du syndic NI la composition du CS (extraction = génération) :
    # on donne au LLM appelant l'endroit exact où les lire, avec la date.
    def dernier_pointeur(regex_py):
        cur.execute("""
            SELECT resolution_id, date_ag, numero, objet_court, chunk_ids, resultat, confiance
            FROM resolutions
            WHERE code_ncg = %s AND resultat = 'adoptee' AND objet_court IS NOT NULL
            ORDER BY date_ag DESC NULLS LAST
        """, (code,))
        for row in cur.fetchall():
            if regex_py.search(row[3] or ""):
                p = _pointeur_resolution(row)
                p["statut"] = "à confirmer sur le PV (pointeur, pas une extraction)"
                return p
        return None

    identite = {
        "code_ncg": code, "nom": nom, "immatriculation": immat,
        "adresse": adresse, "rue": rue,
        "mandat_syndic_pointeur": dernier_pointeur(_SYNDIC_RE),
        "conseil_syndical_pointeur": dernier_pointeur(_CS_RE),
    }
    absents = [k for k in ("immatriculation", "adresse") if not identite.get(k)]
    absents += ["lots", "superficie"]     # non stockés en base pour ce client
    identite["champs_absents"] = absents
    identite["note"] = ("les pointeurs renvoient à la dernière résolution adoptée sur le "
                        "sujet : lire le PV pour le contenu exact, ne rien affirmer d'ici")
    return identite


def build_chiffres(code):
    cur.execute("""
        SELECT COUNT(DISTINCT source_file), MIN(annee), MAX(annee)
        FROM documents WHERE code_ncg = %s
    """, (code,))
    nb_docs, an_min, an_max = cur.fetchone() or (0, None, None)
    cur.execute("SELECT COUNT(*) FROM chunks WHERE code_ncg = %s", (code,))
    nb_chunks = int((cur.fetchone() or [0])[0] or 0)
    cur.execute("""
        SELECT COALESCE(doc_type_corrige, doc_type) AS dt, COUNT(DISTINCT source_file)
        FROM documents WHERE code_ncg = %s GROUP BY dt ORDER BY 2 DESC
    """, (code,))
    doc_types = {r[0]: int(r[1]) for r in cur.fetchall() if r[0]}

    cur.execute(f"""
        SELECT COUNT(*), COUNT(*) FILTER (WHERE {_OUVERT_SQL}),
               COUNT(*) FILTER (WHERE airtable_record_id IS NOT NULL)
        FROM dossiers WHERE code_ncg = %s
    """, (code,))
    d_total, d_ouverts, d_assynco = cur.fetchone() or (0, 0, 0)
    cur.execute("SELECT statut, COUNT(*) FROM dossiers WHERE code_ncg = %s GROUP BY statut", (code,))
    par_statut = {r[0]: int(r[1]) for r in cur.fetchall() if r[0]}
    cur.execute("SELECT type_dossier, COUNT(*) FROM dossiers WHERE code_ncg = %s GROUP BY type_dossier", (code,))
    par_type = {r[0]: int(r[1]) for r in cur.fetchall() if r[0]}

    cur.execute("SELECT resultat, COUNT(*) FROM resolutions WHERE code_ncg = %s GROUP BY resultat", (code,))
    par_resultat = {r[0]: int(r[1]) for r in cur.fetchall()}

    return {
        "nb_documents": int(nb_docs or 0), "nb_chunks": nb_chunks,
        "periode_couverte": [an_min, an_max],
        "doc_types": doc_types,
        "dossiers": {"total": int(d_total or 0), "ouverts": int(d_ouverts or 0),
                     "sourcés_assynco": int(d_assynco or 0),
                     "par_statut": par_statut, "par_type": par_type},
        "resolutions": {"total": sum(par_resultat.values()), "par_resultat": par_resultat},
        "note": "comptes SQL exacts à la date de génération ; ne pas les recalculer à la main",
    }


def build_dossiers_chauds(code):
    cur.execute(f"""
        SELECT dossier_id, nom_dossier, type_dossier, statut, at_situation,
               montant_estime, montant_reel, date_ouverture, airtable_record_id,
               documents_lies, lese_nom
        FROM dossiers
        WHERE code_ncg = %s AND {_OUVERT_SQL}
        ORDER BY montant_estime DESC NULLS LAST, date_ouverture DESC NULLS LAST
        LIMIT %s
    """, (code, MAX_DOSSIERS_CHAUDS))
    rows = cur.fetchall()
    out = []
    today = date.today()
    for (did, nom, typ, statut, at_sit, m_est, m_reel, d_ouv, at_id, docs, lese) in rows:
        motifs = ["dossier non clos"]
        if m_est is not None:
            motifs.append(f"montant estimé {m_est}")
        if d_ouv and _months_between(d_ouv, today) >= SINISTRE_ANCIEN_MOIS:
            motifs.append(f"ouvert depuis {_months_between(d_ouv, today)} mois")
        cur.execute("""
            SELECT chunk_id FROM chunks
            WHERE code_ncg = %s AND dossier_id = %s AND retrieval_exclu = FALSE
            ORDER BY chunk_index LIMIT %s
        """, (code, did, MAX_CHUNKS_ENTREE))
        chunk_ids = [r[0] for r in cur.fetchall()]
        if not chunk_ids and docs:
            # repli : premiers chunks des documents rattachés au dossier (le lien
            # chunk->dossier_id n'est pas systématiquement posé en amont)
            cur.execute("""
                SELECT chunk_id FROM chunks
                WHERE code_ncg = %s AND source_file = ANY(%s) AND retrieval_exclu = FALSE
                ORDER BY source_file, chunk_index LIMIT %s
            """, (code, list(docs)[:5], MAX_CHUNKS_ENTREE))
            chunk_ids = [r[0] for r in cur.fetchall()]
        out.append({
            "dossier_id": did, "titre": nom, "type": typ,
            "statut": statut or at_sit, "lese": lese,
            "montant_estime": float(m_est) if m_est is not None else None,
            "montant_regle": float(m_reel) if m_reel is not None else None,
            "date_ouverture": str(d_ouv) if d_ouv else None,
            "source_assynco": bool(at_id),
            "pointeurs": {"source_files": list(docs or [])[:5], "chunk_ids_entree": chunk_ids},
            "motif_selection": " + ".join(motifs),
        })
    if out:
        out[0].setdefault("_note_section", "")
    return out


def build_questions(code, chiffres):
    """Règles déterministes -> QUESTIONS avec pointeurs. Jamais de conclusion."""
    questions = []
    today = date.today()

    # R1 — exercice comptable sans approbation acquise (LE cas de l'incident)
    cur.execute("""
        SELECT resolution_id, date_ag, numero, objet_court, chunk_ids, resultat, confiance
        FROM resolutions
        WHERE code_ncg = %s AND objet_court IS NOT NULL
        ORDER BY date_ag DESC NULLS LAST
    """, (code,))
    par_exercice = {}
    for row in cur.fetchall():
        objet = row[3] or ""
        if not _COMPTES_RE.search(objet):
            continue
        for annee in _ANNEE_RE.findall(objet):
            par_exercice.setdefault(annee, []).append(row)
    for annee, rows in sorted(par_exercice.items(), reverse=True):
        resultats = {r[5] for r in rows}
        if "adoptee" in resultats:
            continue
        pointeurs = [_pointeur_resolution(r) for r in rows[:4]]
        etats = ", ".join(sorted(resultats))
        questions.append({
            "question": f"Les comptes de l'exercice {annee} ont-ils été approuvés ?",
            "regle": f"aucune résolution 'adoptée' sur ces comptes (états trouvés : {etats})",
            "pointeurs": {"resolutions": pointeurs},
        })

    # R2 — résolutions dont le résultat n'est pas établi de façon fiable
    cur.execute("""
        SELECT resolution_id, date_ag, numero, objet_court, chunk_ids, resultat, confiance
        FROM resolutions
        WHERE code_ncg = %s AND (resultat = 'contradictoire'
              OR (resultat = 'indetermine' AND 'decompte_illisible' = ANY(flags)))
        ORDER BY date_ag DESC NULLS LAST LIMIT 8
    """, (code,))
    douteuses = cur.fetchall()
    if douteuses:
        questions.append({
            "question": f"{len(douteuses)} résolution(s) au sens de vote non établi : que disent les PV ?",
            "regle": "décompte et proclamation discordants, ou décompte illisible (scan/OCR)",
            "pointeurs": {"resolutions": [_pointeur_resolution(r) for r in douteuses]},
        })

    # R3 — dossiers ouverts depuis plus de N mois
    cur.execute(f"""
        SELECT dossier_id, nom_dossier, date_ouverture, montant_estime, documents_lies
        FROM dossiers
        WHERE code_ncg = %s AND {_OUVERT_SQL} AND date_ouverture IS NOT NULL
        ORDER BY date_ouverture ASC LIMIT 6
    """, (code,))
    vieux = [r for r in cur.fetchall()
             if _months_between(r[2], today) >= SINISTRE_ANCIEN_MOIS]
    if vieux:
        questions.append({
            "question": f"{len(vieux)} dossier(s) ouverts depuis plus de {SINISTRE_ANCIEN_MOIS} mois : où en sont-ils ?",
            "regle": f"statut non clos et date d'ouverture > {SINISTRE_ANCIEN_MOIS} mois",
            "pointeurs": {"dossiers": [
                {"dossier_id": d[0], "titre": d[1], "date_ouverture": str(d[2]),
                 "montant_estime": float(d[3]) if d[3] is not None else None,
                 "source_files": list(d[4] or [])[:3]} for d in vieux]},
        })

    # R4 — aucune AG récente (obligation annuelle)
    cur.execute("""
        SELECT MAX(date_ag) FROM resolutions WHERE code_ncg = %s
    """, (code,))
    derniere_ag = (cur.fetchone() or [None])[0]
    if derniere_ag and _months_between(derniere_ag, today) >= AG_MANQUANTE_MOIS:
        questions.append({
            "question": f"Aucune AG documentée depuis {_months_between(derniere_ag, today)} mois "
                        f"(dernière : {derniere_ag}) — le PV le plus récent est-il en base ?",
            "regle": f"dernier PV_AG indexé > {AG_MANQUANTE_MOIS} mois (AG annuelle obligatoire)",
            "pointeurs": {"date_derniere_ag": str(derniere_ag)},
        })

    # R5 — dossiers Assynco sans document RAG rattaché
    cur.execute(f"""
        SELECT dossier_id, nom_dossier FROM dossiers
        WHERE code_ncg = %s AND airtable_record_id IS NOT NULL
              AND (documents_lies IS NULL OR cardinality(documents_lies) = 0)
              AND {_OUVERT_SQL}
        LIMIT 5
    """, (code,))
    orphelins = cur.fetchall()
    if orphelins:
        questions.append({
            "question": f"{len(orphelins)} sinistre(s) suivis chez l'assureur n'ont aucune pièce "
                        f"dans la base documentaire : les pièces sont-elles au dossier ?",
            "regle": "dossier sourcé Assynco, ouvert, sans documents_lies",
            "pointeurs": {"dossiers": [{"dossier_id": d[0], "titre": d[1]} for d in orphelins]},
        })
    return questions


def build_pv_recents(code):
    # Seuls les documents portant AU MOINS UNE résolution à résultat établi sont des
    # PV exploitables : les feuilles de présence, VPC et récapitulatifs sont classés
    # PV_AG en amont mais n'ont aucun vote — ils polluaient l'annuaire.
    cur.execute("""
        SELECT source_file, MAX(date_ag) AS d FROM resolutions
        WHERE code_ncg = %s AND date_ag IS NOT NULL
        GROUP BY source_file
        HAVING COUNT(*) FILTER (WHERE resultat IN ('adoptee','rejetee','retiree')) > 0
        ORDER BY d DESC LIMIT %s
    """, (code, MAX_PV_RECENTS))
    pvs = cur.fetchall()
    out = []
    for source_file, dag in pvs:
        cur.execute("""
            SELECT resolution_id, date_ag, numero, objet_court, chunk_ids, resultat, confiance
            FROM resolutions
            WHERE code_ncg = %s AND source_file = %s AND resultat IN ('adoptee','rejetee','retiree')
            ORDER BY numero NULLS LAST LIMIT %s
        """, (code, source_file, MAX_RES_PAR_PV))
        etablies = [_pointeur_resolution(r) for r in cur.fetchall()]
        cur.execute("""
            SELECT COUNT(*) FROM resolutions
            WHERE code_ncg = %s AND source_file = %s AND resultat NOT IN ('adoptee','rejetee','retiree')
        """, (code, source_file))
        n_non_etablies = int((cur.fetchone() or [0])[0] or 0)
        out.append({"date": str(dag), "source_file": source_file,
                    "resolutions_etablies": etablies,
                    "n_resolutions_sans_resultat_etabli": n_non_etablies})
    return out


def build_fiche(code):
    chiffres = build_chiffres(code)
    fiche = {
        "fiche_version": FICHE_VERSION,
        "usage": ("ANNUAIRE : cette fiche oriente, elle n'établit rien. Suivre les pointeurs "
                  "(chunk_ids, source_files, dossier_id) et lire les sources avant toute "
                  "affirmation ; un sens de vote se vérifie sur le PV."),
        "identite": build_identite(code),
        "chiffres_cles": chiffres,
        "dossiers_chauds": build_dossiers_chauds(code),
        "note_dossiers_chauds": ("montants issus de l'extraction documentaire amont : "
                                 "à vérifier sur pièce avant toute communication"),
        "questions_cles": build_questions(code, chiffres),
        "pv_recents": build_pv_recents(code),
    }
    return fiche


if args.copro:
    codes = [pcfg.resolve(args.copro)]
else:
    cur.execute("SELECT DISTINCT code_ncg FROM chunks WHERE code_ncg IS NOT NULL")
    en_base = {r[0] for r in cur.fetchall()}
    codes = sorted(set(pcfg.COPRO_META) & en_base)
    print(f"📌 --all : {len(codes)} copros")

stats = Counter()
for code in codes:
    fiche = build_fiche(code)
    cur.execute("""
        INSERT INTO copro_synthese (code_ncg, nom, faits_v2, fiche_version, fiche_v2_generated_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (code_ncg) DO UPDATE SET
            nom = COALESCE(EXCLUDED.nom, copro_synthese.nom),
            faits_v2 = EXCLUDED.faits_v2,
            fiche_version = EXCLUDED.fiche_version,
            fiche_v2_generated_at = NOW()
    """, (code, fiche["identite"]["nom"], Json(fiche), FICHE_VERSION))
    conn.commit()
    stats["fiches"] += 1
    stats["questions"] += len(fiche["questions_cles"])
    stats["dossiers_chauds"] += len(fiche["dossiers_chauds"])
    print(f"  {code}: {len(fiche['questions_cles'])} question(s), "
          f"{len(fiche['dossiers_chauds'])} dossier(s) chaud(s), "
          f"{len(fiche['pv_recents'])} PV récents")

print(f"\n✅ {stats['fiches']} fiche(s) v2 (annuaire) — {stats['questions']} questions clés, "
      f"{stats['dossiers_chauds']} dossiers chauds. Coût LLM : 0 $.")
cur.close()
conn.close()
