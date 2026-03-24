"""
ÉTAPE 7 — Interface de requête RAG (Streamlit Cloud) — v4 Haiku Strategy Router
Pipeline : Haiku strategy detection → Pré-filtrage document → Vector + BM25 → RRF fusion → Source diversity → Claude
Lance : streamlit run streamlit_app.py
Note : pas de FlashRank en cloud (compensé par RERANK_CANDIDATES=200)
"""
import json
import re
import os
import boto3
import psycopg2
import streamlit as st
import streamlit_mermaid as stmd

# =====================================================
# CONFIGURATION — credentials via st.secrets (Streamlit Cloud)
# =====================================================
DB_HOST = st.secrets["db"]["host"]
DB_PORT = st.secrets["db"].get("port", 5432)
DB_NAME = st.secrets["db"].get("name", "postgres")
DB_USER = st.secrets["db"]["user"]
DB_PASSWORD = st.secrets["db"]["password"]
AWS_REGION = st.secrets["aws"].get("region", "eu-west-1")
AWS_ACCESS_KEY = st.secrets["aws"]["access_key_id"]
AWS_SECRET_KEY = st.secrets["aws"]["secret_access_key"]

# Branding client — logo affiché dans le header (PNG/JPG, fond transparent recommandé)
# Télécharger le logo du client et le placer dans le même dossier que ce script.
# Mettre None ou "" pour désactiver.
CLIENT_LOGO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Logo_NCG.png")

EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
LLM_MODEL = "eu.anthropic.claude-sonnet-4-6"
LLM_MODEL_FAST = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"

MAX_CHUNKS_LLM_DEFAULT = 50
MAX_CHUNKS_LLM_BROAD = 80
TOP_K_DISPLAY = 20            # Sources principales affichées
TOP_K_EXTRA = 80              # Hard limit sources supplémentaires (chunks 21 à 80)
MAX_CHUNKS_PER_SOURCE = 3
SIMILARITY_THRESHOLD = 0.15
RRF_K = 60
RERANK_CANDIDATES = 200       # Plus élevé pour compenser l'absence de FlashRank en cloud
RCP_MIN_SLOTS = 3             # POINT 6 : quota minimum RCP
MIN_CHUNK_CHARS = 500         # Ignorer les chunks trop courts (signatures, fragments OCR)

# Multi-turn
MAX_HISTORY_TURNS = 3
MAX_HISTORY_CHARS = 16000     # ~4K tokens budget

PRIMARY_DOC_TYPES = {"SINISTRE", "ENTRETIEN", "COMPTABILITE", "DEVIS", "FACTURE"}

# Liens 3D démo
DEMO_3D_LINKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "URL_SP_demo.txt")
DEMO_3D_LINKS = {}
try:
    with open(DEMO_3D_LINKS_FILE, "r", encoding="utf-8-sig") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#"):
                continue
            if " : " in _line:
                _kw, _url = _line.split(" : ", 1)
                _kw = _kw.strip().upper()
                _url = _url.strip()
                if _url:
                    DEMO_3D_LINKS[_kw] = _url
            elif _line.startswith("http"):
                DEMO_3D_LINKS["Visualisez votre copropriété en 3D"] = _line
except FileNotFoundError:
    print(f"⚠️ Fichier 3D non trouvé : {DEMO_3D_LINKS_FILE}")
except Exception as _e:
    print(f"⚠️ Erreur lecture fichier 3D : {_e}")

# =====================================================
# Page config
# =====================================================
st.set_page_config(
    page_title="PALIM",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# =====================================================
# CSS — POINT 1 : sidebar lisible en mobile
# =====================================================
_ = st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

    .main-header {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
        padding: 2rem 2.5rem; border-radius: 16px; margin-bottom: 1rem; color: white;
    }
    .main-header h1 { color: white; margin: 0 0 0.3rem 0; font-size: 1.8rem; font-weight: 700; }
    .main-header p { color: #a0aec0; margin: 0; font-size: 0.95rem; }

    .answer-card {
        padding: 0; margin: 0; line-height: 1.55;
    }
    .answer-card { color:#e2e8f0; }
    .answer-card h2 { font-size:1.25rem; margin:0.7rem 0 0.25rem; color:#f59e0b; font-weight:600; }
    .answer-card h3 { font-size:1.05rem; margin:0.5rem 0 0.2rem; color:#34d399; font-weight:600; }
    .answer-card h4 { font-size:0.95rem; margin:0.4rem 0 0.15rem; color:#a78bfa; font-weight:500; }
    .answer-card strong { color:#f1f5f9; }
    .answer-card em { color:#cbd5e1; }
    .answer-card a { color:#60a5fa; text-decoration:underline; }
    .answer-card a:hover { color:#93c5fd; }
    .answer-card br { line-height: 1.2; }
    .answer-card table { width:100%; border-collapse:collapse; margin:0.5rem 0; font-size:0.85rem; border:1px solid #475569; border-radius:8px; overflow:hidden; }
    .answer-card th { background:#1e40af; color:#e0f2fe; padding:8px 12px; text-align:left; font-weight:600; border-bottom:2px solid #1d4ed8; font-size:0.82rem; }
    .answer-card td { padding:6px 12px; border-bottom:1px solid #334155; color:#e2e8f0; font-size:0.85rem; }
    .answer-card tr:nth-child(odd) td { background:#1e293b; }
    .answer-card tr:nth-child(even) td { background:#0f172a; }
    .answer-card tr:hover td { background:#334155; }
    .answer-card ul, .answer-card ol { margin:0.3rem 0; padding-left:1.3rem; }
    .answer-card li { margin-bottom:0.1rem; }
    .source-badge {
        display: inline-block; background: linear-gradient(135deg, #667eea, #764ba2);
        color: white; padding: 2px 10px; border-radius: 12px;
        font-size: 0.75rem; font-weight: 600; margin-right: 6px;
    }
    .sim-bar { height: 6px; border-radius: 3px; background: #e2e8f0; margin-top: 4px; }
    .sim-fill { height: 100%; border-radius: 3px; background: linear-gradient(90deg, #667eea, #48bb78); }

    /* ── Sidebar — fond bleu marine ── */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #1a1a2e 0%, #16213e 100%);
    }
    [data-testid="stSidebar"] .stMarkdown { color: #e2e8f0; }
    /* POINT 1 : TOUT le texte sidebar en blanc — couvre toggles, labels, expanders, spans */
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"],
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] span,
    [data-testid="stSidebar"] [data-testid="stExpander"] summary,
    [data-testid="stSidebar"] [data-testid="stExpander"] summary span,
    [data-testid="stSidebar"] [data-testid="stExpander"] p,
    [data-testid="stSidebar"] .stSelectbox label,
    [data-testid="stSidebar"] .stSlider label,
    [data-testid="stSidebar"] .stCheckbox label span { color: #e2e8f0 !important; }

    .stat-card {
        background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
        border-radius: 10px; padding: 0.8rem 1rem; text-align: center; margin-bottom: 0.5rem;
    }
    .stat-card .number { font-size: 1.5rem; font-weight: 700; color: #48bb78; }
    .stat-card .label { font-size: 0.75rem; color: #a0aec0; }

    /* Boutons d'action */
    .action-btn {
        background: none; border: 1px solid #cbd5e0; border-radius: 6px;
        padding: 4px 14px; cursor: pointer; font-size: 0.82rem; color: #4a5568;
        margin-right: 6px; transition: all 0.15s;
    }
    .action-btn:hover { background: #edf2f7; border-color: #a0aec0; }

    .followup-badge {
        display: inline-block; background: #ebf8ff; color: #2b6cb0;
        padding: 2px 10px; border-radius: 8px; font-size: 0.75rem; margin-bottom: 0.3rem;
    }
</style>
""", unsafe_allow_html=True)


# =====================================================
# Connexions (cached)
# =====================================================
def get_db_connection():
    conn = st.session_state.get("_db_conn")
    if conn is not None:
        try:
            conn.isolation_level
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
        connect_timeout=10,
    )
    conn.autocommit = True
    st.session_state["_db_conn"] = conn
    return conn

@st.cache_resource
def get_bedrock_client():
    from botocore.config import Config
    return boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        config=Config(read_timeout=300, retries={"max_attempts": 3})
    )

@st.cache_data(ttl=300)
def get_copros():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT copropriete, COUNT(*) FROM chunks GROUP BY copropriete ORDER BY copropriete;")
        return cur.fetchall()

@st.cache_data(ttl=300)
def get_total_chunks():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM chunks;")
        return cur.fetchone()[0]

# Pré-chauffage Bedrock
if "bedrock_warm" not in st.session_state:
    try:
        _client = get_bedrock_client()
        _client.invoke_model(
            modelId=EMBEDDING_MODEL,
            body=json.dumps({"inputText": "warmup", "dimensions": 1024, "normalize": True}),
            contentType="application/json", accept="application/json"
        )
        st.session_state["bedrock_warm"] = True
    except Exception:
        st.session_state["bedrock_warm"] = False

# Session state — multi-turn
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []


# =====================================================
# Fonctions RAG
# =====================================================

STRATEGY_PROMPT = """Tu es un routeur de requêtes pour un système RAG sur des archives de copropriété.
Analyse cette question d'un gestionnaire de syndic et détermine la stratégie de recherche optimale.

Question actuelle : {query}
{prev_context}

Réponds UNIQUEMENT par un objet JSON valide, sans commentaire :
{{
  "strategie": "inventaire|cible|equilibre",
  "doc_type": "RCP|PV_AG|CONTRAT|DEVIS|FACTURE|BUDGET|DIAGNOSTIC|COURRIER|SINISTRE|COMPTABILITE|ENTRETIEN|ASSURANCE|MUTATION|PLAN|null",
  "annee": 2024 ou null,
  "annee_min": 2020 ou null,
  "annee_max": 2024 ou null,
  "sous_type": "MRI|DDE|RAVALEMENT|ASCENSEUR|CHAUFFAGE|TOITURE|SYNDIC|etc ou null",
  "statut": "actif|expire|resilie|cloture|en_cours|null",
  "is_followup": true ou false,
  "expanded_query": "version complète et autonome de la question si is_followup=true, sinon null"
}}

Règles pour la stratégie :
- "inventaire" : la question demande une LISTE exhaustive, un historique, une comparaison sur plusieurs années, un récapitulatif, ou utilise des mots comme "tous", "quels sont", "combien", "depuis", "évolution"
- "cible" : la question porte sur UN document précis, un article, une résolution, un détail spécifique, ou demande d'expliquer/détailler quelque chose
- "equilibre" : entre les deux, question ouverte sans besoin d'exhaustivité ni de document précis

Règles pour les filtres :
- Ne remplis que les champs que tu peux déduire avec CERTITUDE de la question
- annee : année exacte mentionnée. Si "depuis 2020" → annee_min=2020, annee=null
- Si deux années mentionnées → annee_min et annee_max, annee=null
- sous_type : catégorie précise du document (MRI=multirisque immeuble, DDE=dégât des eaux, SYNDIC=contrat syndic, etc.)
- statut : seulement si la question implique un état (en cours, actif, résilié, clos)
- Tout champ incertain → null

Règles pour le suivi de conversation :
- is_followup=true si la question actuelle est une continuation de la question précédente (trop courte ou ambiguë pour être comprise seule, fait référence implicite au contexte précédent)
- Si is_followup=true, expanded_query DOIT être une reformulation complète et autonome combinant le contexte précédent et la question actuelle. Exemple : question précédente "liste des sinistres en 2023", question actuelle "et en 2024 ?" → expanded_query "liste des sinistres en 2024"
- Si is_followup=false → expanded_query=null"""


def detect_strategy_haiku(query, prev_query=None):
    """
    v4 : détection unifiée stratégie + pré-filtrage + suivi conversationnel via Haiku.
    Retourne (strategie, prefilter, doc_type_hint, is_followup, expanded_query) ou None.
    """
    prev_context = f"Question précédente de l'utilisateur : {prev_query}" if prev_query else "Pas de question précédente (premier tour)."
    prompt = STRATEGY_PROMPT.format(query=query, prev_context=prev_context)
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 200,
        "messages": [{"role": "user", "content": prompt}]
    })

    try:
        bedrock = get_bedrock_client()
        response = bedrock.invoke_model(
            modelId=LLM_MODEL_FAST, body=body,
            contentType="application/json", accept="application/json"
        )
        result_text = json.loads(response["body"].read())["content"][0]["text"].strip()
        result_text = re.sub(r"^```json?\s*", "", result_text)
        result_text = re.sub(r"\s*```$", "", result_text)
        parsed = json.loads(result_text)

        strategie = parsed.get("strategie", "equilibre")
        if strategie not in ("inventaire", "cible", "equilibre"):
            strategie = "equilibre"

        # doc_type pour le boost RRF
        doc_type_hint = parsed.get("doc_type")
        if doc_type_hint == "null":
            doc_type_hint = None

        # Construire prefilter à partir des champs non-null
        prefilter = {}
        for key in ("doc_type", "annee", "annee_min", "annee_max", "sous_type", "statut"):
            val = parsed.get(key)
            if val is not None and val != "null":
                prefilter[key] = val

        # Activer prefilter seulement si au moins un signal structurel
        if not any(prefilter.get(k) for k in ("annee", "annee_min", "sous_type", "statut")):
            prefilter = None

        # Suivi conversationnel
        is_followup = bool(parsed.get("is_followup", False))
        expanded_query = parsed.get("expanded_query")
        if expanded_query == "null" or not expanded_query:
            expanded_query = None

        return strategie, prefilter, doc_type_hint, is_followup, expanded_query

    except Exception:
        return None


def detect_retrieval_strategy(query, demo_mode=False, prev_query=None):
    """
    v4 : détection via Haiku avec fallback.
    Retourne (chunks_per_source, doc_type_boost, max_chunks_llm, label, prefilter, doc_type_hint, is_followup, expanded_query).
    """
    haiku_result = detect_strategy_haiku(query, prev_query=prev_query)

    if haiku_result:
        strategie, prefilter, doc_type_hint, is_followup, expanded_query = haiku_result

        if strategie == "inventaire":
            mcl = 40 if demo_mode else 80
            return 2, 0.03, mcl, "🔎 Inventaire", prefilter, doc_type_hint, is_followup, expanded_query
        elif strategie == "cible":
            mcl = 30 if demo_mode else 50
            return 8, 0.005, mcl, "🔬 Ciblé", prefilter, doc_type_hint, is_followup, expanded_query
        else:
            mcl = 30 if demo_mode else 50
            return 3, 0.01, mcl, "⚖️ Équilibré", prefilter, doc_type_hint, is_followup, expanded_query

    # Fallback : mode équilibré sans pré-filtrage
    mcl = 30 if demo_mode else 50
    return 3, 0.01, mcl, "⚖️ Équilibré (fallback)", None, None, False, None


def get_embedding(text):
    bedrock = get_bedrock_client()
    if len(text) > 5000:
        text = text[:5000]
    body = json.dumps({"inputText": text, "dimensions": 1024, "normalize": True})
    response = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL, body=body,
        contentType="application/json", accept="application/json"
    )
    return json.loads(response["body"].read())["embedding"]


# =====================================================
# Phase 1b — Décomposition temporelle + filtrage résolutions
# =====================================================
def decompose_temporal_query(query, prefilter):
    """
    Décompose une requête inventaire en sous-requêtes par année.
    Retourne une liste de (sub_query, sub_prefilter) ou None si pas de décomposition.
    Condition : plage temporelle >= 3 ans dans le prefilter.
    """
    if not prefilter:
        return None
    annee_min = prefilter.get("annee_min")
    annee_max = prefilter.get("annee_max")
    if annee_min is None or annee_max is None:
        return None
    try:
        annee_min, annee_max = int(annee_min), int(annee_max)
    except (ValueError, TypeError):
        return None
    span = annee_max - annee_min
    if span < 3:
        return None

    sub_queries = []
    for year in range(annee_min, annee_max + 1):
        sub_pf = {k: v for k, v in prefilter.items() if k not in ("annee_min", "annee_max")}
        sub_pf["annee"] = year
        sub_queries.append((f"{query} {year}", sub_pf))
    return sub_queries


def query_targets_elections(query):
    """Détecte si la requête porte spécifiquement sur les élections/membres CS."""
    patterns = [
        r"[ée]lu|[ée]lection|membre.*conseil.*syndical",
        r"qui.*(?:siège|compose|élu)",
        r"conseil\s+syndical.*(?:compos|membre|élu)",
        r"titulaire|suppléant",
        r"pr[ée]sident.*(?:séance|AG|assemblée)",
    ]
    return any(re.search(p, query, re.IGNORECASE) for p in patterns)


def filter_resolution_categories(results, query, strategie):
    """
    Filtre les chunks PROCEDURE_AG et ELECTION_CS en mode inventaire,
    sauf si la requête porte explicitement sur les élections.
    r[10] = resolution_category (ajouté au SELECT).
    """
    if strategie != "inventaire":
        return results
    if query_targets_elections(query):
        return results
    return [r for r in results if len(r) <= 10 or r[10] not in ("PROCEDURE_AG", "ELECTION_CS")]


def search_chunks(query, copropriete=None, max_chunks=MAX_CHUNKS_LLM_DEFAULT,
                  sim_threshold=SIMILARITY_THRESHOLD, chunks_per_source=MAX_CHUNKS_PER_SOURCE,
                  doc_type_boost=0.01, prefilter=None, doc_type_hint=None):
    """
    Pipeline hybride 5 étapes : pré-filtrage document (conditionnel) + RRF + diversité + rerank + quota RCP.
    doc_type_hint vient de Haiku (detect_strategy_haiku), plus de détection par mots-clés.
    """
    conn = get_db_connection()
    query_embedding = get_embedding(query)

    # ── Étape 0 : pré-filtrage document via table documents ──
    prefilter_files = None
    prefilter_active = False
    prefilter_unique_groups = 0

    if prefilter:
        try:
            with conn.cursor() as cur:
                pf_clauses, pf_params = [], []

                if copropriete:
                    pf_clauses.append("copropriete = %s")
                    pf_params.append(copropriete)

                if prefilter.get("doc_type"):
                    pf_clauses.append("(COALESCE(doc_type_corrige, doc_type) = %s OR dossier_lie = %s)")
                    pf_params.append(prefilter["doc_type"])
                    pf_params.append(prefilter["doc_type"])

                if prefilter.get("annee"):
                    pf_clauses.append("annee = %s")
                    pf_params.append(prefilter["annee"])

                if prefilter.get("annee_min") and prefilter.get("annee_max"):
                    pf_clauses.append("annee BETWEEN %s AND %s")
                    pf_params.extend([prefilter["annee_min"], prefilter["annee_max"]])
                elif prefilter.get("annee_min"):
                    pf_clauses.append("annee >= %s")
                    pf_params.append(prefilter["annee_min"])

                if prefilter.get("sous_type"):
                    pf_clauses.append("sous_type = %s")
                    pf_params.append(prefilter["sous_type"])

                if prefilter.get("statut"):
                    pf_clauses.append("statut = %s")
                    pf_params.append(prefilter["statut"])

                if pf_clauses:
                    pf_sql = "SELECT source_file, COALESCE(groupe_doc, source_file) FROM documents WHERE " + " AND ".join(pf_clauses)
                    cur.execute(pf_sql, pf_params)
                    pf_rows = cur.fetchall()
                    prefilter_files = [r[0] for r in pf_rows]
                    prefilter_unique_groups = len(set(r[1] for r in pf_rows))

                    # Fallback : 0 résultats ou >50 → désactiver le pré-filtrage
                    if 0 < len(prefilter_files) <= 50:
                        prefilter_active = True
                    # 0 ou >50 : silencieusement ignoré, pipeline complet
        except Exception:
            # Table documents absente ou erreur SQL → fallback silencieux au pipeline complet
            prefilter_active = False

    # ── Cap dynamique chunks_per_source basé sur le nombre de groupes UNIQUES pré-filtrés ──
    if prefilter_active and prefilter_files:
        n_unique = prefilter_unique_groups if prefilter_unique_groups > 0 else len(prefilter_files)
        dynamic_cap = max(2, min(15, max_chunks // max(n_unique, 1)))
        chunks_per_source = dynamic_cap
        # Pré-filtrage actif → les bons docs sont déjà sélectionnés, pas besoin du seuil vectoriel
        sim_threshold = 0.05

    with conn.cursor() as cur:
        where_clauses, params_before = [], []
        # Exclure les chunks trop courts (signatures, pieds de page, fragments OCR)
        where_clauses.append(f"c.nb_caracteres >= {MIN_CHUNK_CHARS}")

        if copropriete:
            where_clauses.append("c.copropriete = %s")
            params_before.append(copropriete)

        if prefilter_active and prefilter_files:
            placeholders = ",".join(["%s"] * len(prefilter_files))
            where_clauses.append(f"c.source_file IN ({placeholders})")
            params_before.extend(prefilter_files)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        doc_type_for_boost = doc_type_hint if doc_type_hint else "__NONE__"

        # Quand le pré-filtrage est actif, ouvrir large la diversité SQL
        # pour avoir des candidats de tout le document
        sql_cap = chunks_per_source
        sql_limit = RERANK_CANDIDATES
        if prefilter_active:
            sql_cap = 30  # Laisser passer beaucoup de chunks par source
            sql_limit = max(RERANK_CANDIDATES, max_chunks * 4)

        sql = f"""
            WITH base AS (
                SELECT c.chunk_id, c.copropriete, c.source_file, c.nom_fichier, c.doc_type,
                       c.text, c.chunk_index, c.resolution_category,
                       COALESCE(d.groupe_doc, c.source_file) as groupe_doc,
                       1 - (c.embedding <=> %s::vector) as vec_similarity,
                       ts_rank(c.text_search, plainto_tsquery('french', %s), 32) as bm25_score,
                       CASE WHEN c.doc_type = %s THEN %s ELSE 0 END as doc_type_boost
                FROM chunks c
                LEFT JOIN documents d ON c.source_file = d.source_file
                {where_sql}
            ),
            with_ranks AS (
                SELECT *,
                       row_number() OVER (ORDER BY vec_similarity DESC) as vec_rank,
                       row_number() OVER (ORDER BY bm25_score DESC) as bm25_rank
                FROM base
            ),
            with_rrf AS (
                SELECT *,
                       (1.0 / ({RRF_K} + vec_rank)
                        + 1.0 / ({RRF_K} + bm25_rank)
                        + doc_type_boost) as rrf_score
                FROM with_ranks
            ),
            diversified AS (
                SELECT *,
                       row_number() OVER (
                           PARTITION BY groupe_doc ORDER BY rrf_score DESC
                       ) as rank_in_source
                FROM with_rrf
            )
            SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                   text, vec_similarity, bm25_score, rrf_score, chunk_index,
                   resolution_category
            FROM diversified
            WHERE rank_in_source <= %s
              AND vec_similarity >= %s
            ORDER BY rrf_score DESC
            LIMIT %s
        """

        params = [
            str(query_embedding), query,
            doc_type_for_boost, doc_type_boost,
            *params_before,
            sql_cap, sim_threshold, sql_limit,
        ]
        cur.execute(sql, params)
        raw_results = cur.fetchall()

    # Déduplication
    seen_texts, deduped = set(), []
    for r in raw_results:
        sig = r[5][:300].strip()
        if sig not in seen_texts:
            seen_texts.add(sig)
            deduped.append(r)

    # (Pas de FlashRank en cloud — RRF score utilisé directement, compensé par RERANK_CANDIDATES=200)

    # ── Cap dynamique par source (quand pré-filtrage actif) ──
    if prefilter_active:
        from collections import defaultdict
        by_source = defaultdict(list)
        for r in deduped:
            by_source[r[2]].append(r)  # r[2] = source_file, ordre RRF préservé
        capped = []
        for sf, chunks in by_source.items():
            capped.extend(chunks[:chunks_per_source])
        # Re-trier par score RRF (= ordre dans deduped original)
        original_order = {id(r): i for i, r in enumerate(deduped)}
        capped.sort(key=lambda r: original_order.get(id(r), 999))
        deduped = capped

    # ── POINT 6 : quota minimum RCP ──
    top = deduped[:max_chunks]
    rcp_in_top = sum(1 for r in top if r[4] == "RCP")
    if rcp_in_top < RCP_MIN_SLOTS:
        rcp_below = [r for r in deduped[max_chunks:] if r[4] == "RCP"]
        needed = min(RCP_MIN_SLOTS - rcp_in_top, len(rcp_below))
        if needed > 0:
            extra_rcp = rcp_below[:needed]
            for _ in range(needed):
                for j in range(len(top) - 1, -1, -1):
                    if top[j][4] != "RCP":
                        top.pop(j)
                        break
            top.extend(extra_rcp)

    return top, doc_type_hint, prefilter_active


def search_decomposed(query, copropriete, max_chunks, sim_threshold,
                      chunks_per_source, doc_type_boost, prefilter, doc_type_hint,
                      strategie):
    """
    Phase 1b — Recherche décomposée par année pour les requêtes inventaire temporelles.
    Lance N sous-requêtes en parallèle, agrège et déduplique les résultats.
    Retourne le même format que search_chunks().
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    sub_queries = decompose_temporal_query(query, prefilter)
    if not sub_queries:
        # Pas de décomposition → recherche classique
        results, dt_hint, pf_active = search_chunks(
            query, copropriete, max_chunks, sim_threshold,
            chunks_per_source, doc_type_boost, prefilter, doc_type_hint
        )
        results = filter_resolution_categories(results, query, strategie)
        return results, dt_hint, pf_active

    # Budget par sous-requête : répartir équitablement
    per_year_budget = max(10, max_chunks // len(sub_queries))

    all_results = []
    def _run_sub(sub_query, sub_pf):
        return search_chunks(
            sub_query, copropriete, per_year_budget, sim_threshold,
            chunks_per_source, doc_type_boost, sub_pf, doc_type_hint
        )

    with ThreadPoolExecutor(max_workers=min(len(sub_queries), 8)) as executor:
        futures = {executor.submit(_run_sub, sq, spf): sq for sq, spf in sub_queries}
        for future in as_completed(futures):
            try:
                results, _, _ = future.result()
                all_results.extend(results)
            except Exception:
                pass  # Sous-requête échouée → ignorer silencieusement

    # Déduplier par chunk_id (garder le meilleur score RRF = r[8])
    best_by_id = {}
    for r in all_results:
        cid = r[0]  # chunk_id
        if cid not in best_by_id or float(r[8]) > float(best_by_id[cid][8]):
            best_by_id[cid] = r

    merged = sorted(best_by_id.values(), key=lambda r: float(r[8]), reverse=True)

    # Filtrage résolutions
    merged = filter_resolution_categories(merged, query, strategie)

    # Cap global
    merged = merged[:max_chunks]

    return merged, doc_type_hint, True


# =====================================================
# Multi-turn — historique LLM (POINT 4)
# =====================================================
def build_history_messages(chat_history):
    """Extrait les derniers tours pour injection dans le payload LLM."""
    pairs, total_chars = [], 0
    i = len(chat_history) - 1
    while i >= 1 and len(pairs) < MAX_HISTORY_TURNS:
        if chat_history[i]["role"] == "assistant" and chat_history[i - 1]["role"] == "user":
            u = chat_history[i - 1]["content"]
            a = chat_history[i]["content"]
            pc = len(u) + len(a)
            if total_chars + pc > MAX_HISTORY_CHARS:
                break
            pairs.append((u, a))
            total_chars += pc
            i -= 2
        else:
            i -= 1

    messages = []
    for u, a in reversed(pairs):
        messages.append({"role": "user", "content": u})
        messages.append({"role": "assistant", "content": a[:4000] + "…" if len(a) > 4000 else a})
    return messages


# =====================================================
# LLM — POINTS 8 + 9 : concision + multi-turn
# =====================================================
def build_llm_payload(query, search_results, doc_type_hint, chat_history=None):
    """Construit system prompt, liste de messages, max_tokens."""
    # Contexte RAG
    context_parts = []
    for i, result in enumerate(search_results):
        chunk_id, copro, source, filename, doc_type, text, *_ = result
        source_type = "PRIMAIRE" if doc_type in PRIMARY_DOC_TYPES else "CONTEXTUEL"
        context_parts.append(
            f"[Source {i+1}] [{source_type}] Copropriété: {copro} | Fichier: {filename} | "
            f"Type: {doc_type}\n{text}"
        )
    context = "\n\n---\n\n".join(context_parts)

    # ── System prompt — POINT 8 : concision ──
    system_prompt = """Tu es un assistant expert en gestion de copropriété pour un syndic professionnel.
Tu réponds aux questions en te basant UNIQUEMENT sur les extraits de documents fournis.

Règles STRICTES :
1. Cite toujours les sources (numéro de source, nom du fichier, article ou résolution si applicable)
2. Si l'information n'est PAS dans les extraits, dis-le clairement. Ne fabrique JAMAIS une réponse sans source.
3. Croise les informations entre les différents documents quand c'est pertinent
4. Utilise un langage professionnel adapté au métier de syndic
5. Si la question porte sur plusieurs thèmes, structure ta réponse par thème
6. Si un extrait contient des données OCR de mauvaise qualité, signale-le et extrais ce qui est lisible
7. Quand tu cites des montants, tantièmes ou numéros de lot, vérifie la cohérence entre sources
8. Chaque source est marquée [PRIMAIRE] ou [CONTEXTUEL].
   Les sources PRIMAIRES correspondent chacune à un événement DISTINCT.
   Les sources CONTEXTUELLES (PV_AG, RCP, CONTRAT…) enrichissent la réponse.
9. Pour un inventaire, scanne CHAQUE source PRIMAIRE sans exception.
10. Signale les références à des éléments absents des extraits fournis.

CONCISION (très important) :
- Va droit au fait. Pas de reformulation de la question, pas de phrases d'introduction inutiles.
- Réponds de manière dense et structurée : faits, dates, montants, références.
- Limite ta réponse à ~400 mots sauf pour les inventaires exhaustifs.
- Pas de formules de politesse ni de conclusions génériques.

DIAGRAMMES MERMAID (capacité spéciale — SYNTAXE OBLIGATOIRE) :
- Tu PEUX et DOIS utiliser des diagrammes Mermaid quand c'est pertinent.
- Tu DOIS TOUJOURS envelopper le code Mermaid dans un bloc code avec triple backticks ET le mot "mermaid" :
  ```mermaid
  flowchart TD
    A[Début] --> B[Fin]
  ```
- JAMAIS de code Mermaid nu sans les triple backticks — l'interface ne le rendra pas.
- Utilise flowchart TD/LR pour workflows, timeline pour chronologies, sequenceDiagram pour interactions.
- Garde les labels de nœuds COURTS (max 30 caractères). Détaille dans le texte autour du diagramme, pas dans les nœuds.
- Ne mets PAS de références [Src N] dans les nœuds Mermaid — cite les sources dans le texte qui accompagne le diagramme.

EXCLUSION DES RÉSOLUTIONS DE PROCÉDURE (PV d'AG) :
- Lors d'un inventaire de résolutions, EXCLURE les résolutions de procédure récurrentes :
  désignation du président de séance, du bureau, du scrutateur, du secrétaire,
  rapport du conseil syndical, approbation des comptes, quitus au syndic,
  fixation des modalités de contrôle des comptes, autorisation Police/Gendarmerie.
- Inclure ces résolutions UNIQUEMENT si la question porte spécifiquement dessus
  (ex : "qui a été président de séance ?", "quitus refusé ?" , "qui a été élu au conseil syndical ?")."""

    # ── POINT 9 : instruction multi-turn ──
    has_history = bool(chat_history)
    if has_history:
        system_prompt += """

CONTEXTE CONVERSATIONNEL :
- Un historique de conversation est fourni. Utilise-le pour comprendre les questions de suivi.
- Ne répète PAS les informations déjà données sauf demande explicite.
- Appuie-toi sur le contexte des échanges précédents."""

    # Hints
    context_hints = []
    if doc_type_hint:
        context_hints.append(f"Type de document principal : {doc_type_hint} (mais l'info peut apparaître dans d'autres types)")
    hints_text = "\n".join(context_hints) if context_hints else "Aucun filtre spécifique"

    primary_sources = set()
    primary_types_found = set()
    for r in search_results:
        if r[4] in PRIMARY_DOC_TYPES:
            primary_sources.add(r[2])
            primary_types_found.add(r[4])

    user_prompt = f"""Question : {query}

{hints_text}

Voici les {len(search_results)} extraits de documents les plus pertinents :

{context}

Réponds de manière structurée et précise en citant les sources.
Si la question demande une liste exhaustive, cite TOUTES les occurrences trouvées."""

    if primary_sources:
        user_prompt += f"\n\n⚠️ {len(primary_sources)} sources PRIMAIRES ({', '.join(sorted(primary_types_found))}). Couvre-les toutes."

    # ── POINT 8 : max_tokens adaptatif ──
    max_tokens_response = 8192 if len(search_results) > 50 else (4096 if len(search_results) > 30 else 2500)

    # ── Messages avec historique (POINT 4) ──
    messages = build_history_messages(chat_history) if has_history else []
    messages.append({"role": "user", "content": user_prompt})

    return system_prompt, messages, max_tokens_response


def generate_answer(query, search_results, doc_type_hint,
                    model_id=LLM_MODEL, chat_history=None):
    """Synchrone (non-streaming)."""
    bedrock = get_bedrock_client()
    system_prompt, messages, max_tokens = build_llm_payload(
        query, search_results, doc_type_hint, chat_history
    )
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": messages,
    })
    response = bedrock.invoke_model(
        modelId=model_id, body=body,
        contentType="application/json", accept="application/json"
    )
    return json.loads(response["body"].read())["content"][0]["text"]


def generate_answer_stream(query, search_results, doc_type_hint,
                           model_id, placeholder, chat_history=None):
    """Streaming : écrit progressivement dans un placeholder Streamlit."""
    bedrock = get_bedrock_client()
    system_prompt, messages, max_tokens = build_llm_payload(
        query, search_results, doc_type_hint, chat_history
    )
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": messages,
    })
    response = bedrock.invoke_model_with_response_stream(
        modelId=model_id, body=body,
        contentType="application/json", accept="application/json"
    )
    full_text = ""
    for event in response["body"]:
        chunk = json.loads(event["chunk"]["bytes"])
        if chunk.get("type") == "content_block_delta":
            delta = chunk.get("delta", {}).get("text", "")
            full_text += delta
            placeholder.markdown(full_text + "▌")
    placeholder.markdown(full_text)
    return full_text


def _md_tables_to_html(text):
    """Convert markdown tables (lines starting with |) to proper HTML tables.
    Handles alignment from separator row (:---, :---:, ---:)."""
    lines = text.split('\n')
    result_lines = []
    i = 0
    while i < len(lines):
        # Detect start of a markdown table (line with | that isn't a separator)
        if re.match(r'^\s*\|.*\|', lines[i]):
            table_lines = []
            while i < len(lines) and re.match(r'^\s*\|.*\|', lines[i]):
                table_lines.append(lines[i].strip())
                i += 1
            # Need at least 2 lines (header + separator) for a valid table
            if len(table_lines) >= 2 and re.match(r'^\|[\s:_-]+\|', table_lines[1].replace('|', '|')):
                # Parse alignment from separator row
                sep_cells = [c.strip() for c in table_lines[1].strip('|').split('|')]
                aligns = []
                for cell in sep_cells:
                    cell = cell.strip()
                    if cell.startswith(':') and cell.endswith(':'):
                        aligns.append('center')
                    elif cell.endswith(':'):
                        aligns.append('right')
                    else:
                        aligns.append('left')
                # Parse header
                header_cells = [c.strip() for c in table_lines[0].strip('|').split('|')]
                html = '<table><thead><tr>'
                for idx, cell in enumerate(header_cells):
                    align = aligns[idx] if idx < len(aligns) else 'left'
                    html += f'<th style="text-align:{align}">{cell}</th>'
                html += '</tr></thead><tbody>'
                # Parse data rows (skip separator at index 1)
                for row_line in table_lines[2:]:
                    row_cells = [c.strip() for c in row_line.strip('|').split('|')]
                    html += '<tr>'
                    for idx, cell in enumerate(row_cells):
                        align = aligns[idx] if idx < len(aligns) else 'left'
                        html += f'<td style="text-align:{align}">{cell}</td>'
                    html += '</tr>'
                html += '</tbody></table>'
                result_lines.append(html)
            else:
                # Not a valid table, keep original lines
                result_lines.extend(table_lines)
        else:
            result_lines.append(lines[i])
            i += 1
    return '\n'.join(result_lines)


def linkify_sources(text, max_source_num, anchor_prefix=""):
    """Transforme les 'Source N' en liens cliquables et enveloppe dans un div HTML
    pour forcer Streamlit à rendre le HTML (compatibilité versions récentes)."""
    pfx = f"{anchor_prefix}-" if anchor_prefix else ""

    def make_link(num):
        if 1 <= num <= max_source_num:
            target_id = f"source-{pfx}{num}"
            return (f'<a href="javascript:void(0)" '
                    f'onclick="window.parent.document.getElementById(\'{target_id}\')?.scrollIntoView({{behavior:\'smooth\',block:\'center\'}})" '
                    f'style="color:#60a5fa;text-decoration:underline;font-weight:500;cursor:pointer">'
                    f'Source {num}</a>')
        return f'Source {num}'

    # Extract mermaid blocks BEFORE any markdown conversion
    # Replace ```mermaid ... ``` with placeholders, render them after
    _mermaid_blocks = []
    def _extract_mermaid(match):
        idx = len(_mermaid_blocks)
        _mermaid_blocks.append(match.group(1).strip())
        return f"%%MERMAID_{idx}%%"
    # Primary: fenced ```mermaid blocks
    text = re.sub(r'```mermaid\s*\n(.*?)```', _extract_mermaid, text, flags=re.DOTALL)

    # Pre-process: fix single-line mermaid (Claude sometimes sends entire diagram on one line)
    # Detect "flowchart TD  A[...] --> B[...]  C{...}" pattern and re-inject newlines
    _mermaid_oneline = re.compile(
        r'^((?:flowchart|graph|sequenceDiagram|gantt|pie|timeline|classDiagram|stateDiagram|erDiagram|journey)'
        r'(?:\s+(?:TD|LR|RL|BT))?)'
        r'(\s{2,}\w+[\[\(\{].+)$',
        re.MULTILINE
    )
    def _fix_oneline_mermaid(match):
        header = match.group(1)
        body = match.group(2)
        # Re-inject newlines before each node definition or arrow chain
        # Pattern: 2+ spaces before a node ID (uppercase letter + optional digits + bracket)
        body = re.sub(r'\s{2,}(?=\w+[\[\(\{>])', '\n    ', body)
        body = re.sub(r'\s{2,}(?=\w+\s*-->)', '\n    ', body)
        body = re.sub(r'\s{2,}(?=\w+\s*&\s*\w+)', '\n    ', body)  # A & B --> C
        body = re.sub(r'\s{2,}(?=subgraph\s|end\s|style\s|class\s|click\s|linkStyle\s)', '\n    ', body)
        return header + '\n' + body.strip()
    text = _mermaid_oneline.sub(_fix_oneline_mermaid, text)
    # Fallback: bare mermaid code without fencing
    # Detect lines starting with mermaid keywords, then grab all connected lines
    # (lines containing mermaid syntax: arrows, pipes, brackets, subgraph, end, style, class)
    _mermaid_start = re.compile(
        r'^(flowchart|graph|sequenceDiagram|gantt|pie|timeline|classDiagram|stateDiagram|erDiagram|journey)'
        r'(?:\s+(?:TD|LR|RL|BT))?',
        re.MULTILINE
    )
    _mermaid_line = re.compile(
        r'^(?:'
        r'.*(?:-->|---|==>|-.->|\|>).*|'  # arrows (NOT bare pipes — those appear in markdown tables)
        r'.*:::.*|'  # mermaid class syntax
        r'\s+\w+[\[\(\{].*[\]\)\}]|'  # indented node definitions like "  A[text]"
        r'\w+[\[\(\{].*[\]\)\}]\s*-->|'  # node def followed by arrow "A[text] -->"
        r'\s*(?:subgraph|end)\s*.*|'  # subgraph / end
        r'\s*(?:style|class|click|linkStyle)\s.*|'  # styling commands
        r'\s*%%.*|'  # mermaid comments
        r'\s*$'  # blank lines within diagram
        r')'
    )
    def _extract_bare_mermaid(text_input):
        lines = text_input.split('\n')
        result = []
        i = 0
        while i < len(lines):
            m = _mermaid_start.match(lines[i])
            if m:
                block_lines = [lines[i]]
                i += 1
                while i < len(lines) and _mermaid_line.match(lines[i]):
                    block_lines.append(lines[i])
                    i += 1
                if len(block_lines) >= 2:  # at least keyword + 1 line of code = diagram
                    idx = len(_mermaid_blocks)
                    _mermaid_blocks.append('\n'.join(block_lines))
                    result.append(f"%%MERMAID_{idx}%%")
                else:
                    result.extend(block_lines)
            else:
                result.append(lines[i])
                i += 1
        return '\n'.join(result)
    text = _extract_bare_mermaid(text)

    # Convert markdown tables to HTML FIRST (before other conversions)
    linkified = _md_tables_to_html(text)

    # Step 1: Normalize all Source/Src variants to plain "Source N"
    # Remove bold markers (__), brackets, and normalize Src→Source
    linkified = re.sub(r'__(?:Source|Src)\s*(\d+)__', r'Source \1', linkified)
    linkified = re.sub(r'\*\*(?:Source|Src)\s*(\d+)\*\*', r'Source \1', linkified)
    linkified = re.sub(r'\[(?:Source|Src)\s*(\d+)\]', r'Source \1', linkified)
    linkified = re.sub(r'(?<!\w)Src\s+(\d+)(?!\w)', r'Source \1', linkified)

    # Step 2: Expand ranges "Source 12-13" → "Source 12, Source 13"
    def expand_source_range(match):
        start = int(match.group(1))
        end = int(match.group(2))
        if end > start and (end - start) <= 20:  # safety cap
            return ", ".join(f"Source {n}" for n in range(start, end + 1))
        return match.group(0)

    linkified = re.sub(
        r'(?<!\w)Sources?\s+(\d+)\s*[-–]\s*(\d+)(?!\w)',
        expand_source_range, linkified
    )

    # Step 3: Expand "Source N, N, N" and "Source N/N/N" lists
    # After normalization, bare numbers following a Source reference get expanded
    def expand_source_list(match):
        nums = re.findall(r'\d+', match.group(0))
        return ", ".join(f"Source {n}" for n in nums)

    linkified = re.sub(
        r'(?<!\w)Sources?\s+\d+(?:\s*[,/&]\s*\d+)+',
        expand_source_list, linkified
    )
    # Also expand "Source N, Source N, N, N" (mixed: some have prefix, some don't)
    linkified = re.sub(
        r'Source\s+(\d+)(?:\s*[,/&]\s*(?:Source\s+)?(\d+))+',
        expand_source_list, linkified
    )

    # Step 4: Linkify each "Source N" (all variants already normalized to "Source N" in step 1)
    def replace_source(match):
        num = int(match.group(1))
        return make_link(num)

    linkified = re.sub(r'(?<!\w)Source\s+(\d+)(?!\w)', replace_source, linkified)

    # Convertir le markdown basique en HTML pour le rendu dans le div
    # Gras **text** ou __text__
    linkified = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', linkified)
    linkified = re.sub(r'__(.+?)__', r'<strong>\1</strong>', linkified)
    # Italique *text* ou _text_ (pas après un mot pour éviter les faux positifs)
    linkified = re.sub(r'(?<!\w)\*([^*]+?)\*(?!\w)', r'<em>\1</em>', linkified)
    # Headers markdown → proper HTML heading tags (h2, h3, h4) with correct sizing
    linkified = re.sub(r'^####\s+(.+)$', r'<h4>\1</h4>', linkified, flags=re.MULTILINE)
    linkified = re.sub(r'^###\s+(.+)$', r'<h3>\1</h3>', linkified, flags=re.MULTILINE)
    linkified = re.sub(r'^##\s+(.+)$', r'<h2>\1</h2>', linkified, flags=re.MULTILINE)
    linkified = re.sub(r'^#\s+(.+)$', r'<h2>\1</h2>', linkified, flags=re.MULTILINE)
    # Numbered lists
    linkified = re.sub(r'^(\d+)\.\s+(.+)$', r'<li value="\1">\2</li>', linkified, flags=re.MULTILINE)
    # Listes à puces
    linkified = re.sub(r'^[-•]\s+(.+)$', r'<li>\1</li>', linkified, flags=re.MULTILINE)
    # Wrap consecutive <li> in <ul> or <ol>
    linkified = re.sub(
        r'((?:<li(?:\s+value="\d+")?>.+?</li>\n?)+)',
        lambda m: '<ul>' + m.group(0) + '</ul>' if 'value=' not in m.group(0) else '<ol>' + m.group(0) + '</ol>',
        linkified,
    )
    # Retours à la ligne (skip lines that are already block-level HTML)
    lines_out = []
    for line in linkified.split('\n'):
        stripped = line.strip()
        if stripped.startswith(('<h2', '<h3', '<h4', '<table', '<ul', '<ol', '<li', '</ul', '</ol', '</table', '</thead', '</tbody', '<thead', '<tbody', '<tr', '</tr')):
            lines_out.append(line)
        else:
            lines_out.append(line)
    linkified = '\n'.join(lines_out)
    linkified = re.sub(r'\n\n', '<br>', linkified)
    linkified = re.sub(r'(?<!>)\n(?!<)', '<br>', linkified)
    # Strip leading/trailing <br> to avoid blank line at top of answer
    linkified = re.sub(r'^(<br>\s*)+', '', linkified)
    linkified = re.sub(r'(<br>\s*)+$', '', linkified)

    # Return as segments: list of (type, content) tuples
    # "html" segments are rendered with st.html(), "mermaid" with stmd.st_mermaid()
    if not _mermaid_blocks:
        return [("html", f'<div class="answer-card">{linkified}</div>')]

    segments = []
    remaining = linkified
    for idx, mermaid_code in enumerate(_mermaid_blocks):
        placeholder = f"%%MERMAID_{idx}%%"
        if placeholder in remaining:
            before, after = remaining.split(placeholder, 1)
            if before.strip():
                segments.append(("html", f'<div class="answer-card">{before}</div>'))
            segments.append(("mermaid", mermaid_code))
            remaining = after
    if remaining.strip():
        segments.append(("html", f'<div class="answer-card">{remaining}</div>'))
    return segments


def render_answer_segments(segments):
    """Render a list of (type, content) segments produced by linkify_sources.
    HTML segments → st.markdown(unsafe_allow_html) so anchor links work in main DOM.
    Mermaid segments → stmd.st_mermaid() native Streamlit component."""
    for seg_type, seg_content in segments:
        if seg_type == "mermaid":
            # Clean mermaid code for streamlit-mermaid (v10.2.4)
            clean_code = seg_content.strip()
            # 1. Remove emoji characters (mermaid 10.2.4 can't parse Unicode emoji in labels)
            clean_code = re.sub(r'[\U0001F300-\U0001FAFF\U00002702-\U000027B0\U0000FE0F]', '', clean_code)
            # 2. Normalize <br/> to <br> (strict XML self-closing can fail in some mermaid versions)
            clean_code = clean_code.replace('<br/>', '<br>')
            # 3. Replace literal \n in node labels with <br>
            clean_code = clean_code.replace('\\n', '<br>')
            # 4. Remove __bold__ markers that Claude sometimes puts in mermaid code
            clean_code = re.sub(r'__([^_]+)__', r'\1', clean_code)
            # 5. Remove any leaked HTML tags (from our linkification pipeline)
            # IMPORTANT: only match valid HTML tags (start with letter), not mathematical < like "< 1600€"
            clean_code = re.sub(r'<(?!br)(?!br>)(?=[a-zA-Z/])[^>]+>', '', clean_code)
            # 6. Clean up leftover empty brackets from emoji removal: [ text] → [text]
            clean_code = re.sub(r'\[\s+', '[', clean_code)
            clean_code = re.sub(r'\s+\]', ']', clean_code)
            # Debug expander
            n_lines = clean_code.count('\n') + 1
            with st.expander(f"Debug Mermaid v4 — {n_lines} lignes", expanded=False):
                st.code(clean_code, language="text")
            # Inject theme via %%{init:...}%% directive at top of mermaid code
            # (streamlit-mermaid doesn't accept a config param)
            theme_directive = (
                '%%{init: {"theme": "base", "themeVariables": {'
                '"primaryColor": "#1e3a5f", "primaryTextColor": "#e2e8f0", '
                '"primaryBorderColor": "#3b82f6", '
                '"secondaryColor": "#4c1d95", "secondaryTextColor": "#e2e8f0", '
                '"secondaryBorderColor": "#8b5cf6", '
                '"tertiaryColor": "#164e3d", "tertiaryTextColor": "#e2e8f0", '
                '"tertiaryBorderColor": "#34d399", '
                '"lineColor": "#94a3b8", "textColor": "#e2e8f0", '
                '"background": "#0f172a", "mainBkg": "#1e3a5f", '
                '"nodeBorder": "#3b82f6", '
                '"fontFamily": "Inter, system-ui, sans-serif", '
                '"fontSize": "14px", '
                '"edgeLabelBackground": "#1e293b", '
                '"clusterBkg": "#1e293b", "clusterBorder": "#475569", '
                '"noteBkgColor": "#1e293b", "noteTextColor": "#e2e8f0", '
                '"noteBorderColor": "#f59e0b"'
                '}, "flowchart": {"curve": "basis", "padding": 16, '
                '"nodeSpacing": 50, "rankSpacing": 60, "htmlLabels": true}}}%%'
            )
            themed_code = theme_directive + '\n' + clean_code
            try:
                stmd.st_mermaid(themed_code, height="auto")
            except Exception as e:
                st.error(f"Erreur rendu Mermaid : {e}")
                st.code(clean_code, language="text")
        else:
            st.markdown(seg_content, unsafe_allow_html=True)


# =====================================================
# POINT 2 : boutons copier / sauvegarder
# =====================================================
def render_action_buttons(answer_text, key_suffix=""):
    import base64 as _b64
    # Encode the FULL markdown text as base64 to preserve it entirely (no truncation, no escaping issues)
    b64 = _b64.b64encode(answer_text.encode("utf-8")).decode("ascii")
    bid = f"btn-{key_suffix}"
    st.html(f"""
    <script>var _palim_b64_{key_suffix.replace('-','_')}="{b64}";</script>
    <button id="{bid}" style="background:none;border:1px solid #64748b;border-radius:6px;padding:5px 16px;cursor:pointer;font-size:0.82rem;color:#94a3b8;margin-right:6px;transition:all 0.15s;font-family:Inter,sans-serif" onmouseover="this.style.background='#334155';this.style.color='#e2e8f0'" onmouseout="this.style.background='none';this.style.color='#94a3b8'" onclick="
        (function(){{
            var b64=_palim_b64_{key_suffix.replace('-','_')};
            var txt=decodeURIComponent(Array.prototype.map.call(atob(b64),function(c){{return'%'+('00'+c.charCodeAt(0).toString(16)).slice(-2);}}).join(''));
            navigator.clipboard.writeText(txt).then(function(){{
                document.getElementById('{bid}').textContent='\\u2705 Copié !';
                setTimeout(function(){{document.getElementById('{bid}').textContent='\\ud83d\\udccb Copier';}},2000);
            }},function(){{
                var ta=document.createElement('textarea');ta.value=txt;ta.style.position='fixed';ta.style.left='-9999px';
                document.body.appendChild(ta);ta.select();document.execCommand('copy');document.body.removeChild(ta);
                document.getElementById('{bid}').textContent='\\u2705 Copié !';
                setTimeout(function(){{document.getElementById('{bid}').textContent='\\ud83d\\udccb Copier';}},2000);
            }});
        }})();
    ">📋 Copier</button>
    """)


def render_sources(results, display_k=TOP_K_DISPLAY, key_prefix="", offset=0,
                   title="##### 📎 Sources utilisées", anchor_prefix="",
                   collapsed=True):
    """Affiche les sources en expanders. anchor_prefix rend les ancres uniques par message.
    collapsed=True wraps the entire section in an st.expander."""
    pfx = f"{anchor_prefix}-" if anchor_prefix else ""

    def _render_source_list(results_slice, offset_val):
        for i, result in enumerate(results_slice):
            rank = offset_val + i
            num = rank + 1
            chunk_id, copro, source, filename, doc_type, text, vec_sim, bm25_score, rrf_score, *_ = result
            sim_color = "#48bb78" if rank < 5 else "#ecc94b" if rank < 15 else "#fc8181"
            boost_ind = " +📝" if bm25_score > 0.1 else ""

            with st.expander(f"Source {num} — {filename}  ({doc_type}){boost_ind}"):
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.caption(f"📁 **Copropriété :** {copro}")
                    st.caption(f"📄 **Fichier :** {source}")
                    st.caption(f"📊 **Scores :** vec={vec_sim:.2f}  bm25={bm25_score:.3f}  rrf={rrf_score:.4f}")
                with c2:
                    st.markdown(f"""
                    <div style="text-align:center">
                        <div style="font-size:1.4rem;font-weight:700;color:{sim_color}">#{num}</div>
                        <div style="font-size:0.7rem;color:#a0aec0">rang RRF</div>
                    </div>
                    """, unsafe_allow_html=True)
                _ = st.markdown(f'<div id="source-{pfx}{num}"></div>', unsafe_allow_html=True)
                st.markdown("---")
                st.text(text[:2000] + ("..." if len(text) > 2000 else ""))

    if collapsed:
        n_sources = min(display_k, len(results))
        with st.expander(f"📎 Sources utilisées ({n_sources} sources)", expanded=False):
            _render_source_list(results[:display_k], offset)
    else:
        st.markdown(title)
        _render_source_list(results[:display_k], offset)
    # Legacy return for callers that don't use collapsed
    return


# =====================================================
# SIDEBAR
# =====================================================
with st.sidebar:
    st.markdown("## 🏢 PALIM")
    st.markdown("---")

    # Clickable question titles — scroll to the corresponding message
    _user_questions = [
        (idx, msg["content"])
        for idx, msg in enumerate(st.session_state.chat_history)
        if msg["role"] == "user"
    ]
    if _user_questions:
        st.markdown("##### 💬 Questions posées")
        for _qi, (_msg_idx, _q) in enumerate(_user_questions):
            _truncated = (_q[:50] + "...") if len(_q) > 50 else _q
            _anchor_id = f"q-anchor-{_msg_idx}"
            # Use st.html so onclick JS executes in the parent frame (not blocked by sandbox)
            st.html(
                f'<div onclick="'
                f"window.parent.document.getElementById('{_anchor_id}')?.scrollIntoView({{behavior:'smooth',block:'start'}})"
                f'" style="display:block;font-size:0.82rem;padding:4px 8px;margin:2px 0;color:#a0aec0;'
                f'text-decoration:none;border-radius:6px;transition:background 0.15s;cursor:pointer;'
                f'font-family:Inter,sans-serif;" '
                f'onmouseover="this.style.background=\'#1e293b\'" onmouseout="this.style.background=\'none\'">'
                f'<span style="color:#f59e0b;font-weight:500;">{_qi+1}.</span> {_truncated}</div>'
            )
        st.markdown("---")

    copros = get_copros()
    total = get_total_chunks()
    _ = st.markdown(f"""
    <div class="stat-card"><div class="number">{total:,}</div><div class="label">chunks indexés</div></div>
    <div class="stat-card"><div class="number">{len(copros)}</div><div class="label">copropriété(s)</div></div>
    """, unsafe_allow_html=True)

    st.markdown("---")
    copro_names = ["Toutes les copropriétés"] + [c[0] for c in copros]
    default_idx = 1 if len(copros) == 1 else 0
    selected_copro = st.selectbox("📁 Filtrer par copropriété", copro_names, index=default_idx)
    if selected_copro != "Toutes les copropriétés":
        copro_count = next((c[1] for c in copros if c[0] == selected_copro), 0)
        st.caption(f"{copro_count} chunks disponibles")

    st.markdown("---")
    demo_mode = st.toggle("⚡ Mode Démo", value=False,
                           help="Haiku 4.5 + streaming + chunks réduits. ~15-20s au lieu de ~90s.")
    if demo_mode:
        st.caption("⚡ Haiku 4.5 + streaming")
        if DEMO_3D_LINKS:
            st.caption(f"🏠 {len(DEMO_3D_LINKS)} lien(s) 3D actif(s)")
        else:
            st.caption("⚠️ Aucun lien 3D (fichier absent ou vide)")

    with st.expander("⚙️ Paramètres avancés"):
        auto_strategy = st.checkbox("Stratégie de retrieval automatique", value=True,
                                     help="Détecte auto inventaire/ciblé, ajuste chunks et boost.")
        if not auto_strategy:
            chunks_per_source = st.slider("Max chunks par document source", 1, 10, MAX_CHUNKS_PER_SOURCE)
            max_chunks = st.slider("Chunks analysés par l'IA", 15, 100, MAX_CHUNKS_LLM_DEFAULT)
        else:
            chunks_per_source = None
            max_chunks = None
        display_k = st.slider("Sources affichées", 5, 30, TOP_K_DISPLAY)
        sim_threshold = st.slider("Seuil de similarité", 0.0, 1.0, SIMILARITY_THRESHOLD, 0.05)

    st.markdown("---")

    # POINT 3 : bouton nouvelle conversation
    if st.button("🗑️ Nouvelle conversation", use_container_width=True):
        st.session_state.chat_history = []
        st.rerun()

    st.markdown("---")
    st.markdown("""
    **Exemples de questions :**
    - Quel est le règlement de copropriété ?
    - Quels travaux ont été votés ?
    - Analyse des charges de 2022 à 2025
    - Que disent les diagnostics techniques ?
    """)


# =====================================================
# ZONE PRINCIPALE — Multi-turn conversationnel
# =====================================================

# Header avec logo client (conditionnel)
_logo_html = ""
if CLIENT_LOGO_FILE and os.path.exists(CLIENT_LOGO_FILE):
    import base64 as _b64
    with open(CLIENT_LOGO_FILE, "rb") as _lf:
        _logo_b64 = _b64.b64encode(_lf.read()).decode("ascii")
    _ext = CLIENT_LOGO_FILE.rsplit(".", 1)[-1].lower()
    _mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml"}.get(_ext, "image/png")
    _logo_html = (
        f'<img src="data:{_mime};base64,{_logo_b64}" '
        f'style="height:60px;max-width:180px;object-fit:contain;" />'
    )

_ = st.markdown(f"""
<div class="main-header" style="display:flex;align-items:center;justify-content:space-between;">
    <div>
        <h1 style="color:white;margin:0 0 0.3rem 0;font-size:1.8rem;font-weight:700;">🏢 PALIM</h1>
        <p style="color:#a0aec0;margin:0;font-size:0.95rem;">Interrogez notre IA sur vos archives de copropriété &amp; prolongez votre conversation pour affiner vos études</p>
    </div>
    {_logo_html}
</div>
""", unsafe_allow_html=True)

# ── Saisie utilisateur (barre fixe en bas) ──
user_input = st.chat_input("Posez votre question sur les archives de copropriété…")

# ── Afficher l'historique — toutes les réponses restent consultables et cliquables ──
# Fix duplicate question: if user_input is set, the last user message was just appended
# and will be re-displayed by the user_input block below — so skip it here.
_skip_last_user = False
if user_input and st.session_state.chat_history:
    _last = st.session_state.chat_history[-1]
    if _last["role"] == "user" and _last["content"] == user_input:
        _skip_last_user = True

for msg_idx, msg in enumerate(st.session_state.chat_history):
    # Skip the last user message if it was just submitted (avoids duplicate display)
    if _skip_last_user and msg_idx == len(st.session_state.chat_history) - 1 and msg["role"] == "user":
        continue

    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            # Anchor for sidebar navigation
            st.markdown(f'<div id="q-anchor-{msg_idx}"></div>', unsafe_allow_html=True)
            st.markdown(msg["content"])
        else:
            n_disp = msg.get("n_displayed", 0)
            apfx = f"m{msg_idx}"
            render_answer_segments(linkify_sources(msg["content"], n_disp, anchor_prefix=apfx))

            # Render ALL sources in a single collapsed expander
            if msg.get("sources"):
                render_action_buttons(msg["content"], key_suffix=f"h-{msg_idx}")
                all_msg_sources = msg["sources"][:TOP_K_EXTRA]
                render_sources(all_msg_sources, display_k=len(all_msg_sources),
                               key_prefix=f"h-{msg_idx}", anchor_prefix=apfx,
                               collapsed=True)
            else:
                sc = msg.get("source_count", 0)
                if sc:
                    st.caption(f"📎 {sc} sources analysées")

if user_input:
    # Ajouter à l'historique et afficher
    st.session_state.chat_history.append({"role": "user", "content": user_input})
    _new_msg_idx = len(st.session_state.chat_history) - 1
    with st.chat_message("user"):
        st.markdown(f'<div id="q-anchor-{_new_msg_idx}"></div>', unsafe_allow_html=True)
        st.markdown(user_input)

    # ── Paramètres ──
    copro_filter = selected_copro if selected_copro != "Toutes les copropriétés" else None
    DISPLAY_K_ACTUAL = display_k if 'display_k' in dir() else TOP_K_DISPLAY
    SIM_ACTUAL = sim_threshold if 'sim_threshold' in dir() else SIMILARITY_THRESHOLD
    _auto = auto_strategy if 'auto_strategy' in dir() else True
    _demo = demo_mode if 'demo_mode' in dir() else False

    # ── Stratégie via Haiku (v4) ──
    prev_queries = [h["content"] for h in st.session_state.chat_history[:-1] if h["role"] == "user"]
    prev_query = prev_queries[-1] if prev_queries else None

    if _auto:
        CPS_ACTUAL, DTB_ACTUAL, MCL_ACTUAL, strategy_label, prefilter, doc_type_hint, was_expanded, expanded_query = detect_retrieval_strategy(
            user_input, demo_mode=_demo, prev_query=prev_query
        )
        query_for_retrieval = expanded_query if was_expanded and expanded_query else user_input
    else:
        CPS_ACTUAL = chunks_per_source if chunks_per_source else MAX_CHUNKS_PER_SOURCE
        MCL_ACTUAL = max_chunks if max_chunks else MAX_CHUNKS_LLM_DEFAULT
        DTB_ACTUAL = 0.01
        strategy_label = f"Manuel ({CPS_ACTUAL}/source, {MCL_ACTUAL} chunks)"
        prefilter = None
        doc_type_hint = None
        was_expanded = False
        query_for_retrieval = user_input

    active_model = LLM_MODEL_FAST if _demo else LLM_MODEL
    model_label = "Haiku 4.5 ⚡" if _demo else "Sonnet 4.6"

    # ── Recherche (Phase 1b : décomposition temporelle si inventaire) ──
    _strategie = "inventaire" if "Inventaire" in strategy_label else (
        "cible" if "Ciblé" in strategy_label else "equilibre"
    )
    with st.spinner("⏳ Recherche dans les archives..."):
        results, _, prefilter_used = search_decomposed(
            query_for_retrieval, copro_filter,
            max_chunks=MCL_ACTUAL, sim_threshold=SIM_ACTUAL,
            chunks_per_source=CPS_ACTUAL, doc_type_boost=DTB_ACTUAL,
            prefilter=prefilter, doc_type_hint=doc_type_hint,
            strategie=_strategie,
        )

    # ── Réponse ──
    with st.chat_message("assistant"):
        if not results:
            answer = "Aucun résultat trouvé. Essayez de reformuler votre question."
            st.warning(f"❌ {answer}")
            st.session_state.chat_history.append({
                "role": "assistant", "content": answer,
                "source_count": 0, "n_displayed": 0,
            })
        else:
            unique_sources = len(set(r[2] for r in results))

            # Badge suivi de conversation
            if was_expanded:
                _ = st.markdown(
                    '<span class="followup-badge">🔗 Suite de la conversation</span>',
                    unsafe_allow_html=True,
                )

            # Métadonnées compactes
            pf_tag = " · 📋 Pré-filtré" if prefilter_used else ""
            if _demo:
                st.caption(f"⚡ {len(results)} extraits · {unique_sources} docs · {model_label}{pf_tag}")
            else:
                st.caption(f"{strategy_label} · {len(results)} chunks · {unique_sources} docs · {model_label}{pf_tag}")

            # Visite 3D (démo) — match uniquement sur le prompt utilisateur
            if _demo and DEMO_3D_LINKS:
                for kw, url in DEMO_3D_LINKS.items():
                    if kw.lower() in user_input.lower():
                        _ = st.markdown(
                            f'<div style="background:linear-gradient(135deg,#1a365d,#2a4a7f);'
                            f'padding:0.7rem 1.2rem;border-radius:10px;margin-bottom:0.5rem">'
                            f'<span style="color:#e2e8f0"><strong>{kw}</strong> — </span>'
                            f'<a href="{url}" target="_blank" '
                            f'style="color:#63b3ed;text-decoration:underline;font-weight:600">'
                            f'visite 3D ↗</a></div>',
                            unsafe_allow_html=True,
                        )

            # Historique LLM (tours précédents, sans le dernier user qu'on vient d'ajouter)
            history_for_llm = st.session_state.chat_history[:-1]
            n_displayed = min(len(results), TOP_K_EXTRA)
            # Préfixe d'ancre unique pour ce message (basé sur sa future position dans l'historique)
            cur_apfx = f"m{len(st.session_state.chat_history)}"

            # Génération
            if _demo:
                answer_placeholder = st.empty()
                answer = generate_answer_stream(
                    user_input, results, doc_type_hint,
                    active_model, answer_placeholder, chat_history=history_for_llm,
                )
                answer_placeholder.empty()
                render_answer_segments(linkify_sources(answer, n_displayed, anchor_prefix=cur_apfx))
            else:
                with st.spinner("🤖 Génération de la réponse…"):
                    answer = generate_answer(
                        user_input, results, doc_type_hint,
                        model_id=active_model, chat_history=history_for_llm,
                    )
                render_answer_segments(linkify_sources(answer, n_displayed, anchor_prefix=cur_apfx))

            # Boutons copier / sauvegarder (POINT 2)
            render_action_buttons(answer, key_suffix="current")

            # Sources unifiées (toutes les sources dans un seul expander replié)
            all_sources = results[:TOP_K_EXTRA]
            if all_sources:
                render_sources(all_sources, display_k=len(all_sources),
                               key_prefix="current", anchor_prefix=cur_apfx,
                               collapsed=True)

            # ── Sauvegarder dans l'historique ──
            # Keep sources for all messages so they can be displayed in collapsed expanders
            st.session_state.chat_history.append({
                "role": "assistant",
                "content": answer,
                "sources": results[:TOP_K_EXTRA],
                "n_displayed": n_displayed,
                "source_count": len(results),
                "meta": {
                    "strategy": strategy_label, "model": model_label,
                    "doc_type_hint": doc_type_hint,
                    "expanded": was_expanded,
                    "prefilter_used": prefilter_used,
                },
            })
