"""
ÉTAPE 6b — Chargement des chunks avec embeddings dans PostgreSQL
Lance : python 06b_load_db.py
"""
import json
import os
import re
from datetime import date as dt_date
import psycopg2
from psycopg2.extras import execute_values
from tqdm import tqdm

# =====================================================
# CONFIGURATION
# =====================================================
INPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_avec_embeddings.jsonl"  # ← MODIFIER
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"  # ← MODIFIER
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = "SmarterRAG99!"  # ← MODIFIER

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
print("⏳ Vidage des tables avant rechargement...")
cur.execute("TRUNCATE TABLE chunks;")
print("✅ Table chunks vidée")
cur.execute("TRUNCATE TABLE documents;")
conn.commit()
print("✅ Table documents vidée")

batch = []
loaded = 0

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    for line in tqdm(f, total=total, desc="Chargement DB"):
        chunk = json.loads(line)
        
        # Nettoyer les caractères NUL (0x00) que PostgreSQL refuse
        def clean(s):
            return s.replace("\x00", "") if isinstance(s, str) else s
        
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
            str(chunk["embedding"])  # pgvector accepte le format string
        )
        
        batch.append(row)
        
        if len(batch) >= BATCH_SIZE:
            execute_values(cur, """
                INSERT INTO chunks 
                (chunk_id, copropriete, source_file, nom_fichier, doc_type,
                 chunk_index, total_chunks, themes, theme_scores,
                 text, nb_caracteres, embedding)
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
         text, nb_caracteres, embedding)
        VALUES %s
        ON CONFLICT (chunk_id) DO NOTHING
    """, batch)
    conn.commit()
    loaded += len(batch)

# Vérifier
cur.execute("SELECT COUNT(*) FROM chunks;")
count = cur.fetchone()[0]

# Peupler l'index full-text BM25 (français) pour les chunks qui ne l'ont pas
print("\n⏳ Génération de l'index full-text BM25 (français)...")
cur.execute("""
    UPDATE chunks 
    SET text_search = to_tsvector('french', text)
    WHERE text_search IS NULL;
""")
conn.commit()
updated = cur.rowcount
print(f"✅ Index full-text peuplé pour {updated} chunks")

# =====================================================
# Chargement de la table documents (métadonnées étape 4)
# =====================================================
METADATA_FILE = os.path.join(os.path.dirname(INPUT_FILE), "documents_metadata.jsonl")

# doc_type_corrige : valeurs valides (liste fermée dans le prompt Haiku)
VALID_DOC_TYPES = {"RCP", "PV_AG", "CONTRAT", "DEVIS", "FACTURE", "BUDGET",
                   "DIAGNOSTIC", "COURRIER", "SINISTRE", "COMPTABILITE",
                   "ENTRETIEN", "ASSURANCE", "AUTRE", "MUTATION", "PLAN"}

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
                clean((rec.get("premier_texte") or "")[:500])
            )
            doc_batch.append(row)

    if doc_batch:
        execute_values(cur, """
            INSERT INTO documents
            (source_file, copropriete, nom_fichier, doc_type, doc_type_corrige,
             date_document, annee, sous_type, statut, montant_principal,
             dossier_lie, groupe_doc, est_reference,
             parties_concernees, resume, total_chunks, premier_texte)
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
                premier_texte = EXCLUDED.premier_texte
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

cur.close()
conn.close()

print(f"\n✅ {count} chunks chargés dans PostgreSQL")
