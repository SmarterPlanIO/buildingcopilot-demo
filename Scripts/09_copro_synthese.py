"""
ÉTAPE 9 — Fiche synthèse pré-calculée par copropriété (narratif Haiku + faits SQL).

Génère/rafraîchit la table `copro_synthese`, lue par le tool MCP PALIM_copro_overview
(lookup direct, pas de génération dans le hot path). À lancer APRÈS 08_airtable_sync.py :
la table `dossiers` doit déjà refléter Airtable (les sinistres synchronisés portent un
airtable_record_id).

Usage :
  DB_PASSWORD=... python 09_copro_synthese.py --copro 5390      # une copro
  DB_PASSWORD=... python 09_copro_synthese.py --all             # toutes (régénère tout)
  DB_PASSWORD=... python 09_copro_synthese.py --all --if-stale  # seulement les périmées

Fraîcheur : le watermark (nb_documents, dernier_pv_date, nb_sinistres_assynco) est
dérivé de la DB. --if-stale compare le watermark stocké au live DB et ne rappelle Haiku
que si ça a bougé. La péremption côté Airtable ENTRE deux runs de 08 est, elle, détectée
en live par le tool MCP (qui compare nb_sinistres_assynco au compte Assynco live), pas ici.

Narratif : couvre les derniers PV d'AG (résumés document-level de l'étape 04) + les
dossiers en cours (sinistres / travaux / contentieux). Les chiffres d'assurance restent
hors narratif : le tool les merge en live depuis Assynco.
"""
import os
import json
import argparse
from collections import Counter

import boto3
import psycopg2
from psycopg2.extras import Json

import bedrock_cost

# =====================================================
# CONFIGURATION
# =====================================================
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = os.environ.get("DB_PASSWORD")
if not DB_PASSWORD:
    raise SystemExit("❌ DB_PASSWORD manquant. Lance : DB_PASSWORD=... python 09_copro_synthese.py --all")

AWS_REGION = "eu-west-1"
HAIKU_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"

PV_FOR_NARRATIVE = 5     # nb de PV_AG récents tracés dans pv_sources / faits (watermark)
PV_TEXT_DOCS = 2         # nb de PV_AG (les plus récents) dont on injecte le TEXTE des résolutions
PV_TEXT_BUDGET = 3500    # budget de chars de texte de résolutions par PV injecté
DOSSIERS_FOR_NARRATIVE = 40  # cap des dossiers détaillés injectés (les comptes couvrent tout)
RESUME_MAX_CHARS = 700   # troncature des résumés de dossiers (resume_ia) dans le prompt

# =====================================================
# Prompt narratif (PV_AG + dossiers)
# =====================================================
NARRATIVE_PROMPT = """Tu es gestionnaire de copropriété. Rédige une synthèse narrative et factuelle de la situation actuelle de la copropriété {nom} (code {code}), à destination d'un gestionnaire qui reprend le dossier.

Couvre, uniquement si l'information est présente dans les éléments fournis :
- Les décisions récentes en assemblée générale (travaux votés, budgets, mandats de syndic, contentieux).
- L'état des dossiers en cours (sinistres, travaux, contentieux) : nature, avancement, points de blocage.
- Les points de vigilance ou échéances à venir.

Règles strictes :
- Aucune invention : si une information n'est pas dans les éléments, ne la mentionne pas.
- Ne cite pas de montant ou de date que tu n'as pas vu dans les éléments.
- Style sobre et professionnel, 180 à 280 mots, paragraphes courts. De courts intertitres en gras sont acceptés ; pas de liste à puces.
- Si les éléments sont trop pauvres pour une synthèse, réponds UNIQUEMENT par : SKIP

=== Derniers PV d'assemblée générale ===
{pv_block}

=== Dossiers en cours ===
{dossiers_block}

Rédige la synthèse :"""


# =====================================================
# Connexion DB
# =====================================================
def get_conn():
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                            user=DB_USER, password=DB_PASSWORD)
    conn.autocommit = True
    return conn


# =====================================================
# Squelette SQL : faits + watermark + matière pour le narratif
# =====================================================
def compute_facts(cur, code):
    """Retourne (facts, watermark, pv_rows, dossier_rows) pour une copro."""
    cur.execute("SELECT MAX(copropriete) FROM documents WHERE code_ncg = %s", (code,))
    nom = (cur.fetchone() or [None])[0]

    cur.execute("SELECT COUNT(DISTINCT source_file) FROM documents WHERE code_ncg = %s", (code,))
    nb_documents = int((cur.fetchone() or [0])[0] or 0)

    cur.execute("SELECT COUNT(*) FROM chunks WHERE code_ncg = %s", (code,))
    nb_chunks = int((cur.fetchone() or [0])[0] or 0)

    cur.execute("SELECT MIN(annee), MAX(annee) FROM documents WHERE code_ncg = %s", (code,))
    annee_min, annee_max = cur.fetchone() or (None, None)

    cur.execute("""
        SELECT COALESCE(doc_type_corrige, doc_type) AS dt, COUNT(DISTINCT source_file)
        FROM documents WHERE code_ncg = %s
        GROUP BY dt ORDER BY 2 DESC
    """, (code,))
    doc_types = {r[0]: int(r[1]) for r in cur.fetchall() if r[0]}

    # PV_AG les plus récents (matière du narratif)
    cur.execute("""
        SELECT date_document, nom_fichier, source_file, resume, premier_texte
        FROM documents
        WHERE code_ncg = %s AND COALESCE(doc_type_corrige, doc_type) = 'PV_AG'
        ORDER BY date_document DESC NULLS LAST, annee DESC NULLS LAST
        LIMIT %s
    """, (code, PV_FOR_NARRATIVE))
    pv_rows = cur.fetchall()

    cur.execute("""
        SELECT MAX(date_document) FROM documents
        WHERE code_ncg = %s AND COALESCE(doc_type_corrige, doc_type) = 'PV_AG'
    """, (code,))
    dernier_pv_date = (cur.fetchone() or [None])[0]

    # Dossiers (sinistres / travaux / contentieux). airtable_record_id => sourcé Assynco.
    cur.execute("""
        SELECT type_dossier, statut, nom_dossier, lese_nom, montant_estime, montant_reel,
               resume_ia, airtable_record_id, date_ouverture, at_situation
        FROM dossiers WHERE code_ncg = %s
        ORDER BY (airtable_record_id IS NOT NULL) DESC, date_ouverture DESC NULLS LAST
    """, (code,))
    dossier_rows = cur.fetchall()

    nb_dossiers = len(dossier_rows)
    nb_sinistres_assynco = sum(1 for r in dossier_rows if r[7])  # airtable_record_id non nul
    par_statut = Counter(r[1] for r in dossier_rows if r[1])
    par_type = Counter(r[0] for r in dossier_rows if r[0])

    facts = {
        "nom": nom,
        "nb_documents": nb_documents,
        "nb_chunks": nb_chunks,
        "annee_min": annee_min,
        "annee_max": annee_max,
        "doc_types": doc_types,
        "pv_ag_recents": [
            {"date": str(r[0]) if r[0] else None, "source_file": r[2], "nom_fichier": r[1]}
            for r in pv_rows
        ],
        "dossiers": {
            "total": nb_dossiers,
            "sinistres_assynco": nb_sinistres_assynco,
            "par_statut": dict(par_statut),
            "par_type": dict(par_type),
        },
    }
    watermark = {
        "nb_documents": nb_documents,
        "nb_chunks": nb_chunks,
        "nb_dossiers": nb_dossiers,
        "nb_sinistres_assynco": nb_sinistres_assynco,
        "dernier_pv_date": dernier_pv_date,
        "pv_sources": [r[2] for r in pv_rows if r[2]],
        "nom": nom,
    }
    return facts, watermark, pv_rows, dossier_rows


# =====================================================
# Narratif Haiku
# =====================================================
def _pv_text_block(cur, pv_rows):
    """Texte des RÉSOLUTIONS des PV récents, depuis les chunks (pas le résumé document-level,
    qui n'est qu'un titre). PV chunkés par résolution => les chunks portent les décisions."""
    lines = []
    for date_doc, nom_fichier, source_file, _resume, _premier in pv_rows[:PV_TEXT_DOCS]:
        cur.execute("""
            SELECT text FROM chunks
            WHERE source_file = %s AND doc_type = 'PV_AG' AND chunk_index > 0
            ORDER BY chunk_index
        """, (source_file,))
        acc, body = 0, []
        for (txt,) in cur.fetchall():
            seg = (txt or "").strip()
            if not seg:
                continue
            body.append(seg)
            acc += len(seg)
            if acc >= PV_TEXT_BUDGET:
                break
        head = f"[{date_doc}] {nom_fichier}" if date_doc else f"[date inconnue] {nom_fichier}"
        text = "\n".join(body)[:PV_TEXT_BUDGET]
        if text:
            lines.append(f"{head}\n{text}")
    return "\n\n".join(lines) if lines else "(aucun PV d'AG disponible)"


def _dossiers_block(dossier_rows):
    lines = []
    for (typ, statut, nom_d, lese, m_est, m_reel, resume_ia,
         _at_id, _date_ouv, at_situation) in dossier_rows[:DOSSIERS_FOR_NARRATIVE]:
        head = f"[{typ or 'DOSSIER'} / {statut or at_situation or 'statut inconnu'}] {nom_d or ''}".strip()
        details = []
        if lese:
            details.append(f"lésé : {lese}")
        if m_est is not None:
            details.append(f"estimé : {m_est}")
        if m_reel is not None:
            details.append(f"réglé : {m_reel}")
        meta = " — " + ", ".join(details) if details else ""
        body = (resume_ia or "").strip()[:RESUME_MAX_CHARS]
        lines.append(f"{head}{meta}" + (f"\n{body}" if body else ""))
    return "\n\n".join(lines) if lines else "(aucun dossier en cours)"


def generate_narrative(bedrock, code, nom, pv_block, dossiers_block):
    """Appel Haiku sur des blocs pré-construits. None si SKIP / réponse trop courte."""
    prompt = NARRATIVE_PROMPT.format(
        nom=nom or f"copro {code}", code=code,
        pv_block=pv_block, dossiers_block=dossiers_block,
    )
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 600,
        "messages": [{"role": "user", "content": prompt}],
    })
    response = bedrock.invoke_model(modelId=HAIKU_MODEL, body=body,
                                    contentType="application/json", accept="application/json")
    result = json.loads(response["body"].read())
    bedrock_cost.track(result)
    answer = result["content"][0]["text"].strip()
    if answer.upper().startswith("SKIP") or len(answer) < 40:
        return None
    return answer


# =====================================================
# Fraîcheur (--if-stale) : watermark stocké vs live DB
# =====================================================
def stored_watermark(cur, code):
    cur.execute("""
        SELECT nb_documents, dernier_pv_date, nb_sinistres_assynco
        FROM copro_synthese WHERE code_ncg = %s
    """, (code,))
    return cur.fetchone()


def is_stale(stored, watermark):
    """True si la fiche doit être régénérée (absente ou watermark divergent)."""
    if stored is None:
        return True
    s_docs, s_pv, s_sin = stored
    return (
        s_docs != watermark["nb_documents"]
        or s_pv != watermark["dernier_pv_date"]
        or s_sin != watermark["nb_sinistres_assynco"]
    )


# =====================================================
# UPSERT
# =====================================================
def upsert(cur, code, facts, watermark, narratif, cost_usd):
    cur.execute("""
        INSERT INTO copro_synthese
            (code_ncg, nom, narratif, faits, nb_documents, nb_chunks, nb_dossiers,
             nb_sinistres_assynco, dernier_pv_date, pv_sources, model_used, cost_usd, generated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
        ON CONFLICT (code_ncg) DO UPDATE SET
            nom = EXCLUDED.nom, narratif = EXCLUDED.narratif, faits = EXCLUDED.faits,
            nb_documents = EXCLUDED.nb_documents, nb_chunks = EXCLUDED.nb_chunks,
            nb_dossiers = EXCLUDED.nb_dossiers, nb_sinistres_assynco = EXCLUDED.nb_sinistres_assynco,
            dernier_pv_date = EXCLUDED.dernier_pv_date, pv_sources = EXCLUDED.pv_sources,
            model_used = EXCLUDED.model_used, cost_usd = EXCLUDED.cost_usd, generated_at = NOW()
    """, (
        code, watermark["nom"], narratif, Json(facts),
        watermark["nb_documents"], watermark["nb_chunks"], watermark["nb_dossiers"],
        watermark["nb_sinistres_assynco"], watermark["dernier_pv_date"],
        watermark["pv_sources"], HAIKU_MODEL, round(cost_usd, 6),
    ))


# =====================================================
# Exécution
# =====================================================
def process_copro(cur, bedrock, code, if_stale):
    facts, watermark, pv_rows, dossier_rows = compute_facts(cur, code)
    if watermark["nb_documents"] == 0 and watermark["nb_dossiers"] == 0:
        print(f"  ⏭️  {code} : aucune donnée en DB, ignoré")
        return "skip"

    if if_stale and not is_stale(stored_watermark(cur, code), watermark):
        print(f"  ✅ {code} ({watermark['nom']}) : à jour, pas de régénération")
        return "fresh"

    pv_block = _pv_text_block(cur, pv_rows)
    dossiers_block = _dossiers_block(dossier_rows)
    has_material = bool(pv_rows) or bool(dossier_rows)
    cost_before = bedrock_cost.cost()
    narratif = (generate_narrative(bedrock, code, watermark["nom"], pv_block, dossiers_block)
                if has_material else None)
    cost_usd = bedrock_cost.cost() - cost_before
    upsert(cur, code, facts, watermark, narratif, cost_usd)
    tag = "narratif" if narratif else "faits seuls (PV/dossiers absents)"
    print(f"  ✍️  {code} ({watermark['nom']}) : {tag} | "
          f"{watermark['nb_documents']} docs, {watermark['nb_dossiers']} dossiers "
          f"({watermark['nb_sinistres_assynco']} Assynco) | ${cost_usd:.4f}")
    return "generated"


def all_codes(cur):
    cur.execute("SELECT DISTINCT code_ncg FROM documents WHERE code_ncg IS NOT NULL ORDER BY code_ncg")
    return [r[0] for r in cur.fetchall()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fiche synthèse pré-calculée par copropriété.")
    parser.add_argument("--copro", help="Code NCG d'une copropriété (ex: 5390).")
    parser.add_argument("--all", action="store_true", help="Toutes les copros présentes en DB.")
    parser.add_argument("--if-stale", action="store_true",
                        help="Ne régénère que les fiches périmées (watermark divergent).")
    args = parser.parse_args()

    if not args.copro and not args.all:
        raise SystemExit("❌ Préciser --copro <code> ou --all.")

    print("=" * 60)
    print("ÉTAPE 9 — FICHES SYNTHÈSE PAR COPROPRIÉTÉ")
    print("=" * 60)

    conn = get_conn()
    cur = conn.cursor()
    bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    codes = [args.copro] if args.copro else all_codes(cur)
    print(f"{len(codes)} copro(s) à traiter"
          + (" (mode --if-stale)" if args.if_stale else "") + "\n")

    stats = Counter()
    for code in codes:
        try:
            stats[process_copro(cur, bedrock, code, args.if_stale)] += 1
        except Exception as e:
            stats["error"] += 1
            print(f"  ❌ {code} : {type(e).__name__} — {e}")

    print("\n" + "=" * 60)
    print("RAPPORT")
    print("=" * 60)
    print(f"  Générées    : {stats['generated']}")
    print(f"  À jour      : {stats['fresh']}")
    print(f"  Ignorées    : {stats['skip']}")
    print(f"  Erreurs     : {stats['error']}")
    print(bedrock_cost.format_line())

    cur.close()
    conn.close()
