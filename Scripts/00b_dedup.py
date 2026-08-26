"""00b_dedup.py — Dédup exacte SHA-256 per-copro AVANT extraction (levier L1 coût).

S'exécute entre 01 (filtrage → Archives_Filtrees/<copro>) et 02 (Textract).
~33 % des fichiers du Drive Delacour sont des copies exactes (mesure inventaire
17/08) : chaque copie évitée économise Textract + le traitement aval (02/03) —
Haiku 04/05b étant déjà protégé par la dédup par similarité de 03, le gain
principal est l'OCR, poste réel ~10 pages × 0,0015 $ par fichier scanné.

Mécanique :
- Pré-groupe par taille, puis SHA-256 du contenu (fichiers filtrés = disque
  local, rapide) uniquement pour les tailles en collision.
- Par groupe de doublons : garde UN exemplaire (déterministe : chemin le moins
  profond, puis ordre alphabétique insensible à la casse), supprime les autres
  du dossier filtré. La source (Drive) n'est JAMAIS touchée.
- Manifest per_copro/<code>/dedup_manifest.json : {sha: {kept, removed, size}}
  pour traçabilité.
- 01 rebâtit `filtered` à chaque run → 00b DOIT re-tourner après chaque 01
  (câblé dans ingest.py et run_pipeline_per_copro.py).
- Les doublons déjà ingérés en DB sont retirés au prochain ingest : absents de
  `filtered`, ils passent par la mécanique standard des suppressions (06b).

Usage : PALIM_CLIENT=<client> python 00b_dedup.py --copro <code>
"""
import argparse
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

import pipeline_config as pcfg


def sha256_of(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(bufsize):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description="Dédup exacte SHA-256 du dossier filtré d'une copro.")
    ap.add_argument("--copro", required=True, help="Code copro (immatriculation ou alias).")
    args, _ = ap.parse_known_args()

    code = pcfg.resolve(args.copro)
    base = Path(pcfg.filtered_dir(code))
    if not base.is_dir():
        raise SystemExit(f"❌ Dossier filtré introuvable : {base} (lancer 01_filtrage d'abord)")

    # Pré-groupe par taille (on ne hashe que les tailles en collision)
    by_size = defaultdict(list)
    for root, _dirs, files in os.walk(base):
        for fn in files:
            p = Path(root) / fn
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > 0:
                by_size[size].append(p)

    n_total = sum(len(v) for v in by_size.values())
    groups = defaultdict(list)
    hashed = 0
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        for p in paths:
            try:
                groups[(sha256_of(p), size)].append(p)
                hashed += 1
            except OSError as e:
                print(f"   ⚠️ hash impossible, conservé : {p.name} ({e})")

    manifest = {}
    removed_files, removed_bytes = 0, 0
    for (sha, size), paths in groups.items():
        if len(paths) < 2:
            continue
        ordered = sorted(paths, key=lambda p: (len(p.relative_to(base).parts), str(p).lower()))
        kept, dupes = ordered[0], ordered[1:]
        for d in dupes:
            try:
                os.remove(d)
                removed_files += 1
                removed_bytes += size
            except OSError as e:
                print(f"   ⚠️ suppression impossible : {d} ({e})")
                dupes = [x for x in dupes if x != d]
        if dupes:
            manifest[sha] = {
                "size": size,
                "kept": str(kept.relative_to(base)),
                "removed": [str(d.relative_to(base)) for d in dupes],
            }

    pcd = Path(pcfg.per_copro_dir(code))
    pcd.mkdir(parents=True, exist_ok=True)
    with open(pcd / "dedup_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    pct = 100 * removed_files / max(n_total, 1)
    print(f"\n===== DÉDUP SHA-256 — {code} =====")
    print(f"   Fichiers analysés   : {n_total} ({hashed} hashés)")
    print(f"   Doublons supprimés  : {removed_files} ({pct:.0f} %), {removed_bytes / 1e6:.0f} Mo")
    print(f"   Groupes             : {len(manifest)}")
    print(f"   Manifest            : {pcd / 'dedup_manifest.json'}")


if __name__ == "__main__":
    main()
