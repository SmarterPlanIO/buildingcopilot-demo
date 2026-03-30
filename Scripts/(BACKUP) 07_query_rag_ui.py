"""
ÉTAPE 7 — Interface de requête RAG (Streamlit)
Lance : streamlit run 07_query_rag_ui.py
"""
import json
import re
import boto3
import psycopg2
import streamlit as st

# =====================================================
# CONFIGURATION
# =====================================================
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = "SmarterRAG99!"
AWS_REGION = "eu-west-1"

EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
LLM_MODEL = "eu.anthropic.claude-sonnet-4-6"

TOP_K = 10
SIMILARITY_THRESHOLD = 0.15

# =====================================================
# Thèmes métier
# =====================================================
THEMES_KEYWORDS = {
    "syndic_obligations": ["syndic", "obligation", "mission", "mandat"],
    "parties_communes": ["parties communes", "commun", "hall", "toiture", "façade"],
    "parties_privatives": ["privatif", "privative", "lot", "appartement"],
    "charges_generales": ["charges générales", "entretien", "conservation"],
    "charges_speciales": ["charges spéciales", "ascenseur", "chauffage", "utilité"],
    "assemblee_generale": ["assemblée générale", "ag", "vote", "majorité", "résolution"],
    "conseil_syndical": ["conseil syndical", "président du conseil"],
    "travaux": ["travaux", "ravalement", "rénovation", "devis"],
    "mutations_ventes": ["vente", "mutation", "état daté", "notaire"],
    "assurance_sinistres": ["assurance", "sinistre", "dégât des eaux"],
    "contentieux": ["contentieux", "impayé", "mise en demeure", "recouvrement"],
    "diagnostics_techniques": ["diagnostic", "dpe", "amiante"],
    "comptabilite": ["budget", "comptabilité", "appel de fonds"],
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
}

# =====================================================
# Page config
# =====================================================
st.set_page_config(
    page_title="BuildingCopilot RAG",
    page_icon="🏢",
    layout="wide",
)

# =====================================================
# CSS personnalisé
# =====================================================
st.markdown("""
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
    }

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
@st.cache_resource
def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )

@st.cache_resource
def get_bedrock_client():
    return boto3.client("bedrock-runtime", region_name=AWS_REGION)

@st.cache_data(ttl=300)
def get_copros():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT copropriete, COUNT(*) FROM chunks GROUP BY copropriete ORDER BY copropriete;")
    result = cur.fetchall()
    cur.close()
    return result

@st.cache_data(ttl=300)
def get_total_chunks():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM chunks;")
    count = cur.fetchone()[0]
    cur.close()
    return count


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

def get_embedding(text):
    bedrock = get_bedrock_client()
    body = json.dumps({"inputText": text, "dimensions": 1024, "normalize": True})
    response = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL, body=body,
        contentType="application/json", accept="application/json"
    )
    return json.loads(response["body"].read())["embedding"]

def search_chunks(query, copropriete=None):
    conn = get_db_connection()
    query_embedding = get_embedding(query)
    themes = detect_query_themes(query)
    cur = conn.cursor()

    # Recherche avec filtrage thématique
    if themes and copropriete:
        cur.execute("""
            SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                   themes, text, 1 - (embedding <=> %s::vector) as similarity
            FROM chunks WHERE copropriete = %s AND themes && %s::text[]
            ORDER BY embedding <=> %s::vector LIMIT %s
        """, (str(query_embedding), copropriete, themes, str(query_embedding), TOP_K))
    elif themes:
        cur.execute("""
            SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                   themes, text, 1 - (embedding <=> %s::vector) as similarity
            FROM chunks WHERE themes && %s::text[]
            ORDER BY embedding <=> %s::vector LIMIT %s
        """, (str(query_embedding), themes, str(query_embedding), TOP_K))
    elif copropriete:
        cur.execute("""
            SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                   themes, text, 1 - (embedding <=> %s::vector) as similarity
            FROM chunks WHERE copropriete = %s
            ORDER BY embedding <=> %s::vector LIMIT %s
        """, (str(query_embedding), copropriete, str(query_embedding), TOP_K))
    else:
        cur.execute("""
            SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                   themes, text, 1 - (embedding <=> %s::vector) as similarity
            FROM chunks ORDER BY embedding <=> %s::vector LIMIT %s
        """, (str(query_embedding), str(query_embedding), TOP_K))

    results = cur.fetchall()

    # Fallback : si le filtrage thématique n'a rien donné, relancer sans filtre
    if not results and themes:
        if copropriete:
            cur.execute("""
                SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                       themes, text, 1 - (embedding <=> %s::vector) as similarity
                FROM chunks WHERE copropriete = %s
                ORDER BY embedding <=> %s::vector LIMIT %s
            """, (str(query_embedding), copropriete, str(query_embedding), TOP_K))
        else:
            cur.execute("""
                SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                       themes, text, 1 - (embedding <=> %s::vector) as similarity
                FROM chunks ORDER BY embedding <=> %s::vector LIMIT %s
            """, (str(query_embedding), str(query_embedding), TOP_K))
        results = cur.fetchall()

    cur.close()
    filtered = [r for r in results if r[7] >= SIMILARITY_THRESHOLD]
    return filtered, themes

def generate_answer(query, search_results, themes):
    bedrock = get_bedrock_client()

    context_parts = []
    for i, result in enumerate(search_results):
        chunk_id, copro, source, filename, doc_type, chunk_themes, text, similarity = result
        context_parts.append(
            f"[Source {i+1}] Copropriété: {copro} | Fichier: {filename} | "
            f"Type: {doc_type} | Thèmes: {', '.join(chunk_themes) if chunk_themes else 'N/A'} | "
            f"Pertinence: {similarity:.2f}\n{text}"
        )
    context = "\n\n---\n\n".join(context_parts)

    system_prompt = """Tu es un assistant expert en gestion de copropriété pour un syndic professionnel.
Tu réponds aux questions en te basant UNIQUEMENT sur les extraits de documents fournis.

Règles :
- Cite toujours les sources (numéro de source, nom du fichier, article si applicable)
- Si l'information n'est pas dans les extraits, dis-le clairement
- Croise les informations entre les différents documents quand c'est pertinent
- Utilise un langage professionnel adapté au métier de syndic
- Si la question porte sur plusieurs thèmes, structure ta réponse par thème
- Formate ta réponse en Markdown pour une meilleure lisibilité"""

    user_prompt = f"""Question : {query}

Thèmes détectés dans la question : {', '.join(themes) if themes else 'aucun thème spécifique'}

Voici les extraits de documents pertinents :

{context}

Réponds de manière structurée et précise en citant les sources."""

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2000,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}]
    })

    response = bedrock.invoke_model(
        modelId=LLM_MODEL, body=body,
        contentType="application/json", accept="application/json"
    )
    result = json.loads(response["body"].read())
    return result["content"][0]["text"]


# =====================================================
# SIDEBAR
# =====================================================
with st.sidebar:
    st.markdown("## 🏢 BuildingCopilot")
    st.markdown("---")

    # Stats
    copros = get_copros()
    total = get_total_chunks()

    st.markdown(f"""
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

    # Paramètres avancés
    with st.expander("⚙️ Paramètres avancés"):
        top_k = st.slider("Nombre de résultats", 3, 20, TOP_K)
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
st.markdown("""
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

    # Utiliser les paramètres avancés si modifiés
    if 'top_k' in dir():
        TOP_K_ACTUAL = top_k
        SIM_ACTUAL = sim_threshold
    else:
        TOP_K_ACTUAL = TOP_K
        SIM_ACTUAL = SIMILARITY_THRESHOLD

    with st.spinner("⏳ Recherche dans les archives..."):
        results, themes = search_chunks(query, copro_filter)

    if not results:
        st.warning("❌ Aucun résultat trouvé. Essayez de reformuler votre question.")
    else:
        # Thèmes détectés
        if themes:
            theme_html = " ".join(
                f'<span class="theme-tag">{THEME_LABELS.get(t, t)}</span>'
                for t in themes
            )
            st.markdown(f"**Thèmes détectés :** {theme_html}", unsafe_allow_html=True)

        st.markdown(f"**{len(results)}** documents pertinents trouvés")

        # Générer la réponse
        with st.spinner("🤖 Génération de la réponse par Claude..."):
            answer = generate_answer(query, results, themes)

        # Afficher la réponse
        st.markdown("### 💬 Réponse")
        st.markdown(f'<div class="answer-card">', unsafe_allow_html=True)
        st.markdown(answer)
        st.markdown('</div>', unsafe_allow_html=True)

        # Afficher les sources
        st.markdown("### 📎 Sources utilisées")

        for i, result in enumerate(results):
            chunk_id, copro, source, filename, doc_type, chunk_themes, text, similarity = result
            sim_pct = int(similarity * 100)
            sim_color = "#48bb78" if similarity > 0.6 else "#ecc94b" if similarity > 0.4 else "#fc8181"

            with st.expander(f"Source {i+1} — {filename}  ({doc_type})  •  pertinence: {sim_pct}%"):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.caption(f"📁 **Copropriété :** {copro}")
                    st.caption(f"📄 **Fichier :** {source}")
                    if chunk_themes:
                        st.caption(f"🏷️ **Thèmes :** {', '.join(chunk_themes)}")
                with col2:
                    st.markdown(f"""
                    <div style="text-align:center">
                        <div style="font-size:1.8rem;font-weight:700;color:{sim_color}">{sim_pct}%</div>
                        <div style="font-size:0.7rem;color:#a0aec0">pertinence</div>
                    </div>
                    """, unsafe_allow_html=True)

                st.markdown("---")
                st.text(text[:2000] + ("..." if len(text) > 2000 else ""))
