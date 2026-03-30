"""
ÉTAPE 7 — Interface de requête RAG
Lance : python 07_query_rag.py
Tape ta question, puis Entrée. Tape 'quit' pour sortir.
"""
import json
import re
import boto3
import psycopg2

# =====================================================
# CONFIGURATION
# =====================================================
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = "SmarterRAG99!"
AWS_REGION = "eu-west-1"

# Modèles
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
LLM_MODEL = "eu.anthropic.claude-sonnet-4-6"  # Claude Sonnet 4.6 via Bedrock

# Paramètres de recherche
TOP_K = 10                    # Nombre de chunks à récupérer
SIMILARITY_THRESHOLD = 0.15   # Seuil minimum de similarité

# =====================================================
# Clients AWS
# =====================================================
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
conn = psycopg2.connect(
    host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
    user=DB_USER, password=DB_PASSWORD
)

# =====================================================
# Dictionnaire thématique (même que étape 4)
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

def detect_query_themes(query):
    """Détecte les thèmes pertinents dans la question de l'utilisateur."""
    query_lower = query.lower()
    matched = []
    for theme, keywords in THEMES_KEYWORDS.items():
        if any(kw in query_lower for kw in keywords):
            matched.append(theme)
    return matched

def get_embedding(text):
    """Obtient l'embedding d'un texte via Bedrock Titan."""
    body = json.dumps({
        "inputText": text,
        "dimensions": 1024,
        "normalize": True
    })
    response = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL, body=body,
        contentType="application/json", accept="application/json"
    )
    return json.loads(response["body"].read())["embedding"]

def search_chunks(query, copropriete=None):
    """Recherche hybride : similarité vectorielle + filtrage thématique."""
    # 1. Embedding de la requête
    query_embedding = get_embedding(query)
    
    # 2. Détecter les thèmes
    themes = detect_query_themes(query)
    
    # 3. Construire la requête SQL hybride
    cur = conn.cursor()
    
    if themes and copropriete:
        # Filtrage par thème ET copropriété + tri par similarité
        cur.execute("""
            SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                   themes, text,
                   1 - (embedding <=> %s::vector) as similarity
            FROM chunks
            WHERE copropriete = %s
              AND themes && %s::text[]
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """, (str(query_embedding), copropriete, themes, str(query_embedding), TOP_K))
    
    elif themes:
        # Filtrage par thème + tri par similarité
        cur.execute("""
            SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                   themes, text,
                   1 - (embedding <=> %s::vector) as similarity
            FROM chunks
            WHERE themes && %s::text[]
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """, (str(query_embedding), themes, str(query_embedding), TOP_K))
    
    elif copropriete:
        # Filtrage par copropriété seule
        cur.execute("""
            SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                   themes, text,
                   1 - (embedding <=> %s::vector) as similarity
            FROM chunks
            WHERE copropriete = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """, (str(query_embedding), copropriete, str(query_embedding), TOP_K))
    
    else:
        # Recherche pure similarité sur tout le corpus
        cur.execute("""
            SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
                   themes, text,
                   1 - (embedding <=> %s::vector) as similarity
            FROM chunks
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """, (str(query_embedding), str(query_embedding), TOP_K))
    
    results = cur.fetchall()
    cur.close()
    
    # Filtrer par seuil de similarité
    filtered = [r for r in results if r[7] >= SIMILARITY_THRESHOLD]
    
    return filtered, themes

def generate_answer(query, search_results, themes):
    """Génère une réponse synthétisée via Claude sur Bedrock."""
    # Construire le contexte
    context_parts = []
    for i, result in enumerate(search_results):
        chunk_id, copro, source, filename, doc_type, chunk_themes, text, similarity = result
        context_parts.append(
            f"[Source {i+1}] Copropriété: {copro} | Fichier: {filename} | "
            f"Type: {doc_type} | Thèmes: {', '.join(chunk_themes) if chunk_themes else 'N/A'} | "
            f"Pertinence: {similarity:.2f}\n{text}"
        )
    
    context = "\n\n---\n\n".join(context_parts)
    
    # Prompt pour Claude
    system_prompt = """Tu es un assistant expert en gestion de copropriété pour un syndic professionnel.
Tu réponds aux questions en te basant UNIQUEMENT sur les extraits de documents fournis.

Règles :
- Cite toujours les sources (numéro de source, nom du fichier, article si applicable)
- Si l'information n'est pas dans les extraits, dis-le clairement
- Croise les informations entre les différents documents quand c'est pertinent
- Utilise un langage professionnel adapté au métier de syndic
- Si la question porte sur plusieurs thèmes, structure ta réponse par thème"""

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
# Boucle interactive
# =====================================================
print("=" * 60)
print("  BUILDINGCOPILOT RAG — Prototype de requête")
print("  Pose tes questions sur les copropriétés")
print("=" * 60)

# Lister les copropriétés disponibles
cur = conn.cursor()
cur.execute("SELECT DISTINCT copropriete, COUNT(*) FROM chunks GROUP BY copropriete ORDER BY copropriete;")
copros = cur.fetchall()
cur.close()

print(f"\n📁 Copropriétés indexées :")
for copro, count in copros:
    print(f"   {copro} ({count} chunks)")

print(f"\n💡 Astuce : préfixe ta question avec [NOM_COPRO] pour filtrer.")
print(f"   Exemple : [Résidence des Lilas] Quels sont les articles sur les charges spéciales ?")
print(f"   Ou pose une question sans préfixe pour chercher dans toutes les copros.")
print(f"\nTape 'quit' pour quitter.\n")

while True:
    query = input("🔍 Ta question : ").strip()
    
    if query.lower() in ("quit", "exit", "q"):
        break
    
    if not query:
        continue
    
    # Extraire le filtre copropriété si présent
    copro_filter = None
    copro_match = re.match(r'\[(.+?)\]\s*(.*)', query)
    if copro_match:
        copro_filter = copro_match.group(1)
        query = copro_match.group(2)
    
    print(f"\n⏳ Recherche en cours...")
    
    # Recherche
    results, themes = search_chunks(query, copro_filter)
    
    if not results:
        print("❌ Aucun résultat trouvé. Essaie de reformuler ta question.\n")
        continue
    
    print(f"   → {len(results)} chunks pertinents trouvés")
    if themes:
        print(f"   → Thèmes détectés : {', '.join(themes)}")
    
    # Générer la réponse
    print(f"⏳ Génération de la réponse...\n")
    answer = generate_answer(query, results, themes)
    
    print("─" * 60)
    print(answer)
    print("─" * 60)
    
    # Afficher les sources
    print(f"\n📎 Sources utilisées :")
    for i, result in enumerate(results):
        _, copro, source, filename, doc_type, _, _, similarity = result
        print(f"   [{i+1}] {filename} ({doc_type}) — {copro} — pertinence: {similarity:.2f}")
    
    print()

conn.close()
print("👋 À bientôt !")
