"""
ÉTAPE 6a — Initialisation de la base PostgreSQL avec pgvector
Lance : python 06a_init_db.py
"""
import os
import psycopg2

import pipeline_config as pcfg

# =====================================================
# CONFIGURATION — profil client (clients/<client>.json), surchargé par l'env
# =====================================================
DB_HOST = pcfg.require_db_host()
DB_PORT = pcfg.DB_PORT
DB_NAME = pcfg.DB_NAME
DB_USER = pcfg.DB_USER_ADMIN
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

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
        num_sinistre        TEXT,
        num_police          TEXT,
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

# A2 : références extraites du RAG (05c) — idempotent pour table existante
for _col in ("num_sinistre", "num_police"):
    cur.execute(f"ALTER TABLE dossiers ADD COLUMN IF NOT EXISTS {_col} TEXT;")
conn.commit()

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

# ── Profil répétitif (publipostage) — P1 de PLAN_PUBLIPOSTAGE_FACTORISATION.md ──
# Attributs d'OBSERVATION : ils décrivent la redondance interne d'un document
# (texte répété à l'intérieur du même fichier, cas des bundles de publipostage
# "un PV recopié par destinataire"). Aucun effet automatique en V1 : ils servent
# au debug, à l'audit de coût, et alimenteront la factorisation (P2) et le cap
# de diversité au retrieval (P4).
cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunks_bruts INTEGER;")
cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunks_uniques INTEGER;")
cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS redondance_interne NUMERIC;")
cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS profil_repetitif TEXT;")
cur.execute("""
    DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_documents_profil_repetitif') THEN
            ALTER TABLE documents ADD CONSTRAINT chk_documents_profil_repetitif
                CHECK (profil_repetitif IS NULL
                       OR profil_repetitif IN ('PUBLIPOSTAGE', 'REPETITIF_SUSPECT'));
        END IF;
    END $$;
""")
cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_profil_repetitif "
            "ON documents (profil_repetitif) WHERE profil_repetitif IS NOT NULL;")
print("✅ Colonnes profil répétitif (publipostage) sur documents")

# ── Soft delete (version chains / variantes, cf. version_chains.py) ──
# retrieval_exclu=TRUE : chunk exclu du retrieval PAR DEFAUT (patron BORDEREAU_AR),
# toujours en base et accessible par chunk_id (get_chunks / get_full_document).
# Reversible : UPDATE chunks SET retrieval_exclu=FALSE WHERE source_file=...
# nb_occurrences : factorisation publipostage (P2, cf. publipostage.py). Un chunk
# dont le texte se repete N fois dans le MEME document n'est ecrit qu'une fois et
# porte N. Le texte repete n'est pas perdu : il est present, avec son compte.
cur.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS nb_occurrences INTEGER NOT NULL DEFAULT 1;")
cur.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS retrieval_exclu BOOLEAN NOT NULL DEFAULT FALSE;")
cur.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS motif_exclusion TEXT;")
cur.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS ref_source_file TEXT;")
print("✅ Colonnes soft delete (retrieval_exclu/motif_exclusion/ref_source_file) sur chunks")
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

# ── Table copro_synthese : fiche pré-calculée par copro (narratif Haiku + faits SQL) ──
# Générée par 09_copro_synthese.py après 08_airtable_sync.py. Lue par le tool MCP
# PALIM_copro_overview (lookup direct). nb_documents/dernier_pv_date = watermark de
# fraîcheur : le tool compare au compte live pour flaguer une fiche périmée (stale).
cur.execute("""
    CREATE TABLE IF NOT EXISTS copro_synthese (
        code_ncg            TEXT PRIMARY KEY,
        nom                 TEXT,
        narratif            TEXT,            -- synthèse Haiku (derniers PV_AG + dossiers)
        faits               JSONB DEFAULT '{}'::jsonb,  -- agrégats SQL (inventaire, dossiers)
        nb_documents        INTEGER,         -- watermark RAG : compte docs à la génération
        nb_chunks           INTEGER,
        nb_dossiers         INTEGER,         -- total dossiers en DB (RAG + Airtable)
        nb_sinistres_assynco INTEGER,        -- watermark Airtable : dossiers airtable-sourcés (post-08)
        dernier_pv_date     DATE,            -- date du PV_AG le plus récent couvert
        pv_sources          TEXT[] DEFAULT '{}',  -- source_file des PV_AG utilisés
        model_used          TEXT,
        cost_usd            NUMERIC,
        generated_at        TIMESTAMP DEFAULT NOW()
    );
""")
conn.commit()
print("✅ Table copro_synthese créée (ou déjà existante)")

# ── Table copros : registre annuaire (identité, pas retrieval) ──
# Lue par PALIM_list_copros (adresse/aliases optionnels) et PALIM_copro_overview.
# immatriculation = attribut RNIC (AA0000000), jamais une clé interne pour les
# clients à codes courts (cf. copro_id.py / PLAN_IMMATRICULATION_RNIC.md).
# Peuplée par 06b_load_db.py depuis le profil client (upsert, jamais de TRUNCATE).
cur.execute("""
    CREATE TABLE IF NOT EXISTS copros (
        code_ncg        TEXT PRIMARY KEY,
        nom_residence   TEXT,
        adresse         TEXT,
        rue             TEXT,
        aliases         TEXT[] DEFAULT '{}',
        immatriculation TEXT
    );
""")
cur.execute("ALTER TABLE copros ADD COLUMN IF NOT EXISTS immatriculation TEXT;")
conn.commit()
print("✅ Table copros créée (registre annuaire + immatriculation RNIC)")

# ── Table ingestion_registre : memoire d'etat du pipeline, unite = document ──
# Ecrite par 01/00b/02/03/06b via registre.py (cf. PLAN_REGISTRE_INGESTION.md).
# JAMAIS lue par le MCP : outil d'exploitation, aucun GRANT pour mcp_*_reader.
# source_file = chemin relatif prefixe du dossier copro, identique a chunks.source_file.
cur.execute("""
    CREATE TABLE IF NOT EXISTS ingestion_registre (
        source_file     TEXT PRIMARY KEY,
        code_ncg        TEXT NOT NULL,
        nom_fichier     TEXT,
        taille_octets   BIGINT,
        sha256          TEXT,
        signature       TEXT,

        statut          TEXT NOT NULL DEFAULT 'DECOUVERT'
            CHECK (statut IN ('DECOUVERT','EXTRAIT','INGERE','REJETE','SUPPRIME','ERREUR')),
        motif           TEXT
            CHECK (motif IS NULL OR motif IN (
                'FILTRAGE_PHOTO','FILTRAGE_PLAN','FILTRAGE_SYSTEME','FILTRAGE_AUTRE',
                'FILTRAGE_GOOGLE_NATIF','DOUBLON_EXACT','TEXTE_VIDE','NON_EXPLOITABLE',
                'DOUBLON_PROCHE','EXTRACTION_KO','CHARGEMENT_KO','COPIE_KO')),
        etape           TEXT,
        ref_source_file TEXT,
        score           NUMERIC,

        doc_type        TEXT,
        nb_caracteres   INTEGER,
        nb_chunks       INTEGER,

        run_id          TEXT,
        first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_ingest     TIMESTAMPTZ,
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );
""")
# QUARANTAINE + VOLUME_EXCESSIF (mécanisme C de PLAN_PUBLIPOSTAGE_FACTORISATION.md).
# QUARANTAINE n'est PAS un rejet : le document ne doit pas passer dans le pipeline
# standard (volume hors limites Textract, structure exigeant un traitement spécial)
# et attend une décision, sans être ni ingéré ni perdu ni retenté automatiquement.
# Contraintes recréées à l'identique sur les installations existantes (idempotent).
cur.execute("ALTER TABLE ingestion_registre DROP CONSTRAINT IF EXISTS ingestion_registre_statut_check;")
cur.execute("""
    ALTER TABLE ingestion_registre ADD CONSTRAINT ingestion_registre_statut_check
    CHECK (statut IN ('DECOUVERT','EXTRAIT','INGERE','REJETE','SUPPRIME','ERREUR','QUARANTAINE'));
""")
cur.execute("ALTER TABLE ingestion_registre DROP CONSTRAINT IF EXISTS ingestion_registre_motif_check;")
cur.execute("""
    ALTER TABLE ingestion_registre ADD CONSTRAINT ingestion_registre_motif_check
    CHECK (motif IS NULL OR motif IN (
        'FILTRAGE_PHOTO','FILTRAGE_PLAN','FILTRAGE_SYSTEME','FILTRAGE_AUTRE',
        'FILTRAGE_GOOGLE_NATIF','DOUBLON_EXACT','TEXTE_VIDE','NON_EXPLOITABLE',
        'DOUBLON_PROCHE','EXTRACTION_KO','CHARGEMENT_KO','COPIE_KO','VOLUME_EXCESSIF'));
""")
print("OK Statut QUARANTAINE + motif VOLUME_EXCESSIF autorises au registre")

cur.execute("CREATE INDEX IF NOT EXISTS idx_registre_copro ON ingestion_registre (code_ncg, statut);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_registre_motif ON ingestion_registre (statut, motif);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_registre_sha   ON ingestion_registre (sha256);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_registre_run   ON ingestion_registre (run_id);")
print("OK Table ingestion_registre creee (ou deja existante)")

# ── Table ingestion_runs : un batch = une ligne, pour le rapport de fin de cycle ──
cur.execute("""
    CREATE TABLE IF NOT EXISTS ingestion_runs (
        run_id      TEXT PRIMARY KEY,
        code_ncg    TEXT NOT NULL,
        started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at TIMESTAMPTZ,
        ok          BOOLEAN,
        stats       JSONB
    );
""")
print("OK Table ingestion_runs creee (ou deja existante)")

# ── Table resolutions : le nœud décisionnel du graphe (C2, PLAN_FIABILITE_SYNTHESE) ──
# Une ligne par RÉSOLUTION reconstituée (fragments « [Suite résolution …] » regroupés),
# résultat CALCULÉ (décompte) ou LU (proclamation) par resolution_index.py — jamais
# généré par LLM. Peuplée par 09b_resolutions.py (DELETE WHERE code_ncg + INSERT).
cur.execute("""
    CREATE TABLE IF NOT EXISTS resolutions (
        resolution_id       TEXT PRIMARY KEY,
        code_ncg            TEXT NOT NULL,
        source_file         TEXT NOT NULL,
        date_ag             DATE,
        numero              TEXT,
        objet_court         TEXT,
        chunk_ids           TEXT[] NOT NULL DEFAULT '{}',
        decompte_pour       NUMERIC,
        decompte_contre     NUMERIC,
        decompte_abstention NUMERIC,
        article_majorite    TEXT,
        resultat            TEXT NOT NULL,
        source_resultat     TEXT,
        confiance           TEXT,
        flags               TEXT[] NOT NULL DEFAULT '{}',
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    );
""")
cur.execute("CREATE INDEX IF NOT EXISTS idx_resolutions_copro ON resolutions (code_ncg);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_resolutions_source ON resolutions (source_file);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_resolutions_resultat ON resolutions (code_ncg, resultat);")
conn.commit()
print("OK Table resolutions creee (noeud decisionnel, C2)")

cur.close()
conn.close()
print("\n✅ Base de données initialisée avec succès")
