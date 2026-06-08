"""
ÉTAPE 6b — Chargement des chunks avec embeddings dans PostgreSQL
Lance : python 06b_load_db.py
"""
import json
import os
import re
import argparse
from datetime import date as dt_date
import psycopg2
from psycopg2.extras import execute_values
from tqdm import tqdm

import pipeline_config as pcfg

# =====================================================
# CONFIGURATION
# =====================================================
# Mode per-copro (--copro) : lit le shard per_copro/<code>/ et fait un UPSERT
# (DELETE WHERE code_ncg + INSERT) au lieu d'un TRUNCATE global -> scale + incremental.
# parse_known_args = import-safe. Sans --copro : mode legacy (monolithe + TRUNCATE).
_parser = argparse.ArgumentParser(description="Chargement DB chunks/documents/dossiers.")
_parser.add_argument("--copro", help="Code NCG (ex: 8050). Absent = legacy global (TRUNCATE).")
_args, _ = _parser.parse_known_args()
COPRO = _args.copro

if COPRO:
    INPUT_FILE = str(pcfg.paths_for(COPRO)["embeddings_sq_jsonl"])
    print(f"📌 Mode per-copro : {COPRO} — upsert (DELETE+INSERT par copro)")
else:
    INPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_avec_embeddings_sq.jsonl"  # Phase 1a : fichier enrichi avec questions synthétiques
    print("📌 Mode legacy : monolithe global (TRUNCATE complet)")
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"  # ← MODIFIER
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = os.environ.get("DB_PASSWORD")  # secret hors du code — exporter avant de lancer
if not DB_PASSWORD:
    raise SystemExit("❌ DB_PASSWORD manquant. Lance : DB_PASSWORD=... python 06b_load_db.py")

BATCH_SIZE = 100  # Insérer par lots de 100

# =====================================================
# Connexion
# =====================================================
conn = psycopg2.connect(
    host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
    user=DB_USER, password=DB_PASSWORD
)
cur = conn.cursor()

# Compter les lignes
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    total = sum(1 for _ in f)

print(f"{total} chunks à charger dans PostgreSQL\n")

# =====================================================
# Chargement par batch
# =====================================================
if COPRO:
    print(f"⏳ Purge des lignes de la copro {COPRO} avant reload (upsert)...")
    cur.execute("DELETE FROM chunks WHERE code_ncg = %s", (COPRO,))
    print(f"✅ chunks {COPRO} purgés ({cur.rowcount})")
    cur.execute("DELETE FROM documents WHERE code_ncg = %s", (COPRO,))
    print(f"✅ documents {COPRO} purgés ({cur.rowcount})")
    conn.commit()
    # NB : les chunks/dossiers virtuels Airtable de cette copro sont aussi supprimés
    # -> relancer 08_airtable_sync pour cette copro APRÈS 06b (gere par le driver).
else:
    print("⏳ Vidage GLOBAL des tables avant rechargement...")
    cur.execute("TRUNCATE TABLE chunks;")
    print("✅ Table chunks vidée")
    cur.execute("TRUNCATE TABLE documents;")
    conn.commit()
    print("✅ Table documents vidée")

batch = []
loaded = 0

def extract_code_ncg(text):
    """Extract NCG code (4-6 digit ID) from file path or Airtable name."""
    if not text:
        return None
    # Pattern 0 (prioritaire) : dossier copro en tête du chemin relatif — "5390 - 2-6 BIS..."
    # Sans ce pattern ancré, un dossier intermédiaire "\2025 - " ou un doc "\13476 - CV"
    # est capté à tort (le code de tête n'a pas de séparateur devant). Inoffensif sur les
    # Names Airtable qui commencent par "DDE-"/"INC-" (lettres, pas de match digit-start).
    m = re.match(r'\s*(\d{4,6})\s*-\s*', text)
    if m:
        return m.group(1)
    # Pattern 1: between parentheses — Airtable format "TIVOLI(5390)"
    m = re.search(r'\((\d{4,6})\)', text)
    if m:
        return m.group(1)
    # Pattern 2: after path separator — "...\5390 - " or ".../5390 - "
    m = re.search(r'(?:[\\\\/])(\d{4,6})\s*-\s*', text)
    if m:
        return m.group(1)
    # Pattern 3: standalone at word boundary — "5390 - 2-6 BIS"
    m = re.search(r'(?:^|[^0-9])(\d{4,6})\s+-\s+', text)
    if m:
        return m.group(1)
    return None


def parse_date(v):
    """Normalise une date pour PostgreSQL. Haiku produit parfois des dates
    partielles (YYYY-MM, YYYY) → on pad au 1er. None si vide ou non parsable."""
    if not isinstance(v, str) or not v.strip():
        return None
    m = re.fullmatch(r'(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?', v.strip())
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2) or "1", m.group(3) or "1"
    return f"{y}-{int(mo):02d}-{int(d):02d}"

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    for line in tqdm(f, total=total, desc="Chargement DB"):
        chunk = json.loads(line)

        # Nettoyer les caractères NUL (0x00) que PostgreSQL refuse
        def clean(s):
            return s.replace("\x00", "") if isinstance(s, str) else s

        # Extraire code_ncg du source_file
        _code_ncg = extract_code_ncg(chunk.get("source_file", ""))

        # Préparer le tuple pour insertion
        row = (
            clean(chunk["chunk_id"]),
            clean(chunk.get("copropriete", "")),
            clean(chunk.get("source_file", "")),
            clean(chunk.get("nom_fichier", "")),
            clean(chunk.get("doc_type", "AUTRE")),
            chunk.get("chunk_index", 0),
            chunk.get("total_chunks", 1),
            chunk.get("themes", []),
            json.dumps(chunk.get("theme_scores", {})),
            clean(chunk["text"]),
            chunk.get("nb_caracteres", len(chunk["text"])),
            str(chunk["embedding"]),  # pgvector accepte le format string
            chunk.get("resolution_category"),             # Phase 1a
            clean(chunk.get("synthetic_questions", "")),  # Phase 1a
            chunk.get("dossier_id"),                      # Module Dossiers
            _code_ncg,                                    # Code NCG universel
        )
        
        batch.append(row)
        
        if len(batch) >= BATCH_SIZE:
            execute_values(cur, """
                INSERT INTO chunks
                (chunk_id, copropriete, source_file, nom_fichier, doc_type,
                 chunk_index, total_chunks, themes, theme_scores,
                 text, nb_caracteres, embedding,
                 resolution_category, synthetic_questions, dossier_id, code_ncg)
                VALUES %s
                ON CONFLICT (chunk_id) DO NOTHING
            """, batch)
            conn.commit()
            loaded += len(batch)
            batch = []

# Dernier batch
if batch:
    execute_values(cur, """
        INSERT INTO chunks
        (chunk_id, copropriete, source_file, nom_fichier, doc_type,
         chunk_index, total_chunks, themes, theme_scores,
         text, nb_caracteres, embedding,
         resolution_category, synthetic_questions, dossier_id, code_ncg)
        VALUES %s
        ON CONFLICT (chunk_id) DO NOTHING
    """, batch)
    conn.commit()
    loaded += len(batch)

# Vérifier
cur.execute("SELECT COUNT(*) FROM chunks;")
count = cur.fetchone()[0]

# Peupler l'index full-text BM25 (français) avec setweight (Phase 1a)
# Weight A = texte original (poids 1.0), Weight D = questions synthétiques (poids 0.1)
# Les questions améliorent le recall (pont de vocabulaire) sans dominer le scoring
print("\n⏳ Génération de l'index full-text BM25 avec setweight (Phase 1a)...")
cur.execute("""
    UPDATE chunks
    SET text_search = setweight(to_tsvector('french', text), 'A')
                   || setweight(to_tsvector('french', COALESCE(synthetic_questions, '')), 'D')
    WHERE text_search IS NULL;
""")
conn.commit()
updated = cur.rowcount
print(f"✅ Index full-text peuplé pour {updated} chunks (setweight A=texte, D=questions)")

# =====================================================
# Chargement de la table documents (métadonnées étape 4)
# =====================================================
METADATA_FILE = os.path.join(os.path.dirname(INPUT_FILE), "documents_metadata.jsonl")

# doc_type_corrige : valeurs valides (liste fermée dans le prompt Haiku)
VALID_DOC_TYPES = {"RCP", "PV_AG", "CONTRAT", "DEVIS", "FACTURE", "BUDGET",
                   "DIAGNOSTIC", "COURRIER", "SINISTRE", "COMPTABILITE",
                   "ENTRETIEN", "ASSURANCE", "AUTRE", "MUTATION", "PLAN",
                   "BORDEREAU_AR"}

if os.path.exists(METADATA_FILE):
    print("\n⏳ Chargement des métadonnées document-level...")

    doc_batch = []
    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)

            # Nettoyage NUL (0x00) que PostgreSQL refuse
            def clean(s):
                return s.replace("\x00", "") if isinstance(s, str) else s

            # Nettoyage date : null string → None, formats partiels → compléter, dates invalides → None
            date_doc = rec.get("date_document")
            if date_doc in (None, "null", ""):
                date_doc = None
            elif isinstance(date_doc, str):
                date_doc = date_doc.strip()
                # Format "YYYY" → "YYYY-01-01"
                if re.match(r'^\d{4}$', date_doc):
                    date_doc = f"{date_doc}-01-01"
                # Format "YYYY-MM" → "YYYY-MM-01"
                elif re.match(r'^\d{4}-\d{2}$', date_doc):
                    date_doc = f"{date_doc}-01"
                # Valider que la date est réelle (pas de jour 59, mois 13, etc.)
                try:
                    parts = date_doc.split("-")
                    dt_date(int(parts[0]), int(parts[1]), int(parts[2]))
                except (ValueError, IndexError):
                    date_doc = None

            # Validation des champs Haiku
            raw_corrige = clean(rec.get("doc_type_corrige") or rec["doc_type"])
            if raw_corrige not in VALID_DOC_TYPES:
                print(f"  ⚠️ doc_type_corrige invalide '{raw_corrige}' → AUTRE ({rec['nom_fichier']})")
                raw_corrige = "AUTRE"

            raw_sous_type = rec.get("sous_type")
            if raw_sous_type and raw_sous_type != "null":
                raw_sous_type = clean(raw_sous_type.strip())
            else:
                raw_sous_type = None

            raw_dossier = rec.get("dossier_lie")
            if raw_dossier and raw_dossier != "null":
                raw_dossier = clean(raw_dossier.strip().upper())
            else:
                raw_dossier = None

            _doc_code_ncg = extract_code_ncg(rec.get("source_file", ""))
            row = (
                clean(rec["source_file"]),
                clean(rec["copropriete"]),
                clean(rec["nom_fichier"]),
                clean(rec["doc_type"]),
                raw_corrige,
                date_doc,
                rec.get("annee"),
                raw_sous_type,
                clean(rec.get("statut")),
                rec.get("montant_principal"),
                raw_dossier,
                clean(rec.get("groupe_doc")),
                rec.get("est_reference", True),
                rec.get("parties_concernees", []),
                clean(rec.get("resume_une_ligne")),
                rec.get("total_chunks"),
                clean((rec.get("premier_texte") or "")[:500]),
                _doc_code_ncg,
            )
            doc_batch.append(row)

    if doc_batch:
        execute_values(cur, """
            INSERT INTO documents
            (source_file, copropriete, nom_fichier, doc_type, doc_type_corrige,
             date_document, annee, sous_type, statut, montant_principal,
             dossier_lie, groupe_doc, est_reference,
             parties_concernees, resume, total_chunks, premier_texte, code_ncg)
            VALUES %s
            ON CONFLICT (source_file) DO UPDATE SET
                doc_type = EXCLUDED.doc_type,
                doc_type_corrige = EXCLUDED.doc_type_corrige,
                date_document = EXCLUDED.date_document,
                annee = EXCLUDED.annee,
                sous_type = EXCLUDED.sous_type,
                statut = EXCLUDED.statut,
                montant_principal = EXCLUDED.montant_principal,
                dossier_lie = EXCLUDED.dossier_lie,
                groupe_doc = EXCLUDED.groupe_doc,
                est_reference = EXCLUDED.est_reference,
                parties_concernees = EXCLUDED.parties_concernees,
                resume = EXCLUDED.resume,
                total_chunks = EXCLUDED.total_chunks,
                premier_texte = EXCLUDED.premier_texte,
                code_ncg = EXCLUDED.code_ncg
        """, doc_batch)
        conn.commit()

    cur.execute("SELECT COUNT(*) FROM documents;")
    doc_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM documents WHERE annee IS NOT NULL;")
    with_date = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM documents WHERE statut IS NOT NULL;")
    with_statut = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM documents WHERE dossier_lie IS NOT NULL;")
    with_dossier = cur.fetchone()[0]

    print(f"✅ {doc_count} documents chargés ({with_date} avec date, {with_statut} avec statut, {with_dossier} avec dossier_lie)")

    # Stats post-normalisation
    cur.execute("SELECT sous_type, COUNT(*) FROM documents WHERE sous_type IS NOT NULL GROUP BY sous_type ORDER BY COUNT(*) DESC LIMIT 20;")
    st_rows = cur.fetchall()
    if st_rows:
        print(f"\n  Top sous_types (normalisés) :")
        for st, cnt in st_rows:
            print(f"    {st:25s} : {cnt}")
else:
    print(f"\n⚠️  {METADATA_FILE} introuvable — table documents non peuplée.")
    print(f"   Lance d'abord : python 04_metadata_documents.py")

# ── Chargement des dossiers (Module Gestion de Projet) ──
DOSSIERS_FILE = os.path.join(os.path.dirname(INPUT_FILE), "dossiers.jsonl")

if os.path.exists(DOSSIERS_FILE):
    print(f"\nChargement des dossiers depuis {DOSSIERS_FILE}...")
    if COPRO:
        # Upsert : retirer d'abord les dossiers RAG de cette copro (PAS les dossiers
        # Airtable, geres par 08) pour eviter les dossiers fantomes apres un
        # re-groupage qui change les dossier_id.
        cur.execute("DELETE FROM dossiers WHERE code_ncg = %s AND airtable_record_id IS NULL", (COPRO,))
        print(f"  ✅ {cur.rowcount} dossiers RAG de {COPRO} purgés avant reload")
        conn.commit()
    dossier_batch = []
    with open(DOSSIERS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            # Try multiple sources for code_ncg: nom_dossier, documents_lies paths, dossier_id
            _dossier_code_ncg = extract_code_ncg(rec.get("nom_dossier", ""))
            if not _dossier_code_ncg:
                for _dl in (rec.get("documents_lies") or []):
                    _dossier_code_ncg = extract_code_ncg(_dl)
                    if _dossier_code_ncg:
                        break
            if not _dossier_code_ncg:
                _dossier_code_ncg = extract_code_ncg(rec.get("dossier_id", ""))
            row = (
                clean(rec["dossier_id"]),
                clean(rec["copropriete"]),
                clean(rec["type_dossier"]),
                clean(rec["nom_dossier"]),
                rec.get("statut", "EN_ATTENTE"),
                parse_date(rec.get("date_ouverture")),
                parse_date(rec.get("date_cloture")),
                clean(rec.get("lese_nom") or ""),
                clean(rec.get("lese_lot") or ""),
                clean(rec.get("responsable_nom") or ""),
                clean(rec.get("responsable_lot") or ""),
                clean(rec.get("expert_nom") or ""),
                clean(rec.get("assureur") or ""),
                clean(rec.get("num_sinistre") or ""),
                clean(rec.get("num_police") or ""),
                json.dumps(rec.get("etapes", []), ensure_ascii=False),
                rec.get("pieces_requises", []),
                rec.get("pieces_fournies", []),
                rec.get("montant_estime"),
                rec.get("montant_reel"),
                rec.get("documents_lies", []),
                clean(rec.get("resume_ia") or ""),
                _dossier_code_ncg,
            )
            dossier_batch.append(row)

    if dossier_batch:
        execute_values(cur, """
            INSERT INTO dossiers
            (dossier_id, copropriete, type_dossier, nom_dossier, statut,
             date_ouverture, date_cloture,
             lese_nom, lese_lot, responsable_nom, responsable_lot,
             expert_nom, assureur, num_sinistre, num_police,
             etapes, pieces_requises, pieces_fournies,
             montant_estime, montant_reel, documents_lies, resume_ia, code_ncg)
            VALUES %s
            ON CONFLICT (dossier_id) DO UPDATE SET
                statut = EXCLUDED.statut,
                num_sinistre = EXCLUDED.num_sinistre,
                num_police = EXCLUDED.num_police,
                etapes = EXCLUDED.etapes,
                pieces_fournies = EXCLUDED.pieces_fournies,
                montant_estime = EXCLUDED.montant_estime,
                montant_reel = EXCLUDED.montant_reel,
                documents_lies = EXCLUDED.documents_lies,
                resume_ia = EXCLUDED.resume_ia,
                code_ncg = EXCLUDED.code_ncg,
                updated_at = NOW()
        """, dossier_batch)
        conn.commit()

    cur.execute("SELECT COUNT(*) FROM dossiers;")
    dossier_count = cur.fetchone()[0]
    cur.execute("SELECT statut, COUNT(*) FROM dossiers GROUP BY statut ORDER BY statut;")
    statut_rows = cur.fetchall()
    statut_summary = ", ".join(f"{s}: {c}" for s, c in statut_rows)
    print(f"✅ {dossier_count} dossiers chargés ({statut_summary})")
else:
    print(f"\n⚠️  {DOSSIERS_FILE} introuvable — table dossiers non peuplée.")
    print(f"   Lance d'abord : python 05c_entity_extraction.py")

cur.close()
conn.close()

print(f"\n✅ {count} chunks chargés dans PostgreSQL")
