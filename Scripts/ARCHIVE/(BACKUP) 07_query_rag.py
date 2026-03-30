"""
ÉTAPE 7 — Interface de requête RAG (v2 — recherche hybride corrigée)
=====================================================================
Corrections par rapport à la v1 :
  - Seuil de similarité abaissé à 0.15 (texte OCR = scores plus bas)
  - Filtrage thématique remplacé par un BOOST (+0.05) — ne cache plus les
    chunks pertinents dont le thème n'a pas été détecté par mots-clés
  - TOP_K augmenté à 15 pour couvrir les gros documents (RCP 220 pages)
  - Requête SQL unifiée (plus de 4 branches if/elif)
  - Filtrage par type de document (doc_type) ajouté comme option
  - Déduplication des chunks quasi-identiques (doublons d'ingestion)

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
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"  # ← MODIFIER
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = "SmarterRAG99!"  # ← MODIFIER
AWS_REGION = "eu-west-1"

# Modèles
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
LLM_MODEL = "eu.anthropic.claude-sonnet-4-6"  # Claude Sonnet via Bedrock

# Paramètres de recherche
TOP_K = 15                    # Nombre de chunks à récupérer (augmenté pour gros docs)
SIMILARITY_THRESHOLD = 0.15   # Seuil minimum (abaissé : OCR = scores plus bas)
THEME_BOOST = 0.05            # Bonus de score pour les chunks dont le thème matche

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

# Mots-clés pour détecter le type de document demandé dans la question
DOC_TYPE_KEYWORDS = {
    "RCP": ["règlement de copropriété", "reglement de copropriete", "rcp", "règlement", "reglement"],
    "PV_AG": ["pv", "procès-verbal", "proces-verbal", "assemblée générale", "ag"],
    "CONTRAT": ["contrat", "mandat"],
    "BUDGET": ["budget", "appel de fonds"],
    "DIAGNOSTIC": ["diagnostic", "dpe", "amiante"],
}


def detect_query_themes(query):
    """Détecte les thèmes pertinents dans la question de l'utilisateur."""
    query_lower = query.lower()
    matched = []
    for theme, keywords in THEMES_KEYWORDS.items():
        if any(kw in query_lower for kw in keywords):
            matched.append(theme)
    return matched


def detect_doc_type_hint(query):
    """Détecte si la question cible un type de document spécifique."""
    query_lower = query.lower()
    for doc_type, keywords in DOC_TYPE_KEYWORDS.items():
        if any(kw in query_lower for kw in keywords):
            return doc_type
    return None


def get_embedding(text):
    """Obtient l'embedding d'un texte via Bedrock Titan."""
    if len(text) > 5000:
        text = text[:5000]
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
    """
    Recherche hybride : similarité vectorielle + boost thématique.

    Le boost thématique ajoute THEME_BOOST au score de similarité quand le
    chunk a un thème en commun avec la question. Mais il ne FILTRE PAS —
    un chunk très pertinent sans le bon tag thématique remonte quand même.
    """
    # 1. Embedding de la requête
    query_embedding = get_embedding(query)

    # 2. Détecter les thèmes et le type de document
    themes = detect_query_themes(query)
    doc_type_hint = detect_doc_type_hint(query)

    # 3. Requête SQL unifiée — boost thématique, pas filtre
    cur = conn.cursor()

    # Construire les clauses WHERE optionnelles
    where_clauses = []
    params_before = []  # Paramètres avant ORDER BY
    params_order = []   # Paramètres dans ORDER BY

    if copropriete:
        where_clauses.append("copropriete = %s")
        params_before.append(copropriete)

    if doc_type_hint:
        where_clauses.append("doc_type = %s")
        params_before.append(doc_type_hint)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Le score final = similarité cosinus + boost si thème matche
    # On récupère TOP_K * 2 candidats pour avoir de la marge après dédup
    sql = f"""
        SELECT chunk_id, copropriete, source_file, nom_fichier, doc_type,
               themes, text,
               1 - (embedding <=> %s::vector) as similarity,
               CASE WHEN themes && %s::text[] THEN {THEME_BOOST} ELSE 0 END as theme_boost
        FROM chunks
        {where_sql}
        ORDER BY (1 - (embedding <=> %s::vector))
                 + CASE WHEN themes && %s::text[] THEN {THEME_BOOST} ELSE 0 END DESC
        LIMIT %s
    """

    params = [
        str(query_embedding),       # pour similarity
        themes if themes else [],   # pour theme_boost CASE
        *params_before,             # WHERE clauses
        str(query_embedding),       # pour ORDER BY similarity
        themes if themes else [],   # pour ORDER BY theme_boost
        TOP_K * 2,                  # LIMIT (marge pour dédup)
    ]

    cur.execute(sql, params)
    raw_results = cur.fetchall()
    cur.close()

    # 4. Filtrer par seuil de similarité (colonne 7 = similarity)
    filtered = [r for r in raw_results if r[7] >= SIMILARITY_THRESHOLD]

    # 5. Dédupliquer — supprimer les chunks au texte quasi-identique
    #    (doublons d'ingestion issus de runs successifs)
    seen_texts = set()
    deduped = []
    for r in filtered:
        # Signature = 100 premiers caractères du texte (suffisant pour dédup)
        text_sig = r[6][:100].strip()
        if text_sig not in seen_texts:
            seen_texts.add(text_sig)
            deduped.append(r)

    # 6. Tronquer à TOP_K après dédup
    final = deduped[:TOP_K]

    return final, themes, doc_type_hint


def generate_answer(query, search_results, themes, doc_type_hint):
    """Génère une réponse synthétisée via Claude sur Bedrock."""
    if not search_results:
        return "Je n'ai trouvé aucun document pertinent pour répondre à cette question."

    # Construire le contexte
    context_parts = []
    for i, result in enumerate(search_results):
        chunk_id, copro, source, filename, doc_type, chunk_themes, text, similarity, theme_boost = result
        score_total = similarity + theme_boost
        context_parts.append(
            f"[Source {i+1}] Copropriété: {copro} | Fichier: {filename} | "
            f"Type: {doc_type} | Thèmes: {', '.join(chunk_themes) if chunk_themes else 'N/A'} | "
            f"Pertinence: {score_total:.2f}\n{text}"
        )

    context = "\n\n---\n\n".join(context_parts)

    # Prompt pour Claude
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
   cohérents entre les sources avant de les affirmer"""

    # Construire des indices contextuels pour Claude
    context_hints = []
    if themes:
        context_hints.append(f"Thèmes détectés : {', '.join(themes)}")
    if doc_type_hint:
        context_hints.append(f"Type de document ciblé : {doc_type_hint}")
    hints_text = "\n".join(context_hints) if context_hints else "Aucun filtre spécifique"

    user_prompt = f"""Question : {query}

{hints_text}

Voici les {len(search_results)} extraits de documents les plus pertinents :

{context}

Réponds de manière structurée et précise en citant les sources."""

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 3000,
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
print("  BUILDINGCOPILOT RAG — Prototype de requête (v2)")
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

# Stats par type de document
cur = conn.cursor()
cur.execute("SELECT doc_type, COUNT(*) FROM chunks GROUP BY doc_type ORDER BY COUNT(*) DESC;")
doc_types = cur.fetchall()
cur.close()

print(f"\n📄 Types de documents :")
for dt, count in doc_types:
    print(f"   {dt:15s} : {count:5d} chunks")

print(f"\n💡 Commandes :")
print(f"   [NOM_COPRO] ta question   → filtre par copropriété")
print(f"   Exemple : [5390 - 2-6 BIS HENRI TARIEL (QUENTIN)] Quels sont les articles sur les charges ?")
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
    results, themes, doc_type_hint = search_chunks(query, copro_filter)

    if not results:
        print("❌ Aucun résultat pertinent trouvé. Essaie de reformuler ta question.\n")
        continue

    print(f"   → {len(results)} chunks pertinents trouvés")
    if themes:
        print(f"   → Thèmes détectés : {', '.join(themes)}")
    if doc_type_hint:
        print(f"   → Type de document ciblé : {doc_type_hint}")

    # Afficher les scores pour debug
    print(f"   → Scores : {results[0][7]:.3f} (max) → {results[-1][7]:.3f} (min)")

    # Générer la réponse
    print(f"⏳ Génération de la réponse...\n")
    answer = generate_answer(query, results, themes, doc_type_hint)

    print("─" * 60)
    print(answer)
    print("─" * 60)

    # Afficher les sources
    print(f"\n📎 Sources utilisées :")
    for i, result in enumerate(results):
        _, copro, source, filename, doc_type, _, _, similarity, theme_boost = result
        score = similarity + theme_boost
        boost_indicator = " +🏷️" if theme_boost > 0 else ""
        print(f"   [{i+1}] {filename} ({doc_type}) — {copro} — score: {score:.3f}{boost_indicator}")

    print()

conn.close()
print("👋 À bientôt !")
