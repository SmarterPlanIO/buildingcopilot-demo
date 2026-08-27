"""rattrapage_ocr.py — invalide l'extraction des docs a couche texte degradee.

Pour chaque document flague par le balayage (CSV sweep_ocr_degrade), verifie
LOCALEMENT (fitz, zero AWS) que sa source aurait pris le raccourci pdf_natif
(criteres de volume de 02) : si oui, la couche pourrie est en base -> on invalide
son extraction (entree de checkpoint + JSON extrait) pour que le prochain
`ingest.py --copro` re-evalue le doc avec la gate de qualite (fix cas 320) et le
route vers Textract. Les docs deja OCRises par Textract ne sont PAS invalides
(re-payer l'OCR n'apporterait rien).

Usage :
  PALIM_CLIENT=<client> python rattrapage_ocr.py --csv <sweep.csv> [--copro CODE] [--apply]
Sans --apply : dry-run (rien n'est modifie).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from collections import defaultdict
from pathlib import Path

import fitz

import pipeline_config as pcfg

CLIENT = os.environ.get("PALIM_CLIENT", "ncg")


def volume_natif(path: Path) -> bool:
    """Reproduit les criteres de VOLUME de extract_pdf_native (sans la gate qualite)."""
    try:
        doc = fitz.open(path)
        n = doc.page_count
        if n == 0:
            doc.close()
            return False
        texts = [pg.get_text() for pg in doc]
        doc.close()
        if sum(len(t) for t in texts) / n < 300:
            return False
        if n > 2:
            cov = sum(1 for t in texts if len(t.strip()) >= 100) / n
            if cov < 0.80:
                return False
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="CSV du balayage (sweep_ocr_degrade)")
    ap.add_argument("--copro", help="limiter a une copro (code canonique)")
    ap.add_argument("--apply", action="store_true", help="appliquer (defaut : dry-run)")
    args = ap.parse_args()

    rows = [r for r in csv.DictReader(io.open(args.csv, encoding="utf-8-sig"))
            if r["client"] == CLIENT]
    if args.copro:
        code = pcfg.resolve(args.copro)
        rows = [r for r in rows if r["code"] == code]
    print(f"client={CLIENT} : {len(rows)} docs flagues a examiner"
          + (f" (copro {args.copro})" if args.copro else ""))

    stats = defaultdict(lambda: {"natif_invalide": 0, "scanne_laisse": 0, "introuvable": 0})
    for r in rows:
        code, sf = r["code"], r["source_file"]
        src_dir = Path(pcfg.raw_source_dir(code))
        rel_in_copro = sf.split("\\", 1)[1] if "\\" in sf else sf
        src = src_dir / rel_in_copro
        if not src.exists():
            stats[code]["introuvable"] += 1
            continue
        if not volume_natif(src):
            stats[code]["scanne_laisse"] += 1
            continue
        stats[code]["natif_invalide"] += 1
        if args.apply:
            paths = pcfg.paths_for(code)
            # 1. entree de checkpoint (cle = rel_path == source_file)
            cp = Path(paths["extraction_checkpoint"])
            if cp.exists():
                data = json.loads(cp.read_text(encoding="utf-8"))
                sigs = data.get("sigs", data)
                if sf in sigs:
                    del sigs[sf]
                    cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            # 2. JSON extrait
            j = Path(pcfg.extracted_dir(code)) / (sf + ".json")
            if j.exists():
                j.unlink()

    print(f"\n{'copro':12s} {'invalides':>10s} {'scannes(ok)':>12s} {'introuvables':>12s}")
    tot = 0
    for code, st in sorted(stats.items()):
        print(f"{code:12s} {st['natif_invalide']:>10d} {st['scanne_laisse']:>12d} {st['introuvable']:>12d}")
        tot += st["natif_invalide"]
    mode = "APPLIQUE" if args.apply else "DRY-RUN (rien modifie)"
    print(f"\nTOTAL invalides : {tot} docs [{mode}]")
    if args.apply and tot:
        print("Suite : relancer `ingest.py --copro <code>` pour chaque copro listee "
              "(la gate de qualite routera ces docs vers Textract).")


if __name__ == "__main__":
    main()
