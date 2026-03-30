"""
ÉTAPE 3 — Chunking intelligent adapté aux documents de copropriété
Lance : python 03_chunking.py
"""
import os
import re
import json
import hashlib
from pathlib import Path
from tqdm import tqdm

# =====================================================
# CONFIGURATION
# =====================================================
EXTRACTED_DIR = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\Archives_Extraites"  # ← MODIFIER
OUTPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_copro.jsonl"     # ← MODIFIER

# Taille cible des chunks (en caractères)
CHUNK_TARGET_SIZE = 1500    # ~375 tokens français
CHUNK_MAX_SIZE = 3000       # Maximum souple (articles/résolutions)
CHUNK_HARD_MAX = 5000       # Maximum ABSOLU — compatible Titan V2 (8192 tokens)
CHUNK_OVERLAP = 200         # Chevauchement entre chunks

# =====================================================
# Détection du type de document par nom/chemin
# =====================================================
def detect_doc_type(filepath, filename):
    """Identifie le type de document de copropriété."""
    path_lower = (filepath + "/" + filename).lower()
    
    if any(kw in path_lower for kw in ["règlement", "reglement", "rcp", "regl_copro"]):
        return "RCP"
    elif any(kw in path_lower for kw in ["pv", "procès-verbal", "proces_verbal", "assembl"]):
        return "PV_AG"
    elif any(kw in path_lower for kw in ["contrat", "mandat", "convention"]):
        return "CONTRAT"
    elif any(kw in path_lower for kw in ["devis"]):
        return "DEVIS"
    elif any(kw in path_lower for kw in ["facture", "fact_", "fac_"]):
        return "FACTURE"
    elif any(kw in path_lower for kw in ["budget", "appel_fond", "appel de fond", "répartition", "repartition"]):
        return "BUDGET"
    elif any(kw in path_lower for kw in ["diagnostic", "dpe", "amiante", "plomb", "termite"]):
        return "DIAGNOSTIC"
    elif any(kw in path_lower for kw in ["courrier", "lettre", "lrar", "mise_en_demeure"]):
        return "COURRIER"
    elif any(kw in path_lower for kw in ["plan", "pln", "archi"]):
        return "PLAN"
    elif any(kw in path_lower for kw in ["assurance", "police", "sinistre"]):
        return "ASSURANCE"
    else:
        return "AUTRE"

# =====================================================
# Fonctions de chunking par type
# =====================================================

def split_long_paragraph(text, max_size):
    """Découpe un long paragraphe en respectant les frontières de phrases."""
    # Chercher les points de coupure naturels (phrases)
    sentence_ends = list(re.finditer(r'[.!?]\s+', text))
    
    if not sentence_ends:
        # Pas de phrases détectées -> découpe brute avec overlap
        pieces = []
        for i in range(0, len(text), max_size - CHUNK_OVERLAP):
            pieces.append(text[i:i + max_size])
        return pieces
    
    pieces = []
    start = 0
    current_end = 0
    
    for match in sentence_ends:
        candidate_end = match.end()
        if candidate_end - start <= max_size:
            current_end = candidate_end
        else:
            # On a dépassé la taille max -> sauver le chunk jusqu'au dernier point valide
            if current_end > start:
                pieces.append(text[start:current_end].strip())
                start = current_end
                current_end = candidate_end
            else:
                # Phrase unique trop longue -> découpe brute
                pieces.append(text[start:start + max_size].strip())
                start = start + max_size - CHUNK_OVERLAP
                current_end = candidate_end
    
    # Dernier morceau
    if start < len(text):
        remaining = text[start:].strip()
        if remaining:
            pieces.append(remaining)
    
    return pieces


def enforce_max_size(chunks):
    """Garde-fou final : garantit qu'AUCUN chunk ne dépasse CHUNK_HARD_MAX."""
    safe_chunks = []
    for chunk in chunks:
        if len(chunk) <= CHUNK_HARD_MAX:
            safe_chunks.append(chunk)
        else:
            # Tenter un découpage intelligent par phrases
            pieces = split_long_paragraph(chunk, CHUNK_HARD_MAX)
            for piece in pieces:
                if len(piece) <= CHUNK_HARD_MAX:
                    safe_chunks.append(piece)
                else:
                    # Dernier recours : découpe brute absolue
                    for i in range(0, len(piece), CHUNK_HARD_MAX):
                        safe_chunks.append(piece[i:i + CHUNK_HARD_MAX])
    return safe_chunks


def chunk_by_articles(text):
    """Découpe un RCP ou contrat par articles."""
    # Pattern pour détecter les articles
    pattern = r'(?=(?:^|\n)\s*(?:Article|ARTICLE|Art\.?)\s*\d+)'
    parts = re.split(pattern, text)
    
    chunks = []
    for part in parts:
        part = part.strip()
        if len(part) < 20:
            continue
        if len(part) <= CHUNK_MAX_SIZE:
            chunks.append(part)
        else:
            # Article trop long -> découpe par paragraphes avec overlap
            chunks.extend(chunk_by_size(part))
    
    return enforce_max_size(chunks) if chunks else chunk_by_size(text)

def chunk_by_resolutions(text):
    """Découpe un PV d'AG par résolutions."""
    pattern = r'(?=(?:^|\n)\s*(?:Résolution|RÉSOLUTION|Resolution|RESOLUTION)\s*(?:N°|n°|#)?\s*\d+)'
    parts = re.split(pattern, text)
    
    chunks = []
    for part in parts:
        part = part.strip()
        if len(part) < 20:
            continue
        if len(part) <= CHUNK_MAX_SIZE:
            chunks.append(part)
        else:
            chunks.extend(chunk_by_size(part))
    
    return enforce_max_size(chunks) if chunks else chunk_by_size(text)

def chunk_by_size(text):
    """Découpe générique par taille avec overlap — pour documents courts ou sans structure."""
    if len(text) <= CHUNK_HARD_MAX:
        return [text]
    
    chunks = []
    # Découper par paragraphes d'abord
    paragraphs = text.split("\n\n")
    
    current_chunk = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        if len(current_chunk) + len(para) + 2 <= CHUNK_TARGET_SIZE:
            current_chunk += "\n\n" + para if current_chunk else para
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
                # Overlap : garder les derniers N caractères
                overlap = current_chunk[-CHUNK_OVERLAP:] if len(current_chunk) > CHUNK_OVERLAP else ""
                current_chunk = overlap + "\n\n" + para
            else:
                # Paragraphe seul trop long -> découpe par phrases
                chunks.extend(split_long_paragraph(para, CHUNK_TARGET_SIZE))
                current_chunk = ""
        
        # Sécurité : si le chunk en cours dépasse CHUNK_HARD_MAX, le flusher
        if len(current_chunk) > CHUNK_HARD_MAX:
            chunks.append(current_chunk.strip())
            current_chunk = ""
    
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    # Garde-fou final
    return enforce_max_size(chunks)

def chunk_whole_document(text):
    """Garde le document entier si assez court, sinon découpe par taille."""
    if len(text) <= CHUNK_HARD_MAX:
        return [text]
    return chunk_by_size(text)

# Mapping type -> stratégie
CHUNKING_STRATEGY = {
    "RCP": chunk_by_articles,
    "PV_AG": chunk_by_resolutions,
    "CONTRAT": chunk_by_articles,
    "DEVIS": chunk_whole_document,
    "FACTURE": chunk_whole_document,
    "BUDGET": chunk_whole_document,
    "DIAGNOSTIC": chunk_by_size,
    "COURRIER": chunk_whole_document,
    "PLAN": chunk_whole_document,
    "ASSURANCE": chunk_by_articles,
    "AUTRE": chunk_by_size,
}

# =====================================================
# Exécution
# =====================================================
# Nettoyage de l'ancien fichier de chunks
if os.path.exists(OUTPUT_FILE):
    print(f"Nettoyage de l'ancien fichier : {OUTPUT_FILE}")
    os.remove(OUTPUT_FILE)

print("=" * 50)
print("CHUNKING INTELLIGENT DES DOCUMENTS")
print("=" * 50)

# Charger tous les fichiers JSON extraits
json_files = []
if os.path.exists(EXTRACTED_DIR):
    for root, dirs, filenames in os.walk(EXTRACTED_DIR):
        for fname in filenames:
            if fname.endswith(".json"):
                json_files.append(os.path.join(root, fname))
else:
    print(f"Le dossier {EXTRACTED_DIR} n'existe pas. Lance d'abord l'etape 02.")
    import sys
    sys.exit(1)

print(f"\n{len(json_files)} fichiers a chunker\n")

total_chunks = 0
doc_type_stats = {}

with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
    for json_path in tqdm(json_files, desc="Chunking"):
        with open(json_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        
        text = doc.get("texte", "")
        if len(text.strip()) < 30:
            continue
        
        # Détecter le type de document
        doc_type = detect_doc_type(doc.get("dossier_parent", ""), doc.get("nom_fichier", ""))
        doc_type_stats[doc_type] = doc_type_stats.get(doc_type, 0) + 1
        
        # Appliquer la stratégie de chunking
        chunker = CHUNKING_STRATEGY[doc_type]
        chunks = chunker(text)
        
        # Écrire chaque chunk avec ses métadonnées
        for i, chunk_text in enumerate(chunks):
            chunk_id = hashlib.md5(f"{doc['source_file']}_{i}".encode()).hexdigest()[:12]
            
            chunk_record = {
                "chunk_id": chunk_id,
                "copropriete": doc.get("copropriete", ""),
                "source_file": doc.get("source_file", ""),
                "nom_fichier": doc.get("nom_fichier", ""),
                "doc_type": doc_type,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "text": chunk_text,
                "nb_caracteres": len(chunk_text)
            }
            
            out.write(json.dumps(chunk_record, ensure_ascii=False) + "\n")
            total_chunks += 1

# =====================================================
# Rapport
# =====================================================
print("\n" + "=" * 50)
print("RAPPORT DE CHUNKING")
print("=" * 50)
print(f"\nTotal chunks generes : {total_chunks}")
print(f"\nPar type de document :")
for doc_type, count in sorted(doc_type_stats.items(), key=lambda x: -x[1]):
    print(f"  {doc_type:20s} : {count:5d} documents")
print(f"\n-> Chunks : {OUTPUT_FILE}")
