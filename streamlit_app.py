"""
ÉTAPE 7 — Interface de requête RAG (Streamlit Cloud) — v2 Multi-turn
Pipeline : Vector + BM25 → RRF fusion → Source diversity → Claude (sans FlashRank, RERANK_CANDIDATES=200)
Lance : streamlit run streamlit_app.py

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
CLIENT_LOGO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Logo_NCG.png")

EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
LLM_MODEL = "eu.anthropic.claude-sonnet-4-6"
LLM_MODEL_FAST = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"

MAX_CHUNKS_LLM_DEFAULT = 50
MAX_CHUNKS_LLM_BROAD = 80
TOP_K_DISPLAY = 20            # Sources principales affichées
TOP_K_EXTRA = 50              # Hard limit sources supplémentaires (chunks 21 à 50)
MAX_CHUNKS_PER_SOURCE = 3
SIMILARITY_THRESHOLD = 0.15
THEME_BOOST = 0.05
RRF_K = 60
RERANK_CANDIDATES = 200       # Plus élevé pour compenser l'absence de FlashRank en cloud
RCP_MIN_SLOTS = 3             # POINT 6 : quota minimum RCP

# Multi-turn
MAX_HISTORY_TURNS = 3
MAX_HISTORY_CHARS = 16000     # ~4K tokens budget
FOLLOWUP_QUERY_THRESHOLD = 60

PRIMARY_DOC_TYPES = {"SINISTRE", "ENTRETIEN", "COMPTABILITE", "DEVIS", "FACTURE"}

# Liens 3D démo — fichier texte avec format "MOT_CLE : URL" (une paire par ligne)
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
    .answer-card h1, .answer-card h2, .answer-card h3,
    .answer-card h4, .answer-card h5, .answer-card h6 { color: #1a202c; }
    .answer-card p, .answer-card li, .answer-card td, .answer-card th { color: #2d3748; }
    .answer-card strong { color: #1a202c; }
    .answer-card code { background: #edf2f7; padding: 2px 6px; border-radius: 4px; color: #2d3748; }
    .answer-card a { color: #667eea; }

    .source-badge {
        display: inline-block; background: linear-gradient(135deg, #667eea, #764ba2);
        color: white; padding: 2px 10px; border-radius: 12px;
        font-size: 0.75rem; font-weight: 600; margin-right: 6px;
    }
    .sim-bar { height: 6px; border-radius: 3px; background: #e2e8f0; margin-top: 4px; }
    .sim-fill { height: 100%; border-radius: 3px; background: linear-gradient(90deg, #667eea, #48bb78); }
    .theme-tag {
        display: inline-block; background: #edf2f7; color: #4a5568;
        padding: 2px 8px; border-radius: 6px; font-size: 0.75rem; margin: 2px;
    }

    /* ── Sidebar — fond bleu marine ── */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #1a1a2e 0%, #16213e 100%);
    }
    [data-testid="stSidebar"] .stMarkdown { color: #e2e8f0; }
    /* POINT 1 : TOUT le texte sidebar en blanc */
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
def detect_query_themes(query):
    query_lower = query.lower()
    return [t for t, kws in THEMES_KEYWORDS.items() if any(kw in query_lower for kw in kws)]

def detect_doc_type_hint(query):
    query_lower = query.lower()
    for doc_type, keywords in DOC_TYPE_KEYWORDS.items():
        if any(kw in query_lower for kw in keywords):
            return doc_type
    return None


def detect_retrieval_strategy(query, demo_mode=False):
    """
    POINT 5 : détection temporelle >2 ans → force inventaire (80 chunks).
    """
    q = query.lower()

    # ── Détection temporelle : plage > 2 ans ──
    year_matches = re.findall(r'20[0-2]\d', q)
    if len(year_matches) >= 2:
        years = sorted(set(int(y) for y in year_matches))
        if years[-1] - years[0] > 2:
            mcl = 40 if demo_mode else 80
            return 2, 0.03, mcl, "🔎 Inventaire (plage temporelle)"

    since_match = re.search(r'depuis\s+20([0-2]\d)', q)
    if since_match:
        since_year = 2000 + int(since_match.group(1))
        if 2026 - since_year > 2:
            mcl = 40 if demo_mode else 80
            return 2, 0.03, mcl, "🔎 Inventaire (historique)"

    broad_keywords = [
        "tous les", "toutes les", "liste", "lister", "inventaire",
        "historique", "depuis", "au fil des", "combien de",
        "comparer", "comparaison", "entre les",
        "chaque", "ensemble des", "récapitulatif", "synthèse globale",
        "quels sont", "quelles sont", "y a-t-il eu",
        "évolution", "tendance", "progression",
    ]
    if any(kw in q for kw in broad_keywords):
        mcl = 40 if demo_mode else 80
        return 2, 0.03, mcl, "🔎 Inventaire"

    deep_keywords = [
        "article ", "lot n°", "lot ", "résolution n°",
        "que dit", "que prévoit", "détaille", "explique",
        "dans le règlement", "dans le pv", "dans le contrat",
        "ce document", "ce rapport",
    ]
    if any(kw in q for kw in deep_keywords):
        mcl = 30 if demo_mode else 50
        return 8, 0.005, mcl, "🔬 Ciblé"

    mcl = 30 if demo_mode else 50
    return 3, 0.01, mcl, "⚖️ Équilibré"


def expand_followup_query(current_query, chat_history):
    """POINT 7 : enrichit les questions de suivi courtes."""
    if not chat_history:
        return current_query, False

    q = current_query.strip()
    followup_markers = [
        "et ", "aussi", "même", "pareil", "idem",
        "ça", "ce ", "ces ", "le même", "la même", "les mêmes",
        "celui", "celle", "précise", "détaille", "développe",
        "pour quelle", "quel montant", "à quelle date",
        "combien", "pourquoi", "comment",
    ]
    is_short = len(q) < FOLLOWUP_QUERY_THRESHOLD
    has_marker = any(q.lower().startswith(m) or f" {m}" in q.lower() for m in followup_markers)
    year_only = bool(re.match(r'^(et\s+)?(en\s+|pour\s+)?20\d{2}\s*\??$', q, re.IGNORECASE))

    if is_short and (has_marker or year_only):
        prev_queries = [h["content"] for h in chat_history if h["role"] == "user"]
        if prev_queries:
            return f"{prev_queries[-1]} — {q}", True

    return current_query, False


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


def search_chunks(query, copropriete=None, max_chunks=MAX_CHUNKS_LLM_DEFAULT,
                  sim_threshold=SIMILARITY_THRESHOLD, chunks_per_source=MAX_CHUNKS_PER_SOURCE,
                  doc_type_boost=0.01):
    """
    Pipeline hybride 3 étapes (sans FlashRank en cloud, compensé par RERANK_CANDIDATES=200) + quota RCP.
    """
    conn = get_db_connection()
    query_embedding = get_embedding(query)
    themes = detect_query_themes(query)
    doc_type_hint = detect_doc_type_hint(query)

    with conn.cursor() as cur:
        where_clauses, params_before = [], []
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
            str(query_embedding), themes if themes else [], query,
            doc_type_for_boost, doc_type_boost,
            *params_before,
            chunks_per_source, sim_threshold, RERANK_CANDIDATES,
        ]
        cur.execute(sql, params)
        raw_results = cur.fetchall()

    # Déduplication
    seen_texts, deduped = set(), []
    for r in raw_results:
        sig = r[6][:300].strip()
        if sig not in seen_texts:
            seen_texts.add(sig)
            deduped.append(r)

    # (Pas de FlashRank en cloud — RRF score utilisé directement)

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

    return top, themes, doc_type_hint


# =====================================================
# Multi-turn — historique LLM (POINT 4)
# =====================================================
def build_history_messages(chat_history):
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
def build_llm_payload(query, search_results, themes, doc_type_hint, chat_history=None):
    context_parts = []
    for i, result in enumerate(search_results):
        chunk_id, copro, source, filename, doc_type, chunk_themes, text, *_ = result
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
- Pas de formules de politesse ni de conclusions génériques."""

    has_history = bool(chat_history)
    if has_history:
        system_prompt += """

CONTEXTE CONVERSATIONNEL :
- Un historique de conversation est fourni. Utilise-le pour comprendre les questions de suivi.
- Ne répète PAS les informations déjà données sauf demande explicite.
- Appuie-toi sur le contexte des échanges précédents."""

    context_hints = []
    if themes:
        context_hints.append(f"Thèmes détectés : {', '.join(themes)}")
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

    max_tokens_response = 4096 if len(search_results) > 30 else 2500

    messages = build_history_messages(chat_history) if has_history else []
    messages.append({"role": "user", "content": user_prompt})

    return system_prompt, messages, max_tokens_response


def generate_answer(query, search_results, themes, doc_type_hint,
                    model_id=LLM_MODEL, chat_history=None):
    bedrock = get_bedrock_client()
    system_prompt, messages, max_tokens = build_llm_payload(
        query, search_results, themes, doc_type_hint, chat_history
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


def generate_answer_stream(query, search_results, themes, doc_type_hint,
                           model_id, placeholder, chat_history=None):
    bedrock = get_bedrock_client()
    system_prompt, messages, max_tokens = build_llm_payload(
        query, search_results, themes, doc_type_hint, chat_history
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
    pfx = f"{anchor_prefix}-" if anchor_prefix else ""
    def replace_source(match):
        num = int(match.group(1))
        if 1 <= num <= max_source_num:
            return (f'<a href="#source-{pfx}{num}" '
                    f'style="color:#3182ce;text-decoration:underline;font-weight:500">'
                    f'Source {num}</a>')
        return match.group(0)
    return re.sub(r'(?<!\w)Source\s+(\d+)(?!\w)', replace_source, text)


# =====================================================
# POINT 2 : boutons copier / sauvegarder
# =====================================================
def render_action_buttons(answer_text, key_suffix=""):
    import base64
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
    pfx = f"{anchor_prefix}-" if anchor_prefix else ""
    st.markdown(title)
    for i, result in enumerate(results[:display_k]):
        rank = offset + i
        num = rank + 1
        chunk_id, copro, source, filename, doc_type, chunk_themes, text, vec_sim, theme_boost, bm25_score, rrf_score = result
        theme_boost = float(theme_boost)
        sim_color = "#48bb78" if rank < 5 else "#ecc94b" if rank < 15 else "#fc8181"
        boost_ind = (" +🏷️" if theme_boost > 0 else "") + (" +📝" if bm25_score > 0.1 else "")

        with st.expander(f"Source {num} — {filename}  ({doc_type}){boost_ind}"):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.caption(f"📁 **Copropriété :** {copro}")
                st.caption(f"📄 **Fichier :** {source}")
                if chunk_themes:
                    st.caption(f"🏷️ **Thèmes :** {', '.join(chunk_themes)}")
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

# ── Afficher l'historique ──
for msg_idx, msg in enumerate(st.session_state.chat_history):
    is_last_assistant = (
        msg["role"] == "assistant"
        and msg_idx == len(st.session_state.chat_history) - 1
    )

    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["content"])
        else:
            n_disp = msg.get("n_displayed", 0)
            apfx = f"m{msg_idx}"
            _ = st.markdown(linkify_sources(msg["content"], n_disp, anchor_prefix=apfx), unsafe_allow_html=True)

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


# ── Saisie utilisateur ──
user_input = st.chat_input("Posez votre question sur les archives de copropriété…")

if user_input:
    st.session_state.chat_history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    copro_filter = selected_copro if selected_copro != "Toutes les copropriétés" else None
    DISPLAY_K_ACTUAL = display_k if 'display_k' in dir() else TOP_K_DISPLAY
    SIM_ACTUAL = sim_threshold if 'sim_threshold' in dir() else SIMILARITY_THRESHOLD
    _auto = auto_strategy if 'auto_strategy' in dir() else True
    _demo = demo_mode if 'demo_mode' in dir() else False

    query_for_retrieval, was_expanded = expand_followup_query(
        user_input, st.session_state.chat_history[:-1]
    )

    if _auto:
        CPS_ACTUAL, DTB_ACTUAL, MCL_ACTUAL, strategy_label = detect_retrieval_strategy(
            query_for_retrieval, demo_mode=_demo
        )
    else:
        CPS_ACTUAL = chunks_per_source if chunks_per_source else MAX_CHUNKS_PER_SOURCE
        MCL_ACTUAL = max_chunks if max_chunks else MAX_CHUNKS_LLM_DEFAULT
        DTB_ACTUAL = 0.01
        strategy_label = f"Manuel ({CPS_ACTUAL}/source, {MCL_ACTUAL} chunks)"

    active_model = LLM_MODEL_FAST if _demo else LLM_MODEL
    model_label = "Haiku 4.5 ⚡" if _demo else "Sonnet 4.6"

    with st.spinner("⏳ Recherche dans les archives..."):
        results, themes, doc_type_hint = search_chunks(
            query_for_retrieval, copro_filter,
            max_chunks=MCL_ACTUAL, sim_threshold=SIM_ACTUAL,
            chunks_per_source=CPS_ACTUAL, doc_type_boost=DTB_ACTUAL,
        )

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

            if was_expanded:
                _ = st.markdown(
                    '<span class="followup-badge">🔗 Suite de la conversation</span>',
                    unsafe_allow_html=True,
                )

            if _demo:
                st.caption(f"⚡ {len(results)} extraits · {unique_sources} docs · {model_label}")
            else:
                st.caption(f"{strategy_label} · {len(results)} chunks · {unique_sources} docs · {model_label}")

            if _demo and DEMO_3D_LINKS:
                search_pool = user_input.lower() + " " + " ".join(
                    (r[2] + " " + r[6][:200]).lower() for r in results
                )
                for kw, url in DEMO_3D_LINKS.items():
                    if kw.lower() in search_pool:
                        _ = st.markdown(
                            f'<div style="background:linear-gradient(135deg,#1a365d,#2a4a7f);'
                            f'padding:0.7rem 1.2rem;border-radius:10px;margin-bottom:0.5rem">'
                            f'<span style="color:#e2e8f0"><strong>{kw}</strong> — </span>'
                            f'<a href="{url}" target="_blank" '
                            f'style="color:#63b3ed;text-decoration:underline;font-weight:600">'
                            f'visite 3D ↗</a></div>',
                            unsafe_allow_html=True,
                        )

            history_for_llm = st.session_state.chat_history[:-1]
            n_displayed = min(len(results), TOP_K_EXTRA)
            cur_apfx = f"m{len(st.session_state.chat_history)}"

            if _demo:
                answer_placeholder = st.empty()
                answer = generate_answer_stream(
                    user_input, results, themes, doc_type_hint,
                    active_model, answer_placeholder, chat_history=history_for_llm,
                )
                answer_placeholder.markdown(
                    linkify_sources(answer, n_displayed, anchor_prefix=cur_apfx), unsafe_allow_html=True
                )
            else:
                with st.spinner("🤖 Génération de la réponse…"):
                    answer = generate_answer(
                        user_input, results, themes, doc_type_hint,
                        model_id=active_model, chat_history=history_for_llm,
                    )
                _ = st.markdown(
                    linkify_sources(answer, n_displayed, anchor_prefix=cur_apfx), unsafe_allow_html=True
                )

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
                    "themes": themes, "doc_type_hint": doc_type_hint,
                    "expanded": was_expanded,
                },
            })
