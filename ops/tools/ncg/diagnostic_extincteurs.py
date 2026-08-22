"""
Diagnostic RAG — Recherche des chunks extincteurs
Lance : python diagnostic_extincteurs.py
"""
import os
import psycopg2

# =====================================================
# CONFIGURATION — mêmes credentials que 07_query_rag_ui
# =====================================================
DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

# =====================================================
conn = psycopg2.connect(
    host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
    user=DB_USER, password=DB_PASSWORD
)
cur = conn.cursor()

print("=" * 70)
print("DIAGNOSTIC : Chunks liés aux extincteurs")
print("=" * 70)

# Requête 1 — Chercher par nom de fichier ET par contenu
cur.execute("""
    SELECT chunk_id, nom_fichier, doc_type, themes,
           LEFT(text, 500) as extrait
    FROM chunks
    WHERE nom_fichier ILIKE '%%BLOC%%FEU%%'
       OR nom_fichier ILIKE '%%extincteur%%'
       OR text ILIKE '%%extincteur%%'
    ORDER BY nom_fichier, chunk_id
""")

results = cur.fetchall()

if not results:
    print("\n❌ AUCUN chunk trouvé contenant 'extincteur' ou 'BLOC FEU'.")
    print("   → Le fichier n'a probablement pas été indexé, ou l'OCR n'a pas")
    print("     extrait le mot 'extincteur' du scan.")
    
    # Chercher des variantes approximatives
    print("\n🔍 Recherche élargie (termes proches)...")
    cur.execute("""
        SELECT chunk_id, nom_fichier, doc_type, LEFT(text, 300)
        FROM chunks
        WHERE text ILIKE '%%bloc%%feu%%'
           OR text ILIKE '%%incendie%%'
           OR text ILIKE '%%désenfumage%%'
           OR text ILIKE '%%sécurité incendie%%'
           OR text ILIKE '%%vérification annuelle%%'
           OR nom_fichier ILIKE '%%5390%%'
        ORDER BY nom_fichier, chunk_id
    """)
    fallback = cur.fetchall()
    if fallback:
        print(f"\n   Trouvé {len(fallback)} chunk(s) avec termes proches :\n")
        for r in fallback:
            print(f"   📄 {r[1]} ({r[2]})")
            print(f"      {r[3][:200]}...")
            print()
    else:
        print("   Rien trouvé non plus. Le fichier BLOC-FEU n'est pas dans la base.")
else:
    print(f"\n✅ {len(results)} chunk(s) trouvé(s) :\n")
    for r in results:
        chunk_id, filename, doc_type, themes, text = r
        print(f"─── {chunk_id} ───")
        print(f"   📄 Fichier  : {filename}")
        print(f"   📦 Type     : {doc_type}")
        print(f"   🏷️  Thèmes   : {themes}")
        print(f"   📝 Extrait  :")
        print(f"      {text}")
        print()

cur.close()
conn.close()
print("=" * 70)
print("Fin du diagnostic")
