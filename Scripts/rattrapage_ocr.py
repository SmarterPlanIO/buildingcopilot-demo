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


def rel_sous_copro(source_file: str) -> str:
    """source_file DB = "<dossier copro>\\<sous-chemin>" ; les artefacts de 02
    (JSON extraits) sont ranges SOUS extracted_dir(code) par le sous-chemin seul."""
    return source_file.split("\\", 1)[1] if "\\" in source_file else source_file


def json_extrait(code, source_file) -> Path:
    return Path(pcfg.extracted_dir(code)) / (rel_sous_copro(source_file) + ".json")


def texte_extrait(code, source_file):
    """Texte produit par 02 pour ce document (celui qui est parti en base)."""
    j = json_extrait(code, source_file)
    if not j.exists():
        return ""
    try:
        return json.loads(j.read_text(encoding="utf-8")).get("texte", "")
    except (OSError, ValueError):
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="CSV du balayage (sweep_ocr_degrade)")
    ap.add_argument("--copro", help="limiter a une copro (code canonique)")
    ap.add_argument("--apply", action="store_true", help="appliquer (defaut : dry-run)")
    ap.add_argument("--no-arbitrage", action="store_true",
                    help="desactiver l'arbitrage Haiku (invalide sur le score seul)")
    ap.add_argument("--workers", type=int, default=8, help="threads d'arbitrage Haiku")
    args = ap.parse_args()

    rows = [r for r in csv.DictReader(io.open(args.csv, encoding="utf-8-sig"))
            if r["client"] == CLIENT]
    if args.copro:
        code = pcfg.resolve(args.copro)
        rows = [r for r in rows if r["code"] == code]
    print(f"client={CLIENT} : {len(rows)} docs flagues a examiner"
          + (f" (copro {args.copro})" if args.copro else ""))

    # ── Etape 1 : filtre local (la source aurait-elle pris le raccourci natif ?) ──
    candidats = []
    stats = defaultdict(lambda: {"natif_invalide": 0, "scanne_laisse": 0,
                                 "introuvable": 0, "sauve_arbitrage": 0,
                                 "json_trouve": 0, "json_absent": 0,
                                 "json_supprime": 0, "cp_supprime": 0})
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
        candidats.append((code, sf))
    print(f"  {len(candidats)} candidats (couche native prise par le raccourci)")

    # ── Etape 2 : arbitrage Haiku — le score heuristique produit ~57 % de faux
    # positifs sur les documents tabulaires/comptables sains (mesure 27/08).
    # 0,0005 $/doc d'arbitrage evitent ~0,014 $/doc d'OCR inutile. ──
    a_invalider = set(candidats)
    if not args.no_arbitrage and candidats:
        import boto3
        from concurrent.futures import ThreadPoolExecutor

        import ocr_quality
        bedrock = boto3.client("bedrock-runtime", region_name="eu-west-1")
        print(f"  arbitrage Haiku de {len(candidats)} candidats "
              f"(~${len(candidats) * 0.0005:.2f}, {args.workers} threads)...")

        def _juge(item):
            code, sf = item
            texte = texte_extrait(code, sf)
            if len(texte.strip()) < 200:      # trop court pour juger : on garde
                return item, "DEGRADE"
            try:
                return item, ocr_quality._arbitrage_haiku(texte, bedrock)
            except Exception:
                return item, "DEGRADE"        # fail-safe : on privilegie la qualite

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for item, verdict in ex.map(_juge, candidats):
                if verdict == "PROPRE":
                    a_invalider.discard(item)
                    stats[item[0]]["sauve_arbitrage"] += 1
        print(f"  arbitrage : {len(a_invalider)} confirmes degrades, "
              f"{len(candidats) - len(a_invalider)} sauves (faux positifs du score)")

    # ── Etape 3 : invalidation (avec VERIFICATION — un compteur de candidats ne
    # prouve rien : le run du 27/08 a annonce 1269 invalidations alors que ni le
    # JSON ni le checkpoint n'avaient ete touches, chemins errones) ──
    # Levier essentiel = supprimer le JSON extrait. 02 re-traite alors le document
    # (JSON absent => pas de skip possible, quel que soit l'etat du checkpoint).
    # L'entree de checkpoint est retiree en complement, avec correspondance par
    # SUFFIXE : selon les generations d'ingestion, les cles portent un prefixe
    # ("SOURCE_ARCHIVES\\<copro>\\...") ou non.
    cp_cache: dict[str, tuple[Path, dict, dict]] = {}
    for code, sf in sorted(a_invalider):
        stats[code]["natif_invalide"] += 1
        rel = rel_sous_copro(sf)
        j = json_extrait(code, sf)
        if not j.exists():
            stats[code]["json_absent"] += 1
            continue
        stats[code]["json_trouve"] += 1
        if not args.apply:
            continue
        j.unlink()
        stats[code]["json_supprime"] += 1
        if code not in cp_cache:
            cp = Path(pcfg.paths_for(code)["extraction_checkpoint"])
            data = json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else None
            cp_cache[code] = (cp, data, (data.get("sigs", data) if data else {}))
        cp, data, sigs = cp_cache[code]
        for cle in [k for k in sigs if k == rel or k.endswith("\\" + rel)]:
            del sigs[cle]
            stats[code]["cp_supprime"] += 1

    if args.apply:
        for code, (cp, data, _sigs) in cp_cache.items():
            if data is not None:
                cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    print(f"\n{'copro':12s} {'a traiter':>10s} {'JSON trouve':>12s} {'JSON absent':>12s} "
          f"{'supprimes':>10s} {'cp purges':>10s} {'sauves(arb)':>12s} {'scannes(ok)':>12s}")
    tot = sauves = trouves = absents = supprimes = 0
    for code, st in sorted(stats.items()):
        print(f"{code:12s} {st['natif_invalide']:>10d} {st['json_trouve']:>12d} "
              f"{st['json_absent']:>12d} {st['json_supprime']:>10d} {st['cp_supprime']:>10d} "
              f"{st['sauve_arbitrage']:>12d} {st['scanne_laisse']:>12d}")
        tot += st["natif_invalide"]
        sauves += st["sauve_arbitrage"]
        trouves += st["json_trouve"]
        absents += st["json_absent"]
        supprimes += st["json_supprime"]
    mode = "APPLIQUE" if args.apply else "DRY-RUN (rien modifie)"
    print(f"\nTOTAL : {tot} docs a re-OCRiser, {trouves} JSON localises, "
          f"{absents} JSON introuvables, {supprimes} supprimes [{mode}]"
          + (f"\n{sauves} faux positifs ecartes par l'arbitrage "
             f"(~${sauves * 9 * 0.0015:.1f} d'OCR evites)" if sauves else ""))
    if absents:
        print(f"⚠️  {absents} JSON introuvables : ces documents ne seront PAS re-traites "
              f"(chemin d'artefacts inattendu). Verifier extracted_dir avant de relancer.")
    if args.apply and supprimes:
        print("Suite : relancer `ingest.py --copro <code>` pour chaque copro listee "
              "(la gate de qualite routera ces docs vers Textract).")


if __name__ == "__main__":
    main()
