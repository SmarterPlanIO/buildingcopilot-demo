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

# Colonnes Phase 1a : resolution_category + synthetic_questions
cur.execute("""
    ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS resolution_category TEXT;
""")
cur.execute("""
    ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS synthetic_questions TEXT;
""")
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_chunks_rescategory
    ON chunks (resolution_category);
""")
print("✅ Colonnes resolution_category + synthetic_questions ajoutées (ou déjà présentes)")

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

# ── Table dossiers (gestion de projet — sinistres, travaux, contentieux) ──
cur.execute("""
    CREATE TABLE IF NOT EXISTS dossiers (
        dossier_id          TEXT PRIMARY KEY,
        copropriete         TEXT NOT NULL,
        type_dossier        TEXT NOT NULL,
        nom_dossier         TEXT NOT NULL,
        statut              TEXT DEFAULT 'EN_ATTENTE',
        date_ouverture      DATE,
        date_cloture        DATE,
        lese_nom            TEXT,
        lese_lot            TEXT,
        responsable_nom     TEXT,
        responsable_lot     TEXT,
        expert_nom          TEXT,
        assureur            TEXT,
        etapes              JSONB DEFAULT '[]'::jsonb,
        pieces_requises     TEXT[] DEFAULT '{}',
        pieces_fournies     TEXT[] DEFAULT '{}',
        montant_estime      NUMERIC,
        montant_reel        NUMERIC,
        documents_lies      TEXT[] DEFAULT '{}',
        resume_ia           TEXT,
        created_at          TIMESTAMP DEFAULT NOW(),
        updated_at          TIMESTAMP DEFAULT NOW()
    );
""")
conn.commit()
print("✅ Table dossiers créée (ou déjà existante)")

cur.execute("CREATE INDEX IF NOT EXISTS idx_dossiers_copro ON dossiers (copropriete);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_dossiers_type ON dossiers (type_dossier);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_dossiers_statut ON dossiers (statut);")
conn.commit()
print("✅ Index sur dossiers créés (copro, type_dossier, statut)")

# ── Migration dossiers : colonnes Airtable Assynco ──
_airtable_columns = [
    # Identifiant Airtable pour synchro
    ("airtable_record_id", "TEXT UNIQUE"),
    # Pipeline 🚦 (4 étapes + mise en cause)
    ("at_declaration", "TEXT"),
    ("at_expertise", "TEXT"),
    ("at_accord", "TEXT"),
    ("at_reglement", "TEXT"),
    ("at_mise_en_cause", "TEXT"),
    # Statut enrichi
    ("at_situation", "TEXT"),
    ("at_attente", "TEXT"),
    # Cause et IRSI
    ("cause", "TEXT"),
    ("irsi", "BOOLEAN"),
    ("cause_identifiee", "BOOLEAN"),
    ("cause_reparee", "BOOLEAN"),
    # Garantie
    ("garantie_impactee", "TEXT[]"),
    # Financier
    ("franchise", "NUMERIC"),
    ("provisions", "NUMERIC"),
    ("reglement_realise", "NUMERIC"),
    ("reglement_frais", "NUMERIC"),
    ("recours_en_cours", "NUMERIC"),
    ("recours_realise", "NUMERIC"),
    ("cout_client", "NUMERIC"),
    ("honoraire_syndic", "NUMERIC"),
    ("dommages", "NUMERIC"),
    ("indemnite_immediate", "NUMERIC"),
    ("indemnite_differee", "NUMERIC"),
    ("total_regle", "NUMERIC"),
    # Dates clés
    ("date_declaration", "DATE"),
    ("date_mission_expert", "DATE"),
    ("date_invitation_expertise", "TIMESTAMP"),
    ("date_premiere_visite", "DATE"),
    ("date_pv", "DATE"),
    ("date_lettre_acceptation", "DATE"),
    ("date_depot_rapport", "DATE"),
    ("date_reglement", "DATE"),
    ("date_derniere_relance", "DATE"),
    ("date_relance_expert", "DATE"),
    ("date_relance_compagnie", "DATE"),
    ("date_relance_client", "DATE"),
    ("date_rappel", "DATE"),
    ("date_prescription", "DATE"),
    # Contacts lésé
    ("lese_tel", "TEXT"),
    ("lese_email", "TEXT"),
    ("appt_origine", "TEXT"),
    # Références croisées
    ("ref_cie", "TEXT"),
    ("ref_expert", "TEXT"),
    ("ref_sinistre_client", "TEXT"),
    ("ref_assynco", "TEXT"),
    # Textes riches
    ("circonstances", "TEXT"),
    ("dommages_description", "TEXT"),
    ("conclusion_expert", "TEXT"),
    ("commentaire_assureur", "TEXT"),
    ("commentaire_assynco", "TEXT"),
    ("observations_declaration", "TEXT"),
    ("motif_rappel", "TEXT"),
    ("commentaire_relance_expert", "TEXT"),
    ("commentaire_relance_compagnie", "TEXT"),
    ("commentaire_relance_client", "TEXT"),
    # Flags
    ("important", "BOOLEAN DEFAULT FALSE"),
    ("judiciaire", "BOOLEAN DEFAULT FALSE"),
    ("en_carence", "BOOLEAN DEFAULT FALSE"),
    # Éléments manquants (valeurs exactes Airtable)
    ("elements_manquants", "TEXT[]"),
    # Situation sinistré
    ("situation_sinistre", "TEXT"),
    ("dommage_copro", "BOOLEAN"),
]
_added = 0
for col_name, col_type in _airtable_columns:
    try:
        cur.execute(f"ALTER TABLE dossiers ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
        _added += 1
    except Exception as e:
        print(f"  ⚠️ Colonne {col_name}: {e}")
        conn.rollback()
conn.commit()
cur.execute("CREATE INDEX IF NOT EXISTS idx_dossiers_airtable ON dossiers (airtable_record_id);")
conn.commit()
print(f"✅ Migration Airtable : {_added} colonnes ajoutées à la table dossiers")

# ── Colonne dossier_id sur chunks (lien chunk → dossier) ──
cur.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS dossier_id TEXT;")
conn.commit()
cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_dossierid ON chunks (dossier_id);")
conn.commit()
print("✅ Colonne dossier_id ajoutée à chunks (ou déjà présente)")

# ── code_ncg : identifiant NCG universel (ex: "5390") ──
# Ajout aux 3 tables : chunks, documents, dossiers
for _tbl in ("chunks", "documents", "dossiers"):
    cur.execute(f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS code_ncg TEXT;")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{_tbl}_code_ncg ON {_tbl} (code_ncg);")
conn.commit()
print("✅ Colonne code_ncg ajoutée aux tables chunks, documents, dossiers (+ index)")

# Migration one-shot : extraire code_ncg des données existantes
# Chunks/documents : extraire de source_file (pattern "5390 - 2-6 BIS HENRI TARIEL")
cur.execute("""
    UPDATE chunks SET code_ncg = substring(source_file from E'[\\\\\\\\/](\\d{4,6})\\s*-\\s*')
    WHERE code_ncg IS NULL AND source_file IS NOT NULL;
""")
_n1 = cur.rowcount
cur.execute("""
    UPDATE documents SET code_ncg = substring(source_file from E'[\\\\\\\\/](\\d{4,6})\\s*-\\s*')
    WHERE code_ncg IS NULL AND source_file IS NOT NULL;
""")
_n2 = cur.rowcount
# Dossiers Airtable : extraire de nom_dossier (pattern "LES TERRASSES DE TIVOLI(5390)")
cur.execute(r"""
    UPDATE dossiers SET code_ncg = substring(nom_dossier from '\((\d{4,6})\)')
    WHERE code_ncg IS NULL AND nom_dossier ~ '\(\d{4,6}\)';
""")
_n3 = cur.rowcount
# Dossiers sans parenthèses : essayer depuis le source_file des chunks liés
cur.execute("""
    UPDATE dossiers d SET code_ncg = (
        SELECT DISTINCT c.code_ncg FROM chunks c
        WHERE c.dossier_id = d.dossier_id AND c.code_ncg IS NOT NULL
        LIMIT 1
    )
    WHERE d.code_ncg IS NULL;
""")
_n4 = cur.rowcount
conn.commit()
print(f"✅ Migration code_ncg : {_n1} chunks, {_n2} documents, {_n3}+{_n4} dossiers mis à jour")

# ── Table chat_sessions : persistance des conversations pour résilience mobile ──
cur.execute("""
    CREATE TABLE IF NOT EXISTS chat_sessions (
        session_id TEXT PRIMARY KEY,
        code_ncg TEXT,
        chat_history JSONB DEFAULT '[]',
        selected_dossier TEXT,
        pending_query TEXT,
        updated_at TIMESTAMP DEFAULT NOW()
    );
""")
cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions (updated_at);")
conn.commit()
print("✅ Table chat_sessions créée (persistance conversations mobile)")

cur.close()
conn.close()
print("\n✅ Base de données initialisée avec succès")
