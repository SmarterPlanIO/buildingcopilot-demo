"""
PALIM_config.py — Constantes du serveur MCP PALIM.

Valeurs reprises de Scripts/Streamlit Cloud/streamlit_app.py pour garder
le retrieval cohérent avec l'app (porte de régression, cf. PLAN_ACTION §6).
Aucune dépendance Streamlit : tout est lu depuis l'environnement.
"""
import os

# ── Embeddings (Bedrock Titan V2) ──
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
EMBED_DIM = 1024
EMBED_MAX_CHARS = 5000  # troncature avant embedding (cf. get_embedding streamlit_app.py:695)

# ── Régions AWS ──
AWS_REGION_EMBED = os.environ.get("AWS_REGION_EMBED", "eu-west-1")
AWS_REGION_RERANK = os.environ.get("AWS_REGION_RERANK", "eu-central-1")  # cohere, Phase 6

# ── Retrieval hybride (RRF + diversité) ──
RRF_K = 60
SIMILARITY_THRESHOLD = 0.15
MAX_CHUNKS_PER_SOURCE = 3
RERANK_CANDIDATES = 200       # pool large (compense absence de FlashRank en cloud)
RCP_MIN_SLOTS = 3             # quota minimum RCP quand include_legal_context=True
MIN_CHUNK_CHARS = 500         # ignore signatures / fragments OCR

# ── Rerank Cohere (eu-central-1, cf. rerank.py) ──
# ON par défaut en V1 pour aligner la qualité MCP sur l'app Streamlit. Mettre
# ENABLE_RERANK=0 pour désactiver (ex. si IAM eu-central-1 absent). Le rerank ne
# s'applique que hors pré-filtrage actif, et retombe fail-open sur l'ordre RRF.
ENABLE_RERANK = os.environ.get("ENABLE_RERANK", "1").strip().lower() not in ("0", "false", "no", "")

# Catégories de résolution filtrées en mode inventaire (cf. filter_resolution_categories)
INVENTAIRE_EXCLUDE_CATEGORIES = ("PROCEDURE_AG", "ELECTION_CS")

# Profils de recherche : (max_chunks par défaut, chunks_per_source, sim_threshold)
RETRIEVAL_MODES = {
    "cible":      {"chunks_per_source": 2, "sim_threshold": 0.20},
    "equilibre":  {"chunks_per_source": MAX_CHUNKS_PER_SOURCE, "sim_threshold": SIMILARITY_THRESHOLD},
    "inventaire": {"chunks_per_source": 6, "sim_threshold": 0.10},
}

# ── Caps serveur (sécurité pilote, PLAN_ACTION §11) ──
MAX_CHUNKS_CAP = 30
MAX_RESULTS_CAP = 50
MAX_CHARS_CAP = 50000
GET_FULL_DOC_DEFAULT_CHARS = 20000

# ── Découverte documentaire ──
DISCOVERY_TOP_K = 10
DISCOVERY_SNIPPET_CHARS = 220

# ── DB (lue par PALIM_db.py) ──
# Le mot de passe NE doit PAS être en env en prod : fournir DB_SECRET_ARN
# (AWS Secrets Manager). DB_PASSWORD en env reste un fallback de dev uniquement.
DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "mcp_ncg_reader")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")  # fallback dev seulement
DB_SECRET_ARN = os.environ.get("DB_SECRET_ARN", "")  # ARN ou nom du secret Secrets Manager
AWS_REGION_SECRETS = os.environ.get("AWS_REGION_SECRETS", AWS_REGION_EMBED)

# ── Assynco ERP (Airtable — lecture R1 : Copropriétés + Police + Sinistre) ──
# cf. PLAN_ACTION_MCP_ASSYNCO.md et skills/assynco-erp/references/data-model.md
ENABLE_ASSYNCO = os.environ.get("ENABLE_ASSYNCO", "1").strip().lower() not in ("0", "false", "no", "")
ASSYNCO_BASE_ID = os.environ.get("ASSYNCO_BASE_ID", "appi1ee5p93EBHtLR")
ASSYNCO_TABLE_COPRO = os.environ.get("ASSYNCO_TABLE_COPRO", "tblsPUcmAXwWcZFjj")
ASSYNCO_TABLE_POLICE = os.environ.get("ASSYNCO_TABLE_POLICE", "tblNHIMVgw0Xv36u0")
ASSYNCO_TABLE_SINISTRE = os.environ.get("ASSYNCO_TABLE_SINISTRE", "tblvvkhcHZjDyHLdp")
ASSYNCO_TABLE_ORG = os.environ.get("ASSYNCO_TABLE_ORG", "tblKwYRub475OfjMI")       # Organisation
ASSYNCO_TABLE_CONTACT = os.environ.get("ASSYNCO_TABLE_CONTACT", "tblz0qBxVKkSfRzqP")  # Contacts
ASSYNCO_TABLE_PRODUIT = os.environ.get("ASSYNCO_TABLE_PRODUIT", "tbllnrD1aydOAaRqy")  # Produit (type de contrat)
# PAT : Secrets Manager (ARN) en prod ; AIRTABLE_PAT en env = fallback dev seulement.
AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT", "")
AIRTABLE_PAT_SECRET_ARN = os.environ.get("AIRTABLE_PAT_SECRET_ARN", "")
ASSYNCO_MAX_RECORDS_CAP = int(os.environ.get("ASSYNCO_MAX_RECORDS_CAP", "50"))
ASSYNCO_HTTP_TIMEOUT = int(os.environ.get("ASSYNCO_HTTP_TIMEOUT", "15"))

# ── MCP ──
MCP_URL_SLUG = os.environ.get("MCP_URL_SLUG", "mcp")  # slug secret en prod
IVFFLAT_PROBES = int(os.environ.get("IVFFLAT_PROBES", "10"))

# ── Observabilité (Langfuse, optionnel — no-op si clés absentes) ──
# Pin langfuse==2.60.4 (v3 casse l'API .trace()/.span(), cf. CLAUDE.md).
# Pilote : clés en env Lambda. La clé secrète pourra migrer vers Secrets Manager.
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
LANGFUSE_USER = os.environ.get("LANGFUSE_USER", "")  # identifiant pilote optionnel
