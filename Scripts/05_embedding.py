"""
ÉTAPE 5 — Génération des embeddings via Amazon Bedrock Titan (VERSION PARALLÈLE)
Usage :
  python 05_embedding.py --copro 5033    # Mode per-copro (recommandé)
  python 05_embedding.py                  # Mode legacy (chemins hardcodés)

Optimisations vs version séquentielle :
  - 15 workers parallèles (ThreadPoolExecutor) : 5-8x plus rapide
  - Retry exponentiel sur ThrottlingException (pas de sleep fixe)
  - Écriture batch (100 chunks) au lieu de flush à chaque ligne
  - Résumable : reprend où il s'est arrêté
"""
import os
import json
import argparse
import boto3
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

from pipeline_config import paths_for

# =====================================================
# CONFIGURATION
# =====================================================
_parser = argparse.ArgumentParser(description="Génération des embeddings d'une copropriété.")
_parser.add_argument("--copro", help="Code NCG de la copropriété (ex: 5033). Si absent, mode legacy.")
_args, _ = _parser.parse_known_args()

if _args.copro:
    _paths = paths_for(_args.copro)
    _paths["per_copro"].mkdir(parents=True, exist_ok=True)
    INPUT_FILE = str(_paths["chunks_jsonl"])
    OUTPUT_FILE = str(_paths["embeddings_jsonl"])
    print(f"📌 Mode per-copro : {_args.copro} ({_paths['folder_name']})")
else:
    INPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_copro.jsonl"
    OUTPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_avec_embeddings.jsonl"

AWS_REGION = "eu-west-1"

EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSION = 1024

# Parallélisme
MAX_WORKERS = 15        # Workers parallèles — baisser à 10 si beaucoup de ThrottlingException
WRITE_BATCH_SIZE = 100  # Écrire sur disque tous les N chunks
MAX_RETRIES = 5         # Retries par chunk avant abandon

# =====================================================
# Client Bedrock thread-safe (un par thread)
# =====================================================
_thread_local = threading.local()

def get_bedrock_client():
    """Un client boto3 par thread (boto3 clients ne sont pas thread-safe)."""
    if not hasattr(_thread_local, "client"):
        _thread_local.client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _thread_local.client


def get_embedding_with_retry(text, chunk_id):
    """Appelle Bedrock Titan avec retry exponentiel. Retourne (chunk_id, embedding) ou (chunk_id, None)."""
    # Troncature
    MAX_CHARS = 5000
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]

    body = json.dumps({
        "inputText": text,
        "dimensions": EMBEDDING_DIMENSION,
        "normalize": True
    })

    bedrock = get_bedrock_client()

    for attempt in range(MAX_RETRIES):
        try:
            response = bedrock.invoke_model(
                modelId=EMBEDDING_MODEL, body=body,
                contentType="application/json", accept="application/json"
            )
            result = json.loads(response["body"].read())
            return chunk_id, result["embedding"]

        except Exception as e:
            err_str = str(e)

            # Token overflow → troncature progressive
            if "Too many input" in err_str or "ValidationException" in err_str:
                text = text[:int(len(text) * 0.7)]
                body = json.dumps({
                    "inputText": text,
                    "dimensions": EMBEDDING_DIMENSION,
                    "normalize": True
                })
                continue

            # Throttling → backoff exponentiel
            if "ThrottlingException" in err_str:
                wait = min(2 ** attempt, 30)  # 1s, 2s, 4s, 8s, 16s max 30s
                time.sleep(wait)
                continue

            # Autre erreur → retry avec backoff léger
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)
                continue

            return chunk_id, None

    return chunk_id, None


# =====================================================
# Exécution
# =====================================================
print("=" * 60)
print("GÉNÉRATION DES EMBEDDINGS — BEDROCK TITAN V2 (PARALLÈLE)")
print(f"Workers: {MAX_WORKERS} | Batch écriture: {WRITE_BATCH_SIZE}")
print("=" * 60)

# 1. Charger les IDs déjà traités
processed_ids = set()
if os.path.exists(OUTPUT_FILE):
    print("Chargement des chunks déjà traités...")
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
                processed_ids.add(data["chunk_id"])
            except:
                continue
    print(f"  → {len(processed_ids)} chunks déjà prêts.")

# 2. Charger les chunks à traiter
if not os.path.exists(INPUT_FILE):
    print(f"❌ {INPUT_FILE} introuvable.")
    raise SystemExit(1)

chunks_to_process = []
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    for line in f:
        try:
            chunk = json.loads(line)
            if chunk["chunk_id"] not in processed_ids:
                chunks_to_process.append(chunk)
        except:
            continue

total_remaining = len(chunks_to_process)
print(f"Total restants  : {total_remaining}")
print(f"Coût estimé     : ~${total_remaining * 0.00002:.2f}")
print(f"Temps estimé    : ~{total_remaining / MAX_WORKERS / 12:.0f} min\n")

if total_remaining == 0:
    print("✅ Tous les chunks ont déjà un embedding.")
    raise SystemExit(0)

# 3. Traitement parallèle
errors = 0
written = 0
write_buffer = []
write_lock = threading.Lock()

def flush_buffer(fout):
    """Écrit le buffer sur disque et le vide."""
    global write_buffer, written
    with write_lock:
        for chunk_json in write_buffer:
            fout.write(chunk_json + "\n")
        written += len(write_buffer)
        fout.flush()
        write_buffer = []

start_time = time.time()

with open(OUTPUT_FILE, "a", encoding="utf-8") as fout:
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Soumettre tous les jobs
        future_to_chunk = {}
        for chunk in chunks_to_process:
            future = executor.submit(get_embedding_with_retry, chunk["text"], chunk["chunk_id"])
            future_to_chunk[future] = chunk

        # Collecter les résultats au fil de l'eau
        pbar = tqdm(total=total_remaining, desc="Embedding")
        for future in as_completed(future_to_chunk):
            chunk = future_to_chunk[future]
            chunk_id, embedding = future.result()

            if embedding is not None:
                chunk["embedding"] = embedding
                chunk_json = json.dumps(chunk, ensure_ascii=False)
                with write_lock:
                    write_buffer.append(chunk_json)

                # Flush par batch
                if len(write_buffer) >= WRITE_BATCH_SIZE:
                    flush_buffer(fout)
            else:
                errors += 1

            pbar.update(1)

        pbar.close()

        # Flush final
        if write_buffer:
            flush_buffer(fout)

elapsed = time.time() - start_time
rate = (total_remaining - errors) / elapsed if elapsed > 0 else 0

print(f"\n{'=' * 60}")
print(f"✅ Terminé en {elapsed:.0f}s ({elapsed/60:.1f} min)")
print(f"   Chunks traités : {total_remaining - errors}")
print(f"   Erreurs        : {errors}")
print(f"   Débit moyen    : {rate:.1f} chunks/sec")
print(f"📁 {OUTPUT_FILE}")
