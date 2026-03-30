"""
ÉTAPE 7 — Interface de requête RAG (Streamlit) — v4 Haiku Strategy Router
Pipeline : Haiku strategy detection → Pré-filtrage document → Vector + BM25 → RRF fusion → Source diversity → FlashRank rerank → Claude
Lance : streamlit run 07_query_rag_ui.py
Prérequis : pip install flashrank --break-system-packages

Changelog v4 :
  1. detect_strategy_haiku() : Haiku classifie la requête (inventaire/ciblé/équilibré) + extrait les filtres structurels en un seul appel (~300ms)
  2. Suppression des listes de mots-clés pour détection stratégie et pré-filtrage (remplacées par LLM)
  3. Fallback automatique sur mode équilibré si Haiku échoue

Changelog v3 :
  1. Pré-filtrage document via table documents (année, sous-type, statut)
  2. search_chunks() accepte prefilter, CTE conditionnel, fallback si 0 ou >50 docs
  3. Badge visuel "📋 Pré-filtré" dans les métadonnées de réponse

Changelog v2 :
  1. Sidebar lisible en mobile (labels blancs)
  2. Boutons copier / sauvegarder la réponse
  3. Multi-turn UX (st.chat_input / st.chat_message)
  4. Multi-turn backend (historique injecté dans les messages LLM, max 3 tours)
  5. Seuil inventaire élargi (détection temporelle >2 ans → inventaire 80 chunks)
  6. Boost RCP (quota minimum garanti dans la diversité par source)
  7. Query expansion pour questions de suivi courtes
  8. Concision du prompt (instruction explicite + max_tokens réduits)
  9. Adaptation prompt multi-turn (ne pas répéter les infos déjà données)
"""
import json
import re
import os
import boto3
import psycopg2
import streamlit as st
from flashrank import Ranker, RerankRequest

# =====================================================
# CONFIGURATION
# =====================================================
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = "SmarterRAG99!"
AWS_REGION = "eu-west-1"

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
RERANK_CANDIDATES = 120
RCP_MIN_SLOTS = 3             # POINT 6 : quota minimum RCP
MIN_CHUNK_CHARS = 500         # Ignorer les chunks trop courts (signatures, fragments OCR)
RERANK_RRF_WEIGHT = 0.4      # Poids du score RRF dans le mix hybride RRF×FlashRank (0=FlashRank pur, 1=RRF pur)

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
    page_title="Building Copilot",
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
        background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px;
        padding: 1.5rem 2rem; margin: 1rem 0; line-height: 1.7; color: #1a202c;
    }
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
    )
    conn.autocommit = True
    st.session_state["_db_conn"] = conn
    return conn

@st.cache_resource
def get_bedrock_client():
    from botocore.config import Config
    return boto3.client(
        "bedrock-runtime", region_name=AWS_REGION,
        config=Config(read_timeout=300, retries={"max_attempts": 3})
    )

@st.cache_resource
def get_reranker():
    import tempfile
    cache = os.path.join(tempfile.gettempdir(), "flashrank")
    return Ranker(model_name="ms-marco-MultiBERT-L-12", cache_dir=cache)

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
                    pf_clauses.append("code_ncg = %s")
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
        # Pré-filtrage actif → seuil minimal comme garde-fou (#18)
        sim_threshold = 0.05

    with conn.cursor() as cur:
        where_clauses, params_before = [], []
        # Exclure les chunks trop courts (signatures, pieds de page, fragments OCR)
        where_clauses.append(f"c.nb_caracteres >= {MIN_CHUNK_CHARS}")

        if copropriete:
            where_clauses.append("c.code_ncg = %s")
            params_before.append(copropriete)

        if prefilter_active and prefilter_files:
            placeholders = ",".join(["%s"] * len(prefilter_files))
            where_clauses.append(f"c.source_file IN ({placeholders})")
            params_before.extend(prefilter_files)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        doc_type_for_boost = doc_type_hint if doc_type_hint else "__NONE__"

        # Quand le pré-filtrage est actif, ouvrir large la diversité SQL
        # pour que FlashRank ait des candidats de tout le document
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

    # ── D : Bypass FlashRank quand le pré-filtrage est actif ──
    # Le pré-filtrage a déjà sélectionné les bons documents ; le RRF suffit.
    # FlashRank pénalise les chunks OCR bruités (constats manuscrits, scans dégradés).
    if prefilter_active:
        # Cap dynamique par source sur l'ordre RRF (pas de rerank)
        from collections import defaultdict
        by_source = defaultdict(list)
        for r in deduped:
            by_source[r[2]].append(r)
        capped = []
        for sf, chunks_list in by_source.items():
            capped.extend(chunks_list[:chunks_per_source])
        capped.sort(key=lambda r: float(r[8]), reverse=True)  # r[8] = rrf_score
        deduped = capped

    elif len(deduped) > 1:
        # ── A : Injection de métadonnées propres dans le texte FlashRank ──
        # Le cross-encoder voit un en-tête structuré avant le texte OCR bruité,
        # ce qui donne un signal fort même quand le contenu est illisible.
        def _flashrank_text(r):
            # r = (chunk_id, copro, source_file, nom_fichier, doc_type, text, vec_sim, bm25, rrf, chunk_idx)
            doc_type = r[4] or ""
            nom = r[3] or ""
            # Nettoyer le nom : retirer l'extension et le chemin, garder le nom lisible
            nom_clean = os.path.splitext(os.path.basename(nom))[0].replace("_", " ").replace("-", " ")
            header = f"[{doc_type}] {nom_clean}"
            return f"{header}\n{r[5][:1900]}"

        reranker = get_reranker()
        passages = [{"id": i, "text": _flashrank_text(r)} for i, r in enumerate(deduped)]
        reranked = reranker.rerank(RerankRequest(query=query, passages=passages))

        # ── B : Score hybride RRF × FlashRank ──
        # Normaliser les deux scores et les combiner pour éviter les chutes brutales
        # des chunks à bon score RRF mais texte OCR bruité.
        rrf_scores = [float(r[8]) for r in deduped]  # r[8] = rrf_score (Decimal → float)
        rrf_max = max(rrf_scores) if rrf_scores else 1.0
        rrf_min = min(rrf_scores) if rrf_scores else 0.0
        rrf_range = rrf_max - rrf_min if rrf_max > rrf_min else 1.0

        # FlashRank retourne les items triés par score décroissant
        # → le rang dans reranked donne le score normalisé
        n = len(reranked)
        flashrank_norm = {}  # id → score normalisé [0, 1]
        for rank, item in enumerate(reranked):
            flashrank_norm[item["id"]] = 1.0 - (rank / max(n - 1, 1))

        alpha = RERANK_RRF_WEIGHT
        scored = []
        for i, r in enumerate(deduped):
            rrf_norm = (float(r[8]) - rrf_min) / rrf_range
            fr_norm = flashrank_norm.get(i, 0.0)
            hybrid = alpha * rrf_norm + (1.0 - alpha) * fr_norm
            scored.append((hybrid, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        deduped = [r for _, r in scored]

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

FILTRAGE DES RÉSOLUTIONS D'AG (très important) :
- Sauf demande explicite de l'utilisateur, EXCLUS systématiquement de tes tableaux de résolutions :
  * Désignation du président de séance, du bureau, des scrutateurs et secrétaires
  * Rapport du conseil syndical (présenté sans vote)
  * Élection des membres du conseil syndical (sauf si la question porte spécifiquement sur la composition du CS)
  * Questions diverses / vie de l'immeuble (sauf si elles contiennent une décision chiffrée)
- Concentre-toi sur les résolutions de FOND : budgets, travaux, contrats, mandats syndic, procédures, autorisations de travaux privatifs."""

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

    # ── POINT 8 : max_tokens adapté à la stratégie ──
    # Inventaire multi-années : 9 AG × 15 résolutions × tableaux = besoin de 8K+ tokens
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


def linkify_sources(text, max_source_num, anchor_prefix=""):
    """Transforme les 'Source N' en liens cliquables et enveloppe dans un div HTML
    pour forcer Streamlit à rendre le HTML (compatibilité versions récentes)."""
    pfx = f"{anchor_prefix}-" if anchor_prefix else ""

    def make_link(num):
        if 1 <= num <= max_source_num:
            return (f'<a href="#source-{pfx}{num}" '
                    f'style="color:#3182ce;text-decoration:underline;font-weight:500">'
                    f'Source {num}</a>')
        return f'Source {num}'

    # D'abord, expandre "Source 4, 8, 10, 20" → "Source 4, Source 8, Source 10, Source 20"
    def expand_source_list(match):
        nums = re.findall(r'\d+', match.group(0))
        return ", ".join(f"Source {n}" for n in nums)

    linkified = re.sub(
        r'(?<!\w)Sources?\s+(\d+(?:\s*,\s*\d+)+)(?!\w)',
        expand_source_list, text
    )

    # Ensuite, linkifier chaque "Source N" individuel
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
    # Headers markdown → bold
    linkified = re.sub(r'^#{1,4}\s+(.+)$', r'<strong>\1</strong>', linkified, flags=re.MULTILINE)
    # Listes à puces
    linkified = re.sub(r'^[-•]\s+', '• ', linkified, flags=re.MULTILINE)
    # Retours à la ligne
    linkified = linkified.replace('\n\n', '<br><br>').replace('\n', '<br>')

    return f'<div class="answer-card">{linkified}</div>'


# =====================================================
# POINT 2 : boutons copier / sauvegarder
# =====================================================
def render_action_buttons(answer_text, key_suffix=""):
    import base64
    # Encoder en base64 pour éviter tout problème d'échappement HTML/JS
    b64 = base64.b64encode(answer_text.encode("utf-8")).decode("ascii")
    bid = f"btn-{key_suffix}"
    _ = st.markdown(f"""
    <button id="{bid}" class="action-btn" onclick="
        var t=atob('{b64}');
        navigator.clipboard.writeText(t).then(function(){{
            document.getElementById('{bid}').textContent='✅ Copié !';
            setTimeout(function(){{document.getElementById('{bid}').textContent='📋 Copier';}},2000);
        }});
    ">📋 Copier</button>
    """, unsafe_allow_html=True)


def render_sources(results, display_k=TOP_K_DISPLAY, key_prefix="", offset=0,
                   title="##### 📎 Sources utilisées", anchor_prefix=""):
    """Affiche les sources en expanders. anchor_prefix rend les ancres uniques par message."""
    pfx = f"{anchor_prefix}-" if anchor_prefix else ""
    st.markdown(title)
    for i, result in enumerate(results[:display_k]):
        rank = offset + i
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
                    <div style="font-size:0.7rem;color:#a0aec0">rang reranké</div>
                </div>
                """, unsafe_allow_html=True)
            _ = st.markdown(f'<div id="source-{pfx}{num}"></div>', unsafe_allow_html=True)
            st.markdown("---")
            st.text(text[:2000] + ("..." if len(text) > 2000 else ""))


# =====================================================
# SIDEBAR
# =====================================================
with st.sidebar:
    st.markdown("## 🏢 Building Copilot")
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
        <h1 style="color:white;margin:0 0 0.3rem 0;font-size:1.8rem;font-weight:700;">🏢 Building Copilot</h1>
        <p style="color:#a0aec0;margin:0;font-size:0.95rem;">Posez vos questions sur les archives de copropriété — réponses sourcées par IA</p>
    </div>
    {_logo_html}
</div>
""", unsafe_allow_html=True)

# ── Saisie utilisateur (barre fixe en bas) ──
user_input = st.chat_input("Posez votre question sur les archives de copropriété…")

# ── Afficher l'historique ──
# Si une nouvelle requête arrive, on allège l'affichage du dernier assistant
_processing_new = bool(user_input)

for msg_idx, msg in enumerate(st.session_state.chat_history):
    is_last_assistant = (
        msg["role"] == "assistant"
        and msg_idx == len(st.session_state.chat_history) - 1
    )

    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["content"])
        elif is_last_assistant and _processing_new:
            # Nouvelle requête en cours → affichage compact du dernier assistant
            sc = msg.get("source_count", 0)
            st.caption(f"📎 Réponse précédente ({sc} sources analysées)")
        else:
            n_disp = msg.get("n_displayed", 0)
            apfx = f"m{msg_idx}"
            st.html(linkify_sources(msg["content"], n_disp, anchor_prefix=apfx))

            if is_last_assistant:
                render_action_buttons(msg["content"], key_suffix=f"h-{msg_idx}")
                if msg.get("sources"):
                    main_sources = msg["sources"][:TOP_K_DISPLAY]
                    render_sources(main_sources, TOP_K_DISPLAY, key_prefix=f"h-{msg_idx}", anchor_prefix=apfx)
                    extra_sources = msg["sources"][TOP_K_DISPLAY:TOP_K_EXTRA]
                    if extra_sources:
                        with st.expander(f"📂 Sources supplémentaires ({len(extra_sources)} sources)"):
                            render_sources(
                                extra_sources, display_k=len(extra_sources),
                                key_prefix=f"hx-{msg_idx}", offset=TOP_K_DISPLAY,
                                title="##### 📂 Sources supplémentaires",
                                anchor_prefix=apfx,
                            )
            else:
                sc = msg.get("source_count", 0)
                if sc:
                    st.caption(f"📎 {sc} sources analysées")

if user_input:
    # Ajouter à l'historique et afficher
    st.session_state.chat_history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
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
                st.html(linkify_sources(answer, n_displayed, anchor_prefix=cur_apfx))
            else:
                with st.spinner("🤖 Génération de la réponse…"):
                    answer = generate_answer(
                        user_input, results, doc_type_hint,
                        model_id=active_model, chat_history=history_for_llm,
                    )
                st.html(linkify_sources(answer, n_displayed, anchor_prefix=cur_apfx))

            # Boutons copier / sauvegarder (POINT 2)
            render_action_buttons(answer, key_suffix="current")

            # Sources principales (top 20)
            render_sources(results, DISPLAY_K_ACTUAL, key_prefix="current", anchor_prefix=cur_apfx)

            # Sources supplémentaires (chunks 21 à 50) — repliées par défaut
            extra_results = results[DISPLAY_K_ACTUAL:TOP_K_EXTRA]
            if extra_results:
                with st.expander(f"📂 Sources supplémentaires ({len(extra_results)} sources de rang {DISPLAY_K_ACTUAL+1} à {DISPLAY_K_ACTUAL+len(extra_results)})"):
                    render_sources(
                        extra_results, display_k=len(extra_results),
                        key_prefix="extra-current", offset=DISPLAY_K_ACTUAL,
                        title="##### 📂 Sources supplémentaires",
                        anchor_prefix=cur_apfx,
                    )

            # ── Sauvegarder dans l'historique ──
            # Libérer les sources des anciens messages
            for old_msg in st.session_state.chat_history:
                if old_msg["role"] == "assistant" and "sources" in old_msg:
                    old_msg["source_count"] = len(old_msg["sources"])
                    del old_msg["sources"]

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
