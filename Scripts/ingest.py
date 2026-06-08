"""
ingest.py — Driver d'ingestion incrementale per-copro (Phase 3, scale 150 copros).

Orchestre le pipeline 01..09 pour UNE copro (ou --all), avec REGENERATION COUPLEE
A L'INGESTION des agregats Tier-2, gatee par les doc_types du delta (anti-gaspillage).

Usage :
  DB_PASSWORD=... python ingest.py --copro 8050
  DB_PASSWORD=... python ingest.py --copro 8050 --dry-run     # plan sans execution
  DB_PASSWORD=... AIRTABLE_PAT=... python ingest.py --all
  DB_PASSWORD=... python ingest.py --copro 8050 --keep-shards # ne pas supprimer le shard

Principe (cf. PLAN_SCALE_150_COPROS.md) :
  - Tier-1 (chunks/embeddings/metadata) : etages 01..05b, deja incrementaux (02 checkpoint,
    04 cache, 05 skip embeddes, 05c cache content-addressed). On ne refait que le delta.
  - Tier-2 (agregats) regeneres SSI le delta les concerne :
      * 05c (dossiers sinistres) <=> doc SINISTRE dans le delta.
      * 09  (narratif fiche)     <=> PV_AG ou SINISTRE dans le delta.
    (Les faits/compteurs de la fiche sont live a la lecture, jamais perimes.)
  - 08 (Assynco) relance si la copro avait des dossiers Airtable (06b les a purges).

Garantie "single source of truth" : toute ingestion d'une copro regenere ses agregats
=> la fiche/les dossiers ne peuvent pas etre en retard sur la base.

Suppressions (D du CRUD) : GEREES. 01 rebatit 'filtered' depuis la source vivante ;
le driver retire le JSON extrait des docs disparus -> 03 reconstruit chunks.jsonl sans
eux -> 06b les retire de la DB (et donc du RAG). PALIM ne cite plus un doc supprime.

Limites V1 (TODO, cf. plan) :
  - Modifs sur place (meme nom de fichier, contenu change) non detectees : 02
    checkpoint par CHEMIN, pas par contenu -> le doc n'est pas re-extrait. A traiter
    (hash de contenu dans le checkpoint).
  - 08 pas encore per-copro (lance en global, idempotent).
"""
import argparse
import json
import os
import subprocess
import sys

import psycopg2

import pipeline_config as pcfg

DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT, DB_NAME, DB_USER = 5432, "postgres", "ragadmin"

# doc_types qui declenchent la regeneration de chaque agregat Tier-2.
GATE_05C = {"SINISTRE"}
GATE_09 = {"PV_AG", "SINISTRE"}

TIER1_STAGES = ["01_filtrage.py", "02_extraction_optimized.py", "03_chunking.py",
                "04_metadata_documents.py", "05_embedding.py", "05b_synthetic_questions.py"]


def _db():
    pwd = os.environ.get("DB_PASSWORD")
    if not pwd:
        raise SystemExit("❌ DB_PASSWORD manquant (ex: DB_PASSWORD=... python ingest.py --copro 8050)")
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=pwd)


def db_snapshot(code):
    """Etat DB AVANT ingestion : {source_file: doc_type} + nb dossiers Airtable."""
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT source_file, COALESCE(doc_type_corrige, doc_type)
                       FROM documents WHERE code_ncg = %s""", (code,))
        docs = {sf: dt for sf, dt in cur.fetchall()}
        cur.execute("""SELECT COUNT(*) FROM dossiers
                       WHERE code_ncg = %s AND airtable_record_id IS NOT NULL""", (code,))
        nb_airtable = int((cur.fetchone() or [0])[0] or 0)
    return docs, nb_airtable


def current_docs_from_shard(code):
    """Etat courant {source_file: doc_type} depuis per_copro/<code>/chunks.jsonl (post Tier-1)."""
    path = pcfg.paths_for(code)["chunks_jsonl"]
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            sf = c.get("source_file")
            if sf and sf not in out:
                out[sf] = c.get("doc_type", "AUTRE")
    return out


def live_source_files(code):
    """Ensemble des source_file presents dans la source vivante apres 01 (= dossier
    'filtered', rebati par 01 depuis 'Donnees brutes'). Format relatif a
    Archives_Filtrees, identique a la colonne source_file en DB."""
    base = pcfg.filtered_dir(code)
    root = str(pcfg.FILTERED_ROOT)
    out = set()
    if not os.path.isdir(base):
        return out
    for r, _d, files in os.walk(base):
        for fn in files:
            out.add(os.path.relpath(os.path.join(r, fn), root))
    return out


def purge_deleted(code, deleted, dry):
    """Supprime le JSON extrait (Archives_Extraites/<source_file>.json) de chaque doc
    disparu de la source, pour que 03 reconstruise chunks.jsonl sans lui (06b propage
    ensuite la suppression en DB via son DELETE+reload)."""
    n = 0
    for sf in deleted:
        jp = os.path.join(str(pcfg.EXTRACTED_ROOT), sf + ".json")
        if os.path.exists(jp):
            if dry:
                print(f"   [dry-run] rm {jp}")
            else:
                os.remove(jp)
            n += 1
    return n


def run(script, code, dry, extra=None):
    cmd = [sys.executable, script, "--copro", code] + (extra or [])
    if dry:
        print(f"   [dry-run] {' '.join(cmd[1:])}")
        return
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    subprocess.run(cmd, check=True, env=env, cwd=os.path.dirname(os.path.abspath(__file__)))


def run_global(script, dry):
    """Etage SANS --copro (ex: 08, pas encore per-copro)."""
    cmd = [sys.executable, script]
    if dry:
        print(f"   [dry-run] {script} (global)")
        return
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    subprocess.run(cmd, check=True, env=env, cwd=os.path.dirname(os.path.abspath(__file__)))


def ingest_copro(code, dry=False, keep_shards=False):
    print(f"\n{'='*60}\nINGESTION copro {code} ({pcfg.folder_for(code)}){' [DRY-RUN]' if dry else ''}\n{'='*60}")

    before, nb_airtable = ({}, 0) if dry else db_snapshot(code)
    print(f"DB avant : {len(before)} documents, {nb_airtable} dossiers Airtable")

    # --- 01 filtrage : rebatit 'filtered' depuis la source vivante (les suppressions
    #     y deviennent visibles : un doc retire de la source n'est plus copie). ---
    print("\n[01] filtrage (filtered rebati depuis la source vivante)")
    run("01_filtrage.py", code, dry)

    # --- Suppressions : docs presents en DB mais absents de la source vivante.
    #     On retire leur JSON extrait -> 03 reconstruira chunks.jsonl sans eux -> 06b
    #     les retirera de la DB (et donc du RAG). C'est le D du CRUD. ---
    deleted = {}
    if dry:
        print("\n[purge] (dry-run : detection des suppressions necessite la DB)")
    else:
        live = live_source_files(code)
        deleted = {sf: dt for sf, dt in before.items() if sf not in live}
        if deleted:
            n = purge_deleted(code, deleted, dry)
            print(f"\n[purge] {len(deleted)} doc(s) supprime(s) de la source -> {n} JSON extrait(s) retire(s) "
                  f"(06b les retirera de la DB)")
        else:
            print("\n[purge] aucun document supprime")

    # --- Tier-1 restant : 02..05b (incrementaux) ---
    print("\n[Tier-1] 02..05b (incremental)")
    for stage in TIER1_STAGES[1:]:  # 01 deja lance ci-dessus
        run(stage, code, dry)

    # --- Delta : quels doc_types ont change (ajouts depuis chunks.jsonl + suppressions) ? ---
    after = current_docs_from_shard(code)
    new = {sf: dt for sf, dt in after.items() if sf not in before}
    delta_types = set(new.values()) | set(deleted.values())
    if dry:
        delta_types = GATE_05C | GATE_09  # dry-run : on montre toutes les gates
        print("\n[delta] (dry-run : on suppose un delta touchant tout pour montrer les gates)")
    print(f"\n[delta] {len(new)} nouveaux, {len(deleted)} supprimes ; doc_types touches : {sorted(delta_types) or '∅'}")

    # --- Tier-2 gate 05c (dossiers) : SSI un SINISTRE a change (ajoute OU supprime) ---
    regen_dossiers = bool(delta_types & GATE_05C)
    if regen_dossiers:
        print("\n[Tier-2] delta touche un SINISTRE -> regenere dossiers (05c + 00c)")
        run("05c_entity_extraction.py", code, dry)
        run("00c_dedup_dossiers_rag.py", code, dry)
    else:
        print("\n[Tier-2] aucun SINISTRE dans le delta -> 05c/00c SAUTES (anti-gaspillage)")

    # --- Chargement DB (upsert per-copro) ---
    print("\n[load] 06b (upsert DELETE WHERE code_ncg + INSERT)")
    run("06b_load_db.py", code, dry)

    # --- 08 Assynco : 06b a purge les donnees Airtable de la copro -> les restaurer ---
    if dry:
        print("\n[Assynco] (run reel) relance 08 SSI la copro avait des dossiers Airtable")
        run_global("08_airtable_sync.py", dry)
    elif nb_airtable > 0:
        print(f"\n[Assynco] copro avait {nb_airtable} dossiers Airtable -> relance 08 (global, restaure)")
        run_global("08_airtable_sync.py", dry)
    else:
        print("\n[Assynco] pas de donnees Airtable pour cette copro -> 08 saute")

    # --- Tier-2 gate 09 (fiche narratif) : SSI PV_AG ou SINISTRE a change ---
    if delta_types & GATE_09:
        print("\n[Tier-2] delta touche PV_AG/SINISTRE -> regenere la fiche (09)")
        run("09_copro_synthese.py", code, dry)
    else:
        print("\n[Tier-2] ni PV_AG ni SINISTRE dans le delta -> 09 SAUTE (faits live restent a jour)")

    # --- Nettoyage disque : le shard d'embeddings (gros) est jetable une fois en DB ---
    if not keep_shards and not dry:
        shard = pcfg.paths_for(code)["embeddings_sq_jsonl"]
        # TODO V1 : activer la suppression une fois le flux valide bout-en-bout.
        print(f"\n[cleanup] (desactive en V1) shard conservable : {shard}")

    print(f"\n✅ Ingestion {code} terminee.")


def main():
    ap = argparse.ArgumentParser(description="Driver d'ingestion incrementale per-copro.")
    ap.add_argument("--copro", help="Code NCG (ex: 8050).")
    ap.add_argument("--all", action="store_true", help="Toutes les copros de INCLUDED_COPROS.")
    ap.add_argument("--dry-run", action="store_true", help="Affiche le plan sans executer.")
    ap.add_argument("--keep-shards", action="store_true", help="Ne pas supprimer les shards apres load.")
    args = ap.parse_args()

    if args.all:
        codes = sorted(pcfg.INCLUDED_COPROS)
    elif args.copro:
        codes = [args.copro]
    else:
        raise SystemExit("❌ Preciser --copro <code> ou --all.")

    for code in codes:
        ingest_copro(code, dry=args.dry_run, keep_shards=args.keep_shards)


if __name__ == "__main__":
    main()
