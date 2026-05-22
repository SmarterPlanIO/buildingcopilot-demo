"""Orchestrateur pipeline per-copro : enchaîne 01→05b séquentiellement pour 1 copro.

Usage :
  python run_pipeline_per_copro.py --copro 5033
  python run_pipeline_per_copro.py --copro 5033 --from 03   # reprendre à partir de l'étape 03
  python run_pipeline_per_copro.py --copro 5033 --only 04   # ne lancer que l'étape 04
  python run_pipeline_per_copro.py --copro 5033 --skip 04   # tout sauf 04

Conçu pour être lancé en parallèle (1 process par copro) :
  - Chaque étape produit ses fichiers dans per_copro/{code}/ → pas de collision
  - 06b (load DB) et 08 (Airtable sync) sont GLOBAUX → à lancer séparément après

Logs : per_copro/{code}/pipeline.log
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from pipeline_config import paths_for, INCLUDED_COPROS

SCRIPT_DIR = Path(__file__).parent.resolve()

STEPS = [
    ("01", "01_filtrage.py"),
    ("02", "02_extraction_optimized.py"),
    ("03", "03_chunking.py"),
    ("04", "04_metadata_documents.py"),
    ("05", "05_embedding.py"),
    ("05b", "05b_synthetic_questions.py"),
]
STEP_NAMES = [s[0] for s in STEPS]


def run_step(step_id: str, script: str, code: str, log_file) -> int:
    """Lance un script avec --copro <code>. Retourne le returncode."""
    cmd = [sys.executable, str(SCRIPT_DIR / script), "--copro", code]
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n{'=' * 70}\n[{started}] ÉTAPE {step_id} — {script} --copro {code}\n{'=' * 70}\n"
    print(header, flush=True)
    log_file.write(header)
    log_file.flush()

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    t0 = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=str(SCRIPT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        bufsize=1,
    )
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        log_file.write(line)
        log_file.flush()
    rc = proc.wait()
    elapsed = time.time() - t0

    footer = f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ÉTAPE {step_id} terminée (rc={rc}, durée={elapsed:.1f}s)\n"
    print(footer, flush=True)
    log_file.write(footer)
    log_file.flush()
    return rc


def main():
    parser = argparse.ArgumentParser(description="Pipeline d'ingestion per-copro (01→05b).")
    parser.add_argument("--copro", required=True, help="Code NCG (ex: 5033).")
    parser.add_argument("--from", dest="from_step", choices=STEP_NAMES, help="Reprendre à partir de cette étape (incluse).")
    parser.add_argument("--only", choices=STEP_NAMES, help="Ne lancer qu'une étape.")
    parser.add_argument("--skip", action="append", choices=STEP_NAMES, default=[], help="Ignorer une étape (cumulable).")
    args = parser.parse_args()

    if args.copro not in INCLUDED_COPROS:
        print(f"❌ Code copro inconnu : {args.copro}. Valides : {sorted(INCLUDED_COPROS)}", file=sys.stderr)
        sys.exit(2)

    paths = paths_for(args.copro)
    paths["per_copro"].mkdir(parents=True, exist_ok=True)
    log_path = paths["per_copro"] / "pipeline.log"

    # Sélection des étapes
    if args.only:
        selected = [(sid, s) for sid, s in STEPS if sid == args.only]
    elif args.from_step:
        idx = STEP_NAMES.index(args.from_step)
        selected = STEPS[idx:]
    else:
        selected = STEPS[:]
    selected = [(sid, s) for sid, s in selected if sid not in args.skip]

    print(f"📂 Copro {args.copro} ({paths['folder_name']})")
    print(f"📝 Log : {log_path}")
    print(f"🎯 Étapes : {[sid for sid, _ in selected]}")

    pipeline_t0 = time.time()
    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(f"\n\n########## PIPELINE START {datetime.now().isoformat()} | copro={args.copro} | steps={[s[0] for s in selected]} ##########\n")
        for step_id, script in selected:
            rc = run_step(step_id, script, args.copro, log_file)
            if rc != 0:
                msg = f"\n❌ Étape {step_id} a échoué (rc={rc}). Arrêt du pipeline pour {args.copro}.\n"
                print(msg, flush=True)
                log_file.write(msg)
                sys.exit(rc)

    total = time.time() - pipeline_t0
    print(f"\n✅ Pipeline terminé pour {args.copro} en {total / 60:.1f} min")


if __name__ == "__main__":
    main()
