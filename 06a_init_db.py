"""
ÉTAPE 6a — Initialisation de la base PostgreSQL avec pgvector
Lance : python 06a_init_db.py
"""
import psycopg2

# =====================================================
# CONFIGURATION — Remplace par tes valeurs
# =====================================================
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"  # ← MODIFIER
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = "SmarterRAG99!"  # ← MODIFIER

# =====================================================
# Connexion et initialisation
# =====================================================
conn = psycopg2.connect(
    host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
    user=DB_USER, password=DB_PASSWORD
)
conn.autocommit = True
cur = conn.cursor()

# Activer pgvector
cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
print("✅ Extension pgvector activée")

# Créer la table principale
cur.execute("""
    CREATE TABLE IF NOT EXISTS chunks (
        chunk_id        TEXT PRIMARY KEY,
        copropriete     TEXT NOT NULL,
        source_file     TEXT NOT NULL,
        nom_fichier     TEXT NOT NULL,
        doc_type        TEXT NOT NULL,
        chunk_index     INTEGER,
        total_chunks    INTEGER,
        themes          TEXT[],          -- Array de thèmes pour filtrage
        theme_scores    JSONB,
        text            TEXT NOT NULL,
        nb_caracteres   INTEGER,
        embedding       vector(1024),    -- Dimension Titan V2
        text_search     tsvector         -- Full-text search BM25 (français)
    );
""")
print("✅ Table 'chunks' créée")

# Index vectoriel pour recherche par similarité
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_chunks_embedding 
    ON chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
""")
print("✅ Index vectoriel IVFFlat créé")

# Index GIN sur les thèmes pour filtrage rapide
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_chunks_themes 
    ON chunks USING gin (themes);
""")
print("✅ Index GIN sur themes créé")

# Index sur la copropriété
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_chunks_copro 
    ON chunks (copropriete);
""")
print("✅ Index sur copropriete créé")

# Index sur le type de document
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_chunks_doctype 
    ON chunks (doc_type);
""")
print("✅ Index sur doc_type créé")

# Ajouter la colonne text_search si elle n'existe pas (table existante)
cur.execute("""
    ALTER TABLE chunks 
    ADD COLUMN IF NOT EXISTS text_search tsvector;
""")
print("✅ Colonne text_search ajoutée (ou déjà présente)")

# Index GIN pour recherche full-text BM25 (français)
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_chunks_textsearch 
    ON chunks USING gin (text_search);
""")
print("✅ Index GIN full-text (BM25) créé")

# =====================================================
# Table documents — métadonnées document-level (étape 4b)
# =====================================================
cur.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        source_file         TEXT PRIMARY KEY,
        copropriete         TEXT NOT NULL,
        nom_fichier         TEXT NOT NULL,
        doc_type            TEXT NOT NULL,
        doc_type_corrige    TEXT,
        date_document       DATE,
        annee               INTEGER,
        sous_type           TEXT,
        statut              TEXT,
        montant_principal   NUMERIC,
        dossier_lie         TEXT,
        groupe_doc          TEXT,
        est_reference       BOOLEAN DEFAULT TRUE,
        parties_concernees  TEXT[],
        resume              TEXT,
        total_chunks        INTEGER,
        premier_texte       TEXT
    );
""")
print("✅ Table 'documents' créée")

# Ajouter doc_type_corrige si la table existe déjà sans cette colonne
cur.execute("""
    ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS doc_type_corrige TEXT;
""")
print("✅ Colonne doc_type_corrige ajoutée (ou déjà présente)")

cur.execute("""
    ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS dossier_lie TEXT;
""")
print("✅ Colonne dossier_lie ajoutée (ou déjà présente)")

cur.execute("""
    ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS groupe_doc TEXT;
""")
cur.execute("""
    ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS est_reference BOOLEAN DEFAULT TRUE;
""")
print("✅ Colonnes groupe_doc + est_reference ajoutées (ou déjà présentes)")

cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_copro ON documents (copropriete);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_doctype ON documents (doc_type);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_doctype_corr ON documents (doc_type_corrige);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_annee ON documents (annee);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_statut ON documents (statut);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_soustype ON documents (sous_type);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_dossierlie ON documents (dossier_lie);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_groupedoc ON documents (groupe_doc);")
print("✅ Index sur documents créés (copro, doc_type, doc_type_corrige, annee, statut, sous_type, dossier_lie, groupe_doc)")

cur.close()
conn.close()
print("\n✅ Base de données initialisée avec succès")
