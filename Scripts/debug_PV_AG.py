"""diagnostic_pv_manquants.py — Trace le pipeline pour les PV d'AG manquants"""
import os
import json
import psycopg2

# =====================================================
# CONFIGURATION — Valeurs du guide RAG
# =====================================================
BASE_DIR = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts"

EXTRACTED_DIR = os.path.join(BASE_DIR, "Archives_Extraites")
CHUNKS_FILE = os.path.join(BASE_DIR, "chunks_copro.jsonl")
ENRICHED_FILE = os.path.join(BASE_DIR, "chunks_enrichis.jsonl")
EMBEDDINGS_FILE = os.path.join(BASE_DIR, "chunks_avec_embeddings.jsonl")

DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"  # ← MODIFIER
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = "SmarterRAG99!"  # ← MODIFIER

YEARS_MISSING = ["2016", "2021", "2022", "2023", "2024"]

PV_KEYWORDS = ["pv", "proc", "verbal", "assembl", "ag"]

def matches_pv(filename):
    name_lower = filename.lower()
    return any(kw in name_lower for kw in PV_KEYWORDS)

# =====================================================
# PHASE 1 : Fichiers .json d'extraction (étape 2)
# =====================================================
print("=" * 70)
print("PHASE 1 — Fichiers .json d'extraction (étape 2)")
print("=" * 70)

extraction_pv_files = []
for root, dirs, files in os.walk(EXTRACTED_DIR):
    for fname in files:
        if fname.endswith(".json") and matches_pv(fname):
            filepath = os.path.join(root, fname)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                source = data.get("source_file", fname)
                nb_chars = data.get("nb_caracteres", len(data.get("texte", "")))
                extraction_pv_files.append((source, nb_chars, filepath))
            except:
                extraction_pv_files.append((fname, -1, filepath))

print(f"\n{len(extraction_pv_files)} fichier(s) PV trouvés dans l'extraction :\n")
for source, nb_chars, path in sorted(extraction_pv_files):
    year_flags = " ".join(
        f"[{y}]" for y in YEARS_MISSING
        if y in source or y in os.path.basename(path)
    )
    status = f"{nb_chars:,} chars" if nb_chars > 0 else "⚠️ ERREUR LECTURE"
    print(f"  {source} — {status} {year_flags}")

# =====================================================
# PHASE 2 : Présence dans chunks_copro.jsonl (étape 3)
# =====================================================
print("\n" + "=" * 70)
print("PHASE 2 — Chunks après chunking (étape 3)")
print("=" * 70)

def scan_jsonl_for_pv(filepath, label):
    if not os.path.exists(filepath):
        print(f"\n  ❌ Fichier introuvable : {filepath}")
        return {}
    
    pv_sources = {}
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            try:
                chunk = json.loads(line)
            except:
                continue
            source = chunk.get("source_file", "")
            nom = chunk.get("nom_fichier", "")
            if matches_pv(source) or matches_pv(nom):
                key = source or nom
                if key not in pv_sources:
                    pv_sources[key] = {
                        "count": 0,
                        "doc_type": chunk.get("doc_type", "?"),
                        "chunk_ids": []
                    }
                pv_sources[key]["count"] += 1
                cid = chunk.get("chunk_id", "")
                if cid:
                    pv_sources[key]["chunk_ids"].append(cid)
    
    print(f"\n{len(pv_sources)} fichier(s) PV dans {label} :\n")
    for source, info in sorted(pv_sources.items()):
        year_flags = " ".join(f"[{y}]" for y in YEARS_MISSING if y in source)
        print(f"  {source}")
        print(f"    doc_type={info['doc_type']}  chunks={info['count']}  {year_flags}")
    
    return pv_sources

chunks_pv = scan_jsonl_for_pv(CHUNKS_FILE, "chunks_copro.jsonl (étape 3)")

# =====================================================
# PHASE 3 : Présence dans chunks_enrichis.jsonl (étape 4)
# =====================================================
print("\n" + "=" * 70)
print("PHASE 3 — Chunks après enrichissement (étape 4)")
print("=" * 70)

enriched_pv = scan_jsonl_for_pv(ENRICHED_FILE, "chunks_enrichis.jsonl (étape 4)")

# =====================================================
# PHASE 4 : Présence dans chunks_avec_embeddings.jsonl (étape 5)
# =====================================================
print("\n" + "=" * 70)
print("PHASE 4 — Chunks après embedding (étape 5)")
print("=" * 70)

embedded_pv = scan_jsonl_for_pv(EMBEDDINGS_FILE, "chunks_avec_embeddings.jsonl (étape 5)")

# =====================================================
# PHASE 5 : Présence dans PostgreSQL (étape 6)
# =====================================================
print("\n" + "=" * 70)
print("PHASE 5 — Chunks dans PostgreSQL (étape 6)")
print("=" * 70)

try:
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                            user=DB_USER, password=DB_PASSWORD)
    cur = conn.cursor()

    cur.execute("""
        SELECT source_file, doc_type, COUNT(*) as nb_chunks
        FROM chunks
        WHERE nom_fichier ILIKE '%%pv%%'
           OR nom_fichier ILIKE '%%assembl%%'
           OR nom_fichier ILIKE '%%proc%%verbal%%'
           OR source_file ILIKE '%%pv%%'
           OR source_file ILIKE '%%assembl%%'
        GROUP BY source_file, doc_type
        ORDER BY source_file;
    """)
    db_results = cur.fetchall()

    print(f"\n{len(db_results)} fichier(s) PV dans PostgreSQL :\n")
    for source, doc_type, count in db_results:
        year_flags = " ".join(f"[{y}]" for y in YEARS_MISSING if y in source)
        print(f"  [{doc_type:12s}] {source} — {count} chunks {year_flags}")

    print("\n--- Recherche par contenu texte (filet de sécurité) ---\n")
    for year in YEARS_MISSING:
        cur.execute("""
            SELECT DISTINCT source_file, doc_type
            FROM chunks
            WHERE (text ILIKE '%%assemblée générale%%' OR text ILIKE '%%procès-verbal%%')
              AND text LIKE %s
        """, (f"%{year}%",))
        rows = cur.fetchall()
        label = "✅" if rows else "❌"
        print(f"  {label} {year} : {len(rows)} fichier(s)")
        for src, dt in rows:
            print(f"       → [{dt}] {src}")

    cur.close()
    conn.close()
except Exception as e:
    print(f"\n  ⚠️ Erreur connexion DB : {e}")

# =====================================================
# SYNTHÈSE
# =====================================================
print("\n" + "=" * 70)
print("SYNTHÈSE — Où se perdent les PV ?")
print("=" * 70)

extraction_sources = {s for s, _, _ in extraction_pv_files}
chunks_sources = set(chunks_pv.keys())
enriched_sources = set(enriched_pv.keys())
embedded_sources = set(embedded_pv.keys())

lost_at_chunking = extraction_sources - chunks_sources
lost_at_enrichment = chunks_sources - enriched_sources
lost_at_embedding = enriched_sources - embedded_sources

if lost_at_chunking:
    print(f"\n❌ Perdus au CHUNKING (étape 3) : {len(lost_at_chunking)}")
    for s in sorted(lost_at_chunking):
        print(f"   → {s}")

if lost_at_enrichment:
    print(f"\n❌ Perdus à l'ENRICHISSEMENT (étape 4) : {len(lost_at_enrichment)}")
    for s in sorted(lost_at_enrichment):
        print(f"   → {s}")

if lost_at_embedding:
    print(f"\n❌ Perdus à l'EMBEDDING (étape 5) : {len(lost_at_embedding)}")
    for s in sorted(lost_at_embedding):
        print(f"   → {s}")

if not (lost_at_chunking or lost_at_enrichment or lost_at_embedding):
    print("\n✅ Aucune perte détectée dans le pipeline fichiers.")
    print("   → Le problème est probablement au chargement DB (étape 6)")
    print("   ou au doc_type assigné (filtrage dans l'interface de requête).")