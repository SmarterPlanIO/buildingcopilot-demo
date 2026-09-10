"""
ETAPE 5b - Generation de questions synthetiques via Haiku (Phase 1a)
Usage :
  python 05b_synthetic_questions.py --copro 5033    # Mode per-copro (recommandé)
  python 05b_synthetic_questions.py                  # Mode legacy (chemins hardcodés)

Enrichit chaque chunk eligible avec 3-5 questions dont la reponse est
explicitement dans le texte. Ces questions ameliorent le recall BM25
en ajoutant du vocabulaire metier varie.

Chunks eligibles :
  - doc_type in (PV_AG, RCP, CONTRAT)
  - chunk_index > 0 (pas le preambule)
  - resolution_category not in (PROCEDURE_AG, ELECTION_CS)

Cout estime : ~3500 chunks x $0.0001 = ~$0.35
"""
import os
import json
import argparse
import boto3
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

from pipeline_config import paths_for, RESULTS_ROOT
import bedrock_cost
import sq_cache  # cache versionné A2 (cf. PLAN_05B_QUESTIONS_SYNTHETIQUES.md)

# =====================================================
# CONFIGURATION
# =====================================================
_parser = argparse.ArgumentParser(description="Génération de questions synthétiques d'une copropriété.")
_parser.add_argument("--copro", help="Code NCG de la copropriété (ex: 5033). Si absent, mode legacy.")
_args, _ = _parser.parse_known_args()

if _args.copro:
    _paths = paths_for(_args.copro)
    _paths["per_copro"].mkdir(parents=True, exist_ok=True)
    INPUT_FILE = str(_paths["embeddings_jsonl"])
    OUTPUT_FILE = str(_paths["embeddings_sq_jsonl"])
    CACHE_FILE = str(_paths["per_copro"] / "synthetic_questions_cache.json")
    print(f"📌 Mode per-copro : {_args.copro} ({_paths['folder_name']})")
else:
    INPUT_FILE = str(RESULTS_ROOT / "chunks_avec_embeddings.jsonl")
    OUTPUT_FILE = str(RESULTS_ROOT / "chunks_avec_embeddings_sq.jsonl")
    CACHE_FILE = str(RESULTS_ROOT / "synthetic_questions_cache.json")

AWS_REGION = "eu-west-1"

HAIKU_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"

# Types de documents a enrichir
ELIGIBLE_DOC_TYPES = {"PV_AG", "RCP", "CONTRAT"}
# Categories de resolution a exclure
EXCLUDED_CATEGORIES = {"PROCEDURE_AG", "ELECTION_CS"}

# Parallelisme
MAX_WORKERS = 10
MAX_RETRIES = 3
WRITE_BATCH_SIZE = 50

# =====================================================
# Prompt de generation
# =====================================================
SQ_PROMPT = """Tu es un gestionnaire de copropriete. Lis ce texte extrait d'un document de copropriete et genere 3 a 5 questions auxquelles ce texte repond EXPLICITEMENT.

Regles strictes :
- Chaque question DOIT avoir sa reponse mot pour mot dans le texte
- Utilise le vocabulaire metier copropriete (tantiemes, charges, ravalement, DDE, ascenseur, syndic, etc.)
- Varie les formulations (qui, quand, combien, quel, est-ce que)
- NE genere PAS de question si le texte est un preambule, une liste de presence ou un texte juridique boilerplate
- Si le texte n'a pas assez de contenu informatif, reponds UNIQUEMENT par : SKIP

Texte :
{chunk_text}

Reponds UNIQUEMENT par les questions, une par ligne, sans numerotation."""

# =====================================================
# Client Bedrock thread-safe
# =====================================================
_thread_local = threading.local()

def get_bedrock_client():
    if not hasattr(_thread_local, "client"):
        _thread_local.client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _thread_local.client


def generate_questions(chunk_text, chunk_id):
    """Genere des questions synthetiques pour un chunk. Retourne (chunk_id, questions_str) ou (chunk_id, None)."""
    # Tronquer le texte a 2000 chars (suffisant, evite les tokens overflow)
    text_excerpt = chunk_text[:2000].strip()
    if len(text_excerpt) < 100:
        return chunk_id, None

    prompt = SQ_PROMPT.format(chunk_text=text_excerpt)
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 300,
        "temperature": 0,  # A1 : sortie déterministe à modèle constant (spike validé 26/08)
        "messages": [{"role": "user", "content": prompt}]
    })

    bedrock = get_bedrock_client()

    for attempt in range(MAX_RETRIES):
        try:
            response = bedrock.invoke_model(
                modelId=HAIKU_MODEL, body=body,
                contentType="application/json", accept="application/json"
            )
            result = json.loads(response["body"].read())
            bedrock_cost.track(result)
            answer = result["content"][0]["text"].strip()

            # Si Haiku repond SKIP, pas de questions
            if answer.upper().startswith("SKIP"):
                return chunk_id, None

            # Nettoyer : retirer les lignes vides et la numerotation eventuelle
            lines = [l.strip() for l in answer.split("\n") if l.strip()]
            lines = [l.lstrip("0123456789.-) ") for l in lines]
            lines = [l for l in lines if l.endswith("?") and len(l) > 15]

            if not lines:
                return chunk_id, None

            return chunk_id, " ".join(lines)

        except Exception as e:
            err_str = str(e)
            if "ThrottlingException" in err_str:
                wait = min(2 ** attempt, 15)
                time.sleep(wait)
                continue
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)
                continue
            return chunk_id, None

    return chunk_id, None


def est_eligible(chunk):
    if chunk.get("doc_type", "") not in ELIGIBLE_DOC_TYPES:
        return False
    if chunk.get("chunk_index", 0) == 0:  # pas le préambule
        return False
    if chunk.get("resolution_category") in EXCLUDED_CATEGORIES:
        return False
    return True


# =====================================================
# Execution
# =====================================================
if __name__ == "__main__":
    print("=" * 50)
    print("GENERATION DE QUESTIONS SYNTHETIQUES (Phase 1a)")
    print("=" * 50)

    # Charger tous les chunks
    print(f"\nChargement de {INPUT_FILE}...")
    all_chunks = []
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            all_chunks.append(json.loads(line))

    print(f"  {len(all_chunks)} chunks charges")

    # Identifier les chunks eligibles
    eligible = [chunk for chunk in all_chunks if est_eligible(chunk)]

    print(f"  {len(eligible)} chunks eligibles pour les questions synthetiques")
    print(f"  Pre-estim. grossiere : ~${len(eligible) * 0.0001:.2f} (cout REEL affiche en fin de run)")

    if not eligible:
        print("Aucun chunk eligible. Copie du fichier sans modification.")
        import shutil
        shutil.copy2(INPUT_FILE, OUTPUT_FILE)
        print(f"-> {OUTPUT_FILE}")
        exit(0)

    # Cache A2 : lookup avant tout appel Bedrock ; le refus (SKIP) est caché comme un succès.
    cache = sq_cache.charger(CACHE_FILE)
    if not cache and os.path.exists(OUTPUT_FILE):
        cache = sq_cache.amorcer_depuis_sq(OUTPUT_FILE, est_eligible,
                                           sq_cache.PROMPT_VERSION, HAIKU_MODEL)
        if cache:
            sq_cache.sauver(CACHE_FILE, cache)
            print(f"  Cache amorcé depuis le shard _sq existant : {len(cache)} entrées (seed, zéro appel)")

    questions_map = {}  # chunk_id -> questions_str
    a_generer = []
    stats = {"generated": 0, "skipped": 0, "errors": 0, "cache_hits": 0}
    for chunk in eligible:
        e = cache.get(chunk["chunk_id"])
        if e is not None and sq_cache.valide(e, sq_cache.PROMPT_VERSION, HAIKU_MODEL):
            stats["cache_hits"] += 1
            q = sq_cache.questions_de(e)
            if q:
                questions_map[chunk["chunk_id"]] = q
                stats["generated"] += 1
            else:
                stats["skipped"] += 1
        else:
            a_generer.append(chunk)
    print(f"  Cache : {stats['cache_hits']} hits, {len(a_generer)} à générer")

    print(f"\nGeneration en cours ({MAX_WORKERS} workers)...")
    _depuis_sauvegarde = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for chunk in a_generer:
            future = executor.submit(generate_questions, chunk["text"], chunk["chunk_id"])
            futures[future] = chunk["chunk_id"]

        for future in tqdm(as_completed(futures), total=len(futures), desc="Questions synthetiques"):
            chunk_id, questions = future.result()
            cache[chunk_id] = sq_cache.entree(questions, sq_cache.PROMPT_VERSION, HAIKU_MODEL)
            _depuis_sauvegarde += 1
            if _depuis_sauvegarde >= 1000:  # un run interrompu conserve ses acquis
                sq_cache.sauver(CACHE_FILE, cache)
                _depuis_sauvegarde = 0
            if questions:
                questions_map[chunk_id] = questions
                stats["generated"] += 1
            else:
                stats["skipped"] += 1
    if a_generer:
        sq_cache.sauver(CACHE_FILE, cache)

    # Ecrire le fichier enrichi
    print(f"\nEcriture de {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        for chunk in all_chunks:
            cid = chunk["chunk_id"]
            if cid in questions_map:
                chunk["synthetic_questions"] = questions_map[cid]
            out.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    # Rapport
    print("\n" + "=" * 50)
    print("RAPPORT QUESTIONS SYNTHETIQUES")
    print("=" * 50)
    print(f"  Chunks eligibles     : {len(eligible)}")
    print(f"  Cache hits           : {stats['cache_hits']} (appels évités)")
    print(f"  Questions generees   : {stats['generated']}")
    print(f"  Skips (pas assez)    : {stats['skipped']}")
    print(f"  Taux d'enrichissement: {stats['generated']/max(len(eligible),1)*100:.1f}%")
    print(bedrock_cost.format_line())
    print(f"\n-> {OUTPUT_FILE}")
