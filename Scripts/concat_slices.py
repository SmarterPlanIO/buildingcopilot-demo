"""Concatène les slices per-copro en fichiers globaux pour 06b_load_db.py.

Génère dans `Résultats bruts/` :
  - chunks_avec_embeddings_sq.jsonl     (INPUT_FILE de 06b)
  - documents_metadata.jsonl            (METADATA_FILE de 06b, sibling)
  - dossiers.jsonl                      (DOSSIERS_FILE de 06b, sibling — si présent par copro)

Source : `Résultats bruts/per_copro/{code}/*.jsonl` pour chaque copro listée dans
INCLUDED_COPROS de pipeline_config. Skip silencieux si un fichier per-copro manque
(la copro n'a pas encore été traitée).

Usage :
  python concat_slices.py             # tous les copros listés
  python concat_slices.py --only 5553,5033   # seulement ces codes
  python concat_slices.py --check     # ne fait rien, juste liste l'état
"""
import argparse
import sys
from pathlib import Path

from pipeline_config import INCLUDED_COPROS, paths_for, RESULTS_ROOT

# Output globaux (consommés par 06b)
OUT_CHUNKS    = RESULTS_ROOT / "chunks_avec_embeddings_sq.jsonl"
OUT_METADATA  = RESULTS_ROOT / "documents_metadata.jsonl"
OUT_DOSSIERS  = RESULTS_ROOT / "dossiers.jsonl"

# Mapping output -> clé pipeline_config
SLICE_MAP = [
    ("embeddings_sq_jsonl",       OUT_CHUNKS),
    ("documents_metadata_jsonl",  OUT_METADATA),
    ("dossiers_jsonl",            OUT_DOSSIERS),
]


def main():
    parser = argparse.ArgumentParser(description="Concat des slices per-copro pour 06b.")
    parser.add_argument("--only", help="Liste de codes NCG (séparés par virgule).")
    parser.add_argument("--check", action="store_true", help="Affiche l'état sans rien écrire.")
    args = parser.parse_args()

    if args.only:
        codes = [c.strip() for c in args.only.split(",")]
        for c in codes:
            if c not in INCLUDED_COPROS:
                print(f"❌ Code inconnu : {c}. Valides : {sorted(INCLUDED_COPROS)}", file=sys.stderr)
                sys.exit(2)
    else:
        codes = sorted(INCLUDED_COPROS)

    print(f"📦 Codes à concaténer : {codes}")
    print()

    # État : lister fichiers présents par copro
    state = {}
    for code in codes:
        paths = paths_for(code)
        state[code] = {key: paths[key].exists() for key, _ in SLICE_MAP}

    for code in codes:
        statuses = state[code]
        flags = " ".join(f"{key.replace('_jsonl','')}={'✓' if v else '✗'}" for key, v in statuses.items())
        print(f"  {code} : {flags}")

    if args.check:
        return

    print()
    for key, out_path in SLICE_MAP:
        slices = [paths_for(c)[key] for c in codes if paths_for(c)[key].exists()]
        if not slices:
            print(f"⏭  {out_path.name} : aucune slice présente, skip.")
            continue

        total_lines = 0
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as out:
            for slice_path in slices:
                with open(slice_path, "r", encoding="utf-8") as src:
                    for line in src:
                        out.write(line)
                        total_lines += 1
        print(f"✅ {out_path.name} : {total_lines} lignes depuis {len(slices)} slices → {out_path}")


if __name__ == "__main__":
    main()
