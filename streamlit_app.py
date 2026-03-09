"""
ÉTAPE 7 — Interface de requête RAG (Streamlit)
Pipeline : Vector + BM25 → RRF fusion → Source diversity → FlashRank rerank → Claude
Lance : streamlit run 07_query_rag_ui.py
Prérequis : pip install flashrank --break-system-packages
"""
import json
import re
import os
import boto3
import psycopg2
import streamlit as st
from flashrank import Ranker, RerankRequest

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

EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
LLM_MODEL = "eu.anthropic.claude-sonnet-4-6"
LLM_MODEL_FAST = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"  # Mode démo — 5-10x plus rapide

MAX_CHUNKS_LLM_DEFAULT = 50   # Défaut pour Équilibré et Ciblé
MAX_CHUNKS_LLM_BROAD = 80    # Pour requêtes inventaire (couverture max)
TOP_K_DISPLAY = 15            # Chunks affichés dans les sources de l'UI
MAX_CHUNKS_PER_SOURCE = 3     # Défaut (override par stratégie auto)
SIMILARITY_THRESHOLD = 0.15
THEME_BOOST = 0.05
RRF_K = 60                   # Constante RRF (standard = 60)
RERANK_CANDIDATES = 120      # Candidats envoyés au reranker (avant filtrage final)

# Types de documents considérés comme sources PRIMAIRES (événements distincts)
PRIMARY_DOC_TYPES = {"SINISTRE", "ENTRETIEN", "COMPTABILITE", "DEVIS", "FACTURE"}

# Liens 3D pour la démo — fichier texte avec format "MOT_CLE : URL" (une paire par ligne)
DEMO_3D_LINKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "URL_SP_demo.txt")
DEMO_3D_LINKS = {}


def load_demo_urls(filepath):
    """
    Lit le fichier de mapping mot-clé → URL de démo 3D.
    Format attendu par ligne :  MOT_CLE : https://...
    Les lignes vides et celles commençant par # sont ignorées.
    Retourne un dict { "MOT_CLE": "url" } (clés en MAJUSCULES).
    """
    url_map = {}
    if not os.path.exists(filepath):
        print(f"⚠️ Fichier 3D non trouvé : {filepath}")
        return url_map

    with open(filepath, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if " : " in line:
                keyword, url = line.split(" : ", 1)
                keyword = keyword.strip().upper()
                url = url.strip()
                if url:
                    url_map[keyword] = url

    if url_map:
        print(f"✅ {len(url_map)} lien(s) 3D chargé(s) : {', '.join(url_map.keys())}")
    else:
        print(f"⚠️ Fichier 3D vide ou mal formaté : {filepath}")

    return url_map


DEMO_3D_LINKS = load_demo_urls(DEMO_3D_LINKS_FILE)

# =====================================================
# Thèmes métier
# =====================================================
THEMES_KEYWORDS = {
    "syndic_obligations": ["syndic", "obligation", "mission", "mandat", "gestionnaire"],
    "parties_communes": ["parties communes", "commun", "hall", "toiture", "façade", "escalier", "ascenseur", "jardin"],
    "parties_privatives": ["privatif", "privative", "lot", "appartement", "cave", "box", "tantième"],
    "charges_generales": ["charges générales", "entretien", "conservation", "administration", "budget"],
    "charges_speciales": ["charges spéciales", "ascenseur", "chauffage", "utilité", "répartition"],
    "assemblee_generale": ["assemblée générale", "ag", "vote", "majorité", "résolution", "convocation"],
    "conseil_syndical": ["conseil syndical", "président du conseil"],
    "travaux": ["travaux", "ravalement", "rénovation", "devis", "chantier"],
    "mutations_ventes": ["vente", "mutation", "état daté", "notaire", "acquéreur"],
    "assurance_sinistres": ["assurance", "sinistre", "dégât des eaux", "incendie"],
    "contentieux": ["contentieux", "impayé", "mise en demeure", "recouvrement", "huissier"],
    "diagnostics_techniques": ["diagnostic", "dpe", "amiante", "plomb", "termite"],
    "comptabilite": ["budget", "comptabilité", "appel de fonds", "trésorerie", "bilan"],
    "reglement_interieur": ["règlement intérieur", "nuisance", "bruit", "usage", "destination"],
    "personnel_immeuble": ["gardien", "concierge", "employé", "loge"],
}

THEME_LABELS = {
    "syndic_obligations": "🏢 Obligations du syndic",
    "parties_communes": "🏗️ Parties communes",
    "parties_privatives": "🏠 Parties privatives",
    "charges_generales": "💰 Charges générales",
    "charges_speciales": "📊 Charges spéciales",
    "assemblee_generale": "🗳️ Assemblée générale",
    "conseil_syndical": "👥 Conseil syndical",
    "travaux": "🔧 Travaux",
    "mutations_ventes": "📝 Mutations / ventes",
    "assurance_sinistres": "🛡️ Assurance & sinistres",
    "contentieux": "⚖️ Contentieux",
    "diagnostics_techniques": "🔍 Diagnostics techniques",
    "comptabilite": "📒 Comptabilité",
    "reglement_interieur": "📜 Règlement intérieur",
    "personnel_immeuble": "👷 Personnel d'immeuble",
}

DOC_TYPE_KEYWORDS = {
    "RCP": ["règlement de copropriété", "reglement de copropriete", "rcp", "règlement", "reglement"],
    "PV_AG": ["pv", "procès-verbal", "proces-verbal", "assemblée générale", "ag"],
    "CONTRAT": ["contrat", "mandat"],
    "BUDGET": ["budget", "appel de fonds"],
    "DIAGNOSTIC": ["diagnostic", "dpe", "amiante"],
    "ENTRETIEN": ["entretien", "maintenance", "carnet", "équipement", "extincteur", "désenfumage"],
    "SINISTRE": ["sinistre", "anomalie", "constat", "expertise", "dégât", "désordre"],
    "COMPTABILITE": ["annexe comptable", "grand livre", "journal comptable", "comptabilité"],
}


# =====================================================
# Page config
# =====================================================
st.set_page_config(
    page_title="BuildingCopilot RAG",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# =====================================================
# CSS personnalisé
# =====================================================
_ = st.markdown("""
<style>
    /* Typographie */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

    /* Header gradient */
    .main-header {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
        padding: 2rem 2.5rem;
        border-radius: 16px;
        margin-bottom: 2rem;
        color: white;
    }
    .main-header h1 { color: white; margin: 0 0 0.3rem 0; font-size: 1.8rem; font-weight: 700; }
    .main-header p { color: #a0aec0; margin: 0; font-size: 0.95rem; }

    /* Answer card */
    .answer-card {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 1.5rem 2rem;
        margin: 1rem 0;
        line-height: 1.7;
        color: #1a202c;
    }
    .answer-card h1, .answer-card h2, .answer-card h3,
    .answer-card h4, .answer-card h5, .answer-card h6 { color: #1a202c; }
    .answer-card p, .answer-card li, .answer-card td, .answer-card th { color: #2d3748; }
    .answer-card strong { color: #1a202c; }
    .answer-card code { background: #edf2f7; padding: 2px 6px; border-radius: 4px; color: #2d3748; }
    .answer-card a { color: #667eea; }

    /* Source badge */
    .source-badge {
        display: inline-block;
        background: linear-gradient(135deg, #667eea, #764ba2);
        color: white;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
        margin-right: 6px;
    }

    /* Similarity bar */
    .sim-bar {
        height: 6px;
        border-radius: 3px;
        background: #e2e8f0;
        margin-top: 4px;
    }
    .sim-fill {
        height: 100%;
        border-radius: 3px;
        background: linear-gradient(90deg, #667eea, #48bb78);
    }

    /* Theme tag */
    .theme-tag {
        display: inline-block;
        background: #edf2f7;
        color: #4a5568;
        padding: 2px 8px;
        border-radius: 6px;
        font-size: 0.75rem;
        margin: 2px;
    }

    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #1a1a2e 0%, #16213e 100%);
    }
    [data-testid="stSidebar"] .stMarkdown { color: #e2e8f0; }
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3, [data-testid="stSidebar"] label { color: #e2e8f0 !important; }

    /* Stats cards */
    .stat-card {
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 10px;
        padding: 0.8rem 1rem;
        text-align: center;
        margin-bottom: 0.5rem;
    }
    .stat-card .number { font-size: 1.5rem; font-weight: 700; color: #48bb78; }
    .stat-card .label { font-size: 0.75rem; color: #a0aec0; }
</style>
""", unsafe_allow_html=True)


# =====================================================
# Connexions (cached)
# =====================================================
def get_db_connection():
    """Retourne une connexion valide, en la recréant si nécessaire."""
    conn = st.session_state.get("_db_conn")
    if conn is not None:
        try:
            conn.isolation_level  # test rapide de vivacité
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
        "bedrock-runtime",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        config=Config(read_timeout=300, retries={"max_attempts": 3})
    )

@st.cache_resource
def get_reranker():
    """FlashRank multilingue — modèle léger, pas de PyTorch, supporte le français."""
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


# Pré-chauffage Bedrock (TLS + auth) — élimine ~2-3s de latence sur la 1ère requête
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


# =====================================================
# Fonctions RAG
# =====================================================
def detect_query_themes(query):
    query_lower = query.lower()
    matched = []
    for theme, keywords in THEMES_KEYWORDS.items():
        if any(kw in query_lower for kw in keywords):
            matched.append(theme)
    return matched

def detect_doc_type_hint(query):
    query_lower = query.lower()
    for doc_type, keywords in DOC_TYPE_KEYWORDS.items():
        if any(kw in query_lower for kw in keywords):
            return doc_type
    return None

def detect_retrieval_strategy(query, demo_mode=False):
    """
    Détermine automatiquement les paramètres de retrieval.
    Retourne (chunks_per_source, doc_type_boost, max_chunks_llm, label).
    """
    q = query.lower()

    broad_keywords = [
        "tous les", "toutes les", "liste", "lister", "inventaire",
        "historique", "depuis", "au fil des", "combien de",
        "comparer", "comparaison", "entre les",
        "chaque", "ensemble des", "récapitulatif", "synthèse globale",
        "quels sont", "quelles sont", "y a-t-il eu"
    ]
    if any(kw in q for kw in broad_keywords):
        mcl = 40 if demo_mode else 80
        return 2, 0.03, mcl, "🔎 Diversité (inventaire)"

    deep_keywords = [
        "article ", "lot n°", "lot ", "résolution n°",
        "que dit", "que prévoit", "détaille", "explique",
        "dans le règlement", "dans le pv", "dans le contrat",
        "ce document", "ce rapport"
    ]
    if any(kw in q for kw in deep_keywords):
        mcl = 30 if demo_mode else 50
        return 8, 0.005, mcl, "🔬 Profondeur (document ciblé)"

    mcl = 30 if demo_mode else 50
    return 3, 0.01, mcl, "⚖️ Équilibré"

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

def search_chunks(query, copropriete=None, max_chunks=MAX_CHUNKS_LLM_DEFAULT, sim_threshold=SIMILARITY_THRESHOLD, chunks_per_source=MAX_CHUNKS_PER_SOURCE, doc_type_boost=0.01):
    """
    Pipeline de retrieval hybride en 4 étapes :
      1. SQL : vec_rank + bm25_rank indépendants
      2. SQL : RRF fusion + theme_boost + doc_type_boost dynamique
      3. SQL : source diversity (PARTITION BY source_file)
      4. Python : FlashRank rerank (cross-encoder multilingue)

    doc_type_boost est dynamique :
      - Inventaire → 0.03 (fort, pour que les SINISTRE dominent le top 50)
      - Équilibré  → 0.01
      - Ciblé      → 0.005 (léger, pour ne pas biaiser)
    """
    conn = get_db_connection()
    query_embedding = get_embedding(query)
    themes = detect_query_themes(query)
    doc_type_hint = detect_doc_type_hint(query)

    with conn.cursor() as cur:
        where_clauses = []
        params_before = []

        if copropriete:
            where_clauses.append("copropriete = %s")
            params_before.append(copropriete)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        doc_type_for_boost = doc_type_hint if doc_type_hint else "__NONE__"

        sql = f"""
            WITH base AS (
                SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                       themes, text,
                       1 - (embedding <=> %s::vector) as vec_similarity,
                       CASE WHEN themes && %s::text[] THEN {THEME_BOOST} ELSE 0 END as theme_boost,
                       ts_rank(text_search, plainto_tsquery('french', %s), 32) as bm25_score,
                       CASE WHEN doc_type = %s THEN %s ELSE 0 END as doc_type_boost
                FROM chunks
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
                        + theme_boost
                        + doc_type_boost) as rrf_score
                FROM with_ranks
            ),
            diversified AS (
                SELECT *,
                       row_number() OVER (
                           PARTITION BY source_file ORDER BY rrf_score DESC
                       ) as rank_in_source
                FROM with_rrf
            )
            SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                   themes, text, vec_similarity, theme_boost, bm25_score, rrf_score
            FROM diversified
            WHERE rank_in_source <= %s
              AND vec_similarity >= %s
            ORDER BY rrf_score DESC
            LIMIT %s
        """

        params = [
            str(query_embedding),       # vec_similarity
            themes if themes else [],   # theme_boost CASE
            query,                      # bm25 ts_rank
            doc_type_for_boost,         # doc_type CASE — quel type
            doc_type_boost,             # doc_type CASE — valeur
            *params_before,             # WHERE clauses (copropriete only)
            chunks_per_source,          # rank_in_source <=
            sim_threshold,              # vec_similarity >=
            RERANK_CANDIDATES,          # LIMIT
        ]

        cur.execute(sql, params)
        raw_results = cur.fetchall()

    # Déduplication par contenu
    seen_texts = set()
    deduped = []
    for r in raw_results:
        text_sig = r[6][:300].strip()
        if text_sig not in seen_texts:
            seen_texts.add(text_sig)
            deduped.append(r)

    # ── FlashRank rerank ──
    if len(deduped) > 1:
        reranker = get_reranker()
        passages = [{"id": i, "text": r[6][:2000]} for i, r in enumerate(deduped)]
        rerank_request = RerankRequest(query=query, passages=passages)
        reranked = reranker.rerank(rerank_request)
        rerank_order = [item["id"] for item in reranked]
        deduped = [deduped[idx] for idx in rerank_order if idx < len(deduped)]

    return deduped[:max_chunks], themes, doc_type_hint

def generate_answer(query, search_results, themes, doc_type_hint, model_id=LLM_MODEL):
    """Synchrone (non-streaming) — mode production Sonnet."""
    bedrock = get_bedrock_client()
    system_prompt, user_prompt, max_tokens_response = build_llm_payload(query, search_results, themes, doc_type_hint)

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens_response,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}]
    })

    response = bedrock.invoke_model(
        modelId=model_id, body=body,
        contentType="application/json", accept="application/json"
    )
    result = json.loads(response["body"].read())
    return result["content"][0]["text"]


def build_llm_payload(query, search_results, themes, doc_type_hint):
    """Construit le system prompt, user prompt et body JSON pour l'appel LLM.
    Utilisé par generate_answer (sync) et generate_answer_stream (streaming)."""
    context_parts = []
    for i, result in enumerate(search_results):
        chunk_id, copro, source, filename, doc_type, chunk_themes, text, vec_sim, theme_boost, bm25_score, rrf_score = result
        source_type = "PRIMAIRE" if doc_type in PRIMARY_DOC_TYPES else "CONTEXTUEL"
        context_parts.append(
            f"[Source {i+1}] [{source_type}] Copropriété: {copro} | Fichier: {filename} | "
            f"Type: {doc_type} | Thèmes: {', '.join(chunk_themes) if chunk_themes else 'N/A'}\n{text}"
        )
    context = "\n\n---\n\n".join(context_parts)

    system_prompt = """Tu es un assistant expert en gestion de copropriété pour un syndic professionnel.
Tu réponds aux questions en te basant UNIQUEMENT sur les extraits de documents fournis.

Règles STRICTES :
1. Cite toujours les sources (numéro de source, nom du fichier, article ou résolution si applicable)
2. Si l'information n'est PAS dans les extraits, dis-le clairement : "Cette information n'apparaît pas dans les documents disponibles."
   Ne fabrique JAMAIS une réponse sans source.
3. Croise les informations entre les différents documents quand c'est pertinent
4. Utilise un langage professionnel adapté au métier de syndic
5. Si la question porte sur plusieurs thèmes, structure ta réponse par thème
6. Si un extrait contient des données OCR de mauvaise qualité (caractères cassés,
   tableaux mal formés), signale-le et extrais ce qui est lisible
7. Quand tu cites des montants, tantièmes ou numéros de lot, vérifie qu'ils sont
   cohérents entre les sources avant de les affirmer
8. Chaque source est marquée [PRIMAIRE] ou [CONTEXTUEL].
   Les sources PRIMAIRES (SINISTRE, ENTRETIEN, COMPTABILITE, DEVIS, FACTURE) correspondent
   chacune potentiellement à un événement, un sinistre, une intervention ou une dépense DISTINCTE.
   Les sources CONTEXTUELLES (PV_AG, RCP, CONTRAT, COURRIER...) enrichissent la réponse mais
   ne constituent pas un événement en soi.
9. Pour une question d'inventaire ou de liste exhaustive, scanne CHAQUE source PRIMAIRE
   et extrais-en l'information clé (date, lieu, nature, personnes concernées),
   même si l'extrait est court ou de mauvaise qualité OCR. Ne saute aucune source PRIMAIRE.
10. Si tu identifies des références à des éléments non présents dans les extraits fournis
    (ex: un document mentionne 'sinistre n°5' mais tu n'en as pas les détails), signale-le."""

    context_hints = []
    if themes:
        context_hints.append(f"Thèmes détectés : {', '.join(themes)}")
    if doc_type_hint:
        context_hints.append(f"Type de document principal détecté : {doc_type_hint} (mais l'information peut aussi apparaître dans d'autres types de documents)")

    primary_sources = set()
    primary_types_found = set()
    for r in search_results:
        if r[4] in PRIMARY_DOC_TYPES:
            primary_sources.add(r[2])
            primary_types_found.add(r[4])

    hints_text = "\n".join(context_hints) if context_hints else "Aucun filtre spécifique"

    user_prompt = f"""Question : {query}

{hints_text}

Voici les {len(search_results)} extraits de documents les plus pertinents :

{context}

Réponds de manière structurée et précise en citant les sources.
Si la question demande une liste exhaustive ou un historique, cite TOUTES les occurrences trouvées dans les extraits, pas seulement les premières."""

    if primary_sources:
        user_prompt += f"\n\n⚠️ {len(primary_sources)} sources PRIMAIRES de type {', '.join(sorted(primary_types_found))} sont dans les extraits ci-dessus. Assure-toi de toutes les couvrir dans ta réponse."

    max_tokens_response = 4096 if len(search_results) > 30 else 3000

    return system_prompt, user_prompt, max_tokens_response


def generate_answer_stream(query, search_results, themes, doc_type_hint, model_id, placeholder):
    """Streaming : écrit progressivement dans un placeholder Streamlit."""
    bedrock = get_bedrock_client()
    system_prompt, user_prompt, max_tokens_response = build_llm_payload(query, search_results, themes, doc_type_hint)

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens_response,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}]
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


def linkify_sources(text, max_source_num):
    """Transforme les 'Source N' en liens cliquables vers les ancres #source-N."""
    def replace_source(match):
        num = int(match.group(1))
        if 1 <= num <= max_source_num:
            return (f'<a href="#source-{num}" '
                    f'style="color:#3182ce;text-decoration:underline;font-weight:500">'
                    f'Source {num}</a>')
        return match.group(0)
    return re.sub(r'(?<!\w)Source\s+(\d+)(?!\w)', replace_source, text)


# =====================================================
# SIDEBAR
# =====================================================
with st.sidebar:
    st.markdown("## 🏢 BuildingCopilot")
    st.markdown("---")

    # Stats
    copros = get_copros()
    total = get_total_chunks()

    _ = st.markdown(f"""
    <div class="stat-card">
        <div class="number">{total:,}</div>
        <div class="label">chunks indexés</div>
    </div>
    <div class="stat-card">
        <div class="number">{len(copros)}</div>
        <div class="label">copropriété(s)</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    # Filtre copropriété
    copro_names = ["Toutes les copropriétés"] + [c[0] for c in copros]
    selected_copro = st.selectbox("📁 Filtrer par copropriété", copro_names)

    if selected_copro != "Toutes les copropriétés":
        copro_count = next((c[1] for c in copros if c[0] == selected_copro), 0)
        st.caption(f"{copro_count} chunks disponibles")

    st.markdown("---")

    # Mode démo
    demo_mode = st.toggle("⚡ Mode Démo", value=False,
                           help="Haiku 4.5 + streaming + chunks réduits. "
                                "~15-20s au lieu de ~90s. Qualité légèrement réduite.")
    if demo_mode:
        st.caption("⚡ Haiku 4.5 + streaming")

    # Paramètres avancés
    with st.expander("⚙️ Paramètres avancés"):
        auto_strategy = st.checkbox("Stratégie de retrieval automatique", value=True,
                                     help="Détecte automatiquement si la requête est large (inventaire) "
                                          "ou ciblée (document précis) et ajuste chunks/source, "
                                          "doc_type boost et max chunks envoyés à Claude.")
        if not auto_strategy:
            chunks_per_source = st.slider("Max chunks par document source", 1, 10, MAX_CHUNKS_PER_SOURCE)
            max_chunks = st.slider("Chunks analysés par l'IA", 15, 100, MAX_CHUNKS_LLM_DEFAULT)
        else:
            chunks_per_source = None
            max_chunks = None
        display_k = st.slider("Sources affichées", 5, 30, TOP_K_DISPLAY)
        sim_threshold = st.slider("Seuil de similarité", 0.0, 1.0, SIMILARITY_THRESHOLD, 0.05)

    st.markdown("---")
    st.markdown("""
    **Exemples de questions :**
    - Quel est le règlement de copropriété ?
    - Quels travaux ont été votés ?
    - Quel est le budget prévisionnel ?
    - Que disent les diagnostics techniques ?
    """)


# =====================================================
# ZONE PRINCIPALE
# =====================================================
_ = st.markdown("""
<div class="main-header">
    <h1>🏢 BuildingCopilot RAG</h1>
    <p>Posez vos questions sur les archives de copropriété — réponses sourcées par IA</p>
</div>
""", unsafe_allow_html=True)

# Barre de recherche
query = st.text_input(
    "🔍 Posez votre question",
    placeholder="Ex : Quels travaux ont été votés en AG ?",
    label_visibility="collapsed"
)

# Recherche
if query:
    copro_filter = selected_copro if selected_copro != "Toutes les copropriétés" else None
    DISPLAY_K_ACTUAL = display_k if 'display_k' in dir() else TOP_K_DISPLAY
    SIM_ACTUAL = sim_threshold if 'sim_threshold' in dir() else SIMILARITY_THRESHOLD

    # Stratégie de retrieval : auto (4 paramètres) ou manuelle
    _auto = auto_strategy if 'auto_strategy' in dir() else True
    _demo = demo_mode if 'demo_mode' in dir() else False
    if _auto:
        CPS_ACTUAL, DTB_ACTUAL, MCL_ACTUAL, strategy_label = detect_retrieval_strategy(query, demo_mode=_demo)
    else:
        CPS_ACTUAL = chunks_per_source if 'chunks_per_source' in dir() else MAX_CHUNKS_PER_SOURCE
        MCL_ACTUAL = max_chunks if 'max_chunks' in dir() else MAX_CHUNKS_LLM_DEFAULT
        DTB_ACTUAL = 0.01
        strategy_label = f"Manuel ({CPS_ACTUAL}/source, {MCL_ACTUAL} chunks)"

    # Choix du modèle LLM
    active_model = LLM_MODEL_FAST if _demo else LLM_MODEL
    model_label = "Haiku 4.5 ⚡" if _demo else "Sonnet 4.6"

    with st.spinner("⏳ Recherche dans les archives..."):
        results, themes, doc_type_hint = search_chunks(
            query, copro_filter,
            max_chunks=MCL_ACTUAL,
            sim_threshold=SIM_ACTUAL,
            chunks_per_source=CPS_ACTUAL,
            doc_type_boost=DTB_ACTUAL
        )

    if not results:
        st.warning("❌ Aucun résultat trouvé. Essayez de reformuler votre question.")
    else:
        if _demo:
            # Mode démo : affichage léger
            unique_sources = len(set(r[2] for r in results))
            st.caption(f"⚡ {len(results)} extraits analysés issus de {unique_sources} documents — {model_label}")
        else:
            # Mode normal : affichage technique complet
            if themes:
                theme_html = " ".join(
                    f'<span class="theme-tag">{THEME_LABELS.get(t, t)}</span>'
                    for t in themes
                )
                st.markdown(f"**Thèmes détectés :** {theme_html}", unsafe_allow_html=True)
            if doc_type_hint:
                st.markdown(f"**Type de document priorisé :** {doc_type_hint} *(boost, pas filtre)*")

            st.markdown(f"**Stratégie :** {strategy_label} ({CPS_ACTUAL}/source, boost={DTB_ACTUAL}, max={MCL_ACTUAL} chunks) — **{model_label}**")

            unique_sources = len(set(r[2] for r in results))
            st.markdown(
                f"**{len(results)}** chunks analysés par l'IA issus de **{unique_sources}** documents distincts"
                + (f" (top {DISPLAY_K_ACTUAL} affichés ci-dessous)" if len(results) > DISPLAY_K_ACTUAL else "")
            )

        # Section Visite 3D (démo uniquement, avant la réponse)
        if _demo and DEMO_3D_LINKS:
            # Chercher dans la requête, les noms de copro, les noms de fichiers ET le texte des chunks
            search_pool = query.lower() + " " + " ".join(
                (r[1] + " " + r[2] + " " + r[6][:200]).lower() for r in results
            )
            matched_3d = {kw: url for kw, url in DEMO_3D_LINKS.items()
                         if kw.lower() in search_pool}
            if matched_3d:
                st.markdown("### 🏠 Visite 3D")
                for keyword, url in matched_3d.items():
                    st.markdown(
                        f'<div style="background:linear-gradient(135deg,#1a365d,#2a4a7f);'
                        f'padding:1rem 1.5rem;border-radius:10px;margin-bottom:1rem">'
                        f'<span style="color:#e2e8f0">Le dossier <strong>{keyword}</strong> '
                        f'est associé à une visite 3D — </span>'
                        f'<a href="{url}" target="_blank" '
                        f'style="color:#63b3ed;text-decoration:underline;font-weight:600">'
                        f'ouvrir la visite 3D ↗</a></div>',
                        unsafe_allow_html=True
                    )

        # Générer la réponse
        st.markdown("### 💬 Réponse")

        # Nombre max de sources affichées (pour limiter les liens aux sources visibles)
        n_displayed = min(len(results), DISPLAY_K_ACTUAL)

        if _demo:
            # Streaming : premiers mots en ~2-3s, linkify à la fin
            with st.container(border=True):
                answer_placeholder = st.empty()
                answer = generate_answer_stream(query, results, themes, doc_type_hint, active_model, answer_placeholder)
                answer_placeholder.markdown(linkify_sources(answer, n_displayed), unsafe_allow_html=True)
        else:
            # Sync : attente complète
            with st.spinner("🤖 Génération de la réponse par Claude Sonnet..."):
                answer = generate_answer(query, results, themes, doc_type_hint, model_id=active_model)
            with st.container(border=True):
                st.markdown(linkify_sources(answer, n_displayed), unsafe_allow_html=True)

        # Afficher les sources — seulement les DISPLAY_K_ACTUAL premiers
        st.markdown("### 📎 Sources utilisées")

        for i, result in enumerate(results[:DISPLAY_K_ACTUAL]):
            chunk_id, copro, source, filename, doc_type, chunk_themes, text, vec_sim, theme_boost, bm25_score, rrf_score = result
            theme_boost = float(theme_boost)
            # Score affiché = position dans le classement reranké (plus intuitif que le rrf brut)
            rank_pct = max(0, int(100 * (1 - i / max(len(results), 1))))
            
            sim_color = "#48bb78" if i < 5 else "#ecc94b" if i < 15 else "#fc8181"

            boost_indicator = ""
            if theme_boost > 0:
                boost_indicator += " +🏷️"
            if bm25_score > 0.1:
                boost_indicator += " +📝"

            with st.expander(f"Source {i+1} — {filename}  ({doc_type}) {boost_indicator}"):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.caption(f"📁 **Copropriété :** {copro}")
                    st.caption(f"📄 **Fichier :** {source}")
                    if chunk_themes:
                        st.caption(f"🏷️ **Thèmes :** {', '.join(chunk_themes)}")
                    st.caption(f"📊 **Scores :** vec={vec_sim:.2f}  bm25={bm25_score:.3f}  rrf={rrf_score:.4f}")
                with col2:
                    st.markdown(f"""
                    <div style="text-align:center">
                        <div style="font-size:1.4rem;font-weight:700;color:{sim_color}">#{i+1}</div>
                        <div style="font-size:0.7rem;color:#a0aec0">rang reranké</div>
                    </div>
                    """, unsafe_allow_html=True)

                # Ancre pour le lien depuis la réponse (à l'intérieur de l'expander pour éviter les None)
                _ = st.markdown(f'<div id="source-{i+1}"></div>', unsafe_allow_html=True)
                st.markdown("---")
                st.text(text[:2000] + ("..." if len(text) > 2000 else ""))
