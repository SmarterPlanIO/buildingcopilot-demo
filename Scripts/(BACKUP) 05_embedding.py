"""
ÉTAPE 5 — Génération des embeddings via Amazon Bedrock Titan (VERSION RÉSUMABLE)
Lance : python 05_embedding.py
"""
import os
import json
import boto3
import time
from tqdm import tqdm

# =====================================================
# CONFIGURATION
# =====================================================
INPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_enrichis.jsonl"
OUTPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_avec_embeddings.jsonl"
AWS_REGION = "eu-west-1"

# Bedrock client
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

# Modèle d'embedding
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSION = 1024  # Titan V2 supporte 256, 512, 1024

# Contrôle de débit (Bedrock a des limites de requêtes/seconde)
BATCH_PAUSE = 0.05  # 50ms entre chaque appel — ajuster si erreurs throttling

def get_embedding(text):
    """Appelle Bedrock Titan pour obtenir l'embedding d'un texte."""
    # Troncature conservative pour Titan V2 (max 8192 tokens)
    # Le ratio token/char varie fortement en français juridique (accents,
    # numéros de lots, tantièmes, caractères spéciaux).
    # 5000 chars est une limite sûre (~1200-2500 tokens selon le contenu).
    MAX_CHARS = 5000
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    
    body = json.dumps({
        "inputText": text,
        "dimensions": EMBEDDING_DIMENSION,
        "normalize": True
    })
    
    try:
        response = bedrock.invoke_model(
            modelId=EMBEDDING_MODEL,
            body=body,
            contentType="application/json",
            accept="application/json"
        )
        result = json.loads(response["body"].read())
        return result["embedding"]
    
    except Exception as e:
        if "Too many input" in str(e) or "ValidationException" in str(e):
            # Troncature progressive : réduire de 30% et réessayer
            shorter = text[:int(len(text) * 0.7)]
            body = json.dumps({
                "inputText": shorter,
                "dimensions": EMBEDDING_DIMENSION,
                "normalize": True
            })
            response = bedrock.invoke_model(
                modelId=EMBEDDING_MODEL,
                body=body,
                contentType="application/json",
                accept="application/json"
            )
            result = json.loads(response["body"].read())
            return result["embedding"]
        raise  # Re-raise les autres erreurs

# =====================================================
# Exécution
# =====================================================
print("=" * 50)
print("GÉNÉRATION DES EMBEDDINGS — BEDROCK TITAN V2")
print("=" * 50)

# 1. Charger les IDs déjà traités pour pouvoir reprendre
processed_ids = set()
if os.path.exists(OUTPUT_FILE):
    print(f"Chargement des chunks déjà traités...")
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
                processed_ids.add(data["chunk_id"])
            except:
                continue
    print(f"-> {len(processed_ids)} chunks déjà prêts. On reprend la suite.")

# 2. Vérifier si le fichier source existe
if not os.path.exists(INPUT_FILE):
    print(f"❌ Le fichier {INPUT_FILE} n'existe pas. Lance d'abord l'étape 04.")
    import sys
    sys.exit(1)

# 3. Compter le total d'entrées
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    total_inputs = sum(1 for _ in f)

print(f"\nTotal à traiter : {total_inputs}")
print(f"Déjà faits     : {len(processed_ids)}")
print(f"Restants       : {total_inputs - len(processed_ids)}")
print(f"Coût estimé restants : ~${(total_inputs - len(processed_ids)) * 0.00002:.2f}\n")

# 4. Traiter uniquement les manquants
errors = 0
# Mode 'a' (append) pour ne pas effacer l'existant
with open(INPUT_FILE, "r", encoding="utf-8") as fin, \
     open(OUTPUT_FILE, "a", encoding="utf-8") as fout:
    
    for line in tqdm(fin, total=total_inputs, desc="Embedding"):
        chunk = json.loads(line)
        
        # Sauter si déjà fait
        if chunk["chunk_id"] in processed_ids:
            continue
        
        try:
            embedding = get_embedding(chunk["text"])
            chunk["embedding"] = embedding
            fout.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            fout.flush() # Force l'écriture sur le disque
        except Exception as e:
            if "ThrottlingException" in str(e):
                time.sleep(2)  # Attendre si throttled
                try:
                    embedding = get_embedding(chunk["text"])
                    chunk["embedding"] = embedding
                    fout.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                    fout.flush()
                except:
                    errors += 1
            else:
                print(f"  ⚠️ Erreur: {e}")
                errors += 1
        
        time.sleep(BATCH_PAUSE)

print(f"\n✅ Terminé. Erreurs : {errors}")
print(f"📁 Chunks avec embeddings : {OUTPUT_FILE}")
