"""
ÉTAPE 0 (preflight coût) — Mesurer AVANT de dépenser.
====================================================
Transforme en chiffres fermes les hypothèses H1/H2/H3/H5 du plan
`PLAN_REDUCTION_COUT_COPRO.md`, SANS aucun appel AWS (zéro coût) :

  - pages OCR exactes (H2)          -> driver coût Textract
  - taux de doublons fichiers (H3)  -> levier A (dedup pré-ingestion)
  - distribution doc_type (H5)      -> levier C (OCR tiéré), juridique vs routine
  - estimation grossière chunks (H1) -> driver coût Haiku (à confirmer en 03)

Le preflight opère sur les fichiers BRUTS (`Données brutes/<copro>/`) et
réplique les règles déterministes de `01_filtrage.py` (extensions gardées /
exclues) et la logique de triage OCR de `02_extraction_optimized.extract_pdf_native`.
Il NE lance NI 01 (copie disque + Sonnet Vision) NI Textract.

Usage :
  PYTHONIOENCODING=utf-8 python 00a_cost_preflight.py --copro 5412
  PYTHONIOENCODING=utf-8 python 00a_cost_preflight.py --copro 5412 --no-zip-overlap

Livrable : Résultats bruts/per_copro/<code>/cost_preflight.json
"""
import io
import re
import sys
import json
import zlib
import zipfile
import hashlib
import argparse
from pathlib import Path
from collections import defaultdict

try:
    import fitz  # PyMuPDF
except ImportError:
    import os
    os.system("pip install PyMuPDF")
    import fitz

import pipeline_config as pcfg
from pipeline_config import RAW_ROOT, PER_COPRO_ROOT, INCLUDED_COPROS

# Console Windows : forcer UTF-8 (emojis dans les prints)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Taux unitaires (cf. PLAN_REDUCTION_COUT_COPRO.md §2.2, calibrés sur facture réelle)
TEXTRACT_USD_PER_PAGE = 0.0015   # DetectDocumentText
HAIKU_USD_PER_CHUNK = 0.00053    # 87,75 $ / 166 094 chunks

# ── Règles de filtrage répliquées de 01_filtrage.py (garder en sync) ──
KEEP_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv",
                   ".msg", ".eml", ".txt", ".rtf", ".ppt", ".pptx"}
PLAN_EXTENSIONS = {".dwg", ".dxf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".heic"}
EXCLUDE_EXTENSIONS = {".zip", ".rar", ".7z", ".exe", ".msi", ".dmg", ".mp4", ".avi", ".mov", ".mp3"}
SYSTEM_FILES = {".ds_store", "thumbs.db", "desktop.ini", ".gitkeep", ".dropbox"}
PHOTO_KEYWORDS = ["photo", "photos", "img_", "dsc_", "dcim", "screenshot", "capture",
                  "whatsapp", "signal", "image_", "constat",
                  "dégât", "degat", "sinistre_photo", "visite", "état_des_lieux_photo"]
PLAN_KEYWORDS = ["plan", "plans", "pln", "niveau", "etage", "étage", "rdc", "rez-de-chaussée",
                 "sous-sol", "ss1", "ss2", "coupe", "facade", "façade", "élévation", "elevation",
                 "masse", "situation", "cadastr", "parcell", "géomètre", "geometre",
                 "architecte", "archi", "lot", "tantième", "millième", "répartition",
                 "carnet_entretien", "carnet entretien", "diagnostic",
                 "mesurage", "loi_carrez", "carrez", "surface"]

# Extensions qui partent en OCR Textract dans 02 (cf. IMAGE_EXTS de 02)
OCR_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}

# Buckets « routine » (candidats Tesseract local, levier C) vs « juridique » (Textract obligatoire)
JURIDIQUE_TYPES = {"PV_AG", "RCP", "CONTRAT", "ASSURANCE", "SINISTRE"}


def _is_system_file(filename):
    fl = filename.lower()
    return fl in SYSTEM_FILES or filename.startswith("~$") or filename.startswith("._")


def _path_has(path_lower, keywords):
    p = path_lower.replace("_", " ").replace("-", " ")
    return any(kw in p for kw in keywords)


# =====================================================
# doc_type : copie verbatim des passes 1+2 de
# 03_chunking.detect_doc_type (pass 3 LLM retirée -> AUTRE).
# GARDER EN SYNC avec 03_chunking.py.
# =====================================================
def detect_doc_type_static(filepath, filename):
    filename_lower = filename.lower()
    path_parts = [p.lower() for p in filepath.replace("\\", "/").split("/") if p]

    # ── PASSE 1 : structure des dossiers ──
    for part in path_parts:
        if part in ("assemblee", "assemblée", "assemblees", "assemblées",
                    "ag", "pv", "pv_ag", "proces_verbaux"):
            continue
        if part in ("compta", "comptabilité", "comptabilite", "comptable"):
            return "COMPTABILITE"
        if part in ("entretien", "maintenance"):
            return "ENTRETIEN"
        if part in ("sinistre", "sinistres", "expertise", "expertises", "anomalies"):
            return "SINISTRE"
        if part in ("reglement", "règlement", "rcp", "reglement_copro",
                    "règlement_copropriété", "regl_copro"):
            return "RCP"
        if part in ("contrat", "contrats", "mandat", "mandats", "convention", "conventions"):
            return "CONTRAT"
        if part in ("devis",):
            return "DEVIS"
        if part in ("facture", "factures"):
            return "FACTURE"
        if part in ("budget", "budgets", "appels_de_fonds", "repartition", "répartition"):
            return "BUDGET"
        if part in ("diagnostic", "diagnostics", "dpe", "amiante"):
            return "DIAGNOSTIC"
        if part in ("courrier", "courriers", "correspondance", "lrar"):
            return "COURRIER"
        if part in ("plan", "plans", "architecte"):
            return "PLAN"
        if part in ("assurance", "assurances"):
            return "ASSURANCE"

    # ── PASSE 2 : nom du fichier ──
    if (re.search(r'\bbo[r]?dereau', filename_lower)
            or re.search(r'\baccus[eé].*r[eé]ception|\bavis.*r[eé]ception', filename_lower)
            or re.search(r'certif-.*deposit|_deposit\.pdf|recipients\.csv', filename_lower)):
        return "BORDEREAU_AR"
    if re.search(r'\bcarnet\b.*\bentretien\b|\bentretien\b|\bmaintenance\b', filename_lower):
        return "ENTRETIEN"
    if re.search(r'\bsinistres?\b|\banomalies?\b|\bconstat\b|\bexpertise\b|\bbilan\b.*\banomal', filename_lower):
        return "SINISTRE"
    if re.search(r'\bannexe\b|\bgrand[\-_\s]?livre\b|\bjournal\b|\bcompta\b|\bcomptes?\b|\bcharges?\s+de\s+copro|\bbalance\b', filename_lower):
        return "COMPTABILITE"
    if re.search(r'\bappel\b.*\bexcept', filename_lower):
        return "BUDGET"
    if re.search(r'\bpvag\b|\bpv\b|\bproc[eè]s[\-_\s]?verbal', filename_lower):
        return "PV_AG"
    if re.search(r'\bcontrat\b|\bmandat\b|\bconvention\b', filename_lower):
        return "CONTRAT"
    if re.search(r'\bdevis\b', filename_lower):
        return "DEVIS"
    if re.search(r'\br[eè]glement\b|\brcp\b|\bregl[\-_]copro', filename_lower):
        return "RCP"
    if re.search(r'\bfacture\b|\bfact[\-_]', filename_lower):
        return "FACTURE"
    if re.search(r'\bbudget\b|\bappel[\-_\s]de[\-_\s]fond', filename_lower):
        return "BUDGET"
    if re.search(r'\bdiagnostic\b|\bdpe\b|\bamiante\b|\bplomb\b|\btermite\b', filename_lower):
        return "DIAGNOSTIC"
    if re.search(r'\bassurance\b|\bpolice\b', filename_lower):
        return "ASSURANCE"
    if re.search(r'\bplan\b|\bpln\b|\barchi\b', filename_lower):
        return "PLAN"
    return "AUTRE"


# =====================================================
# Triage OCR : réplique extract_pdf_native de 02.
# Retourne (is_native, page_count, total_chars).
# =====================================================
NATIVE_THRESHOLD_CHARS_PER_PAGE = 300
MIN_PAGE_CHARS = 100
MIN_COVERAGE_RATIO = 0.80


def analyze_pdf(filepath):
    try:
        doc = fitz.open(filepath)
    except Exception:
        # PDF illisible par PyMuPDF -> traité comme image-only par 02 (OCR intégral, pages inconnues)
        return (False, 0, 0)
    try:
        page_count = doc.page_count
        if page_count == 0:
            return (False, 0, 0)
        page_texts = [page.get_text() for page in doc]
    finally:
        doc.close()

    full_text = "".join(page_texts)
    total_chars = len(full_text.strip())
    avg_chars = total_chars / page_count

    if avg_chars < NATIVE_THRESHOLD_CHARS_PER_PAGE:
        return (False, page_count, total_chars)
    if page_count > 2:
        pages_with_text = sum(1 for pt in page_texts if len(pt.strip()) >= MIN_PAGE_CHARS)
        if (pages_with_text / page_count) < MIN_COVERAGE_RATIO:
            return (False, page_count, total_chars)  # mixte -> OCR intégral (H7)
    return (True, page_count, total_chars)


def hash_file(filepath):
    """Retourne (sha256_hex, crc32, size_bytes) en une seule lecture streaming."""
    h = hashlib.sha256()
    crc = 0
    size = 0
    with open(filepath, "rb") as f:
        while True:
            block = f.read(8 * 1024 * 1024)
            if not block:
                break
            h.update(block)
            crc = zlib.crc32(block, crc)
            size += len(block)
    return h.hexdigest(), crc & 0xFFFFFFFF, size


def resolve_raw_dir(code):
    if code in INCLUDED_COPROS:
        # Passe par pipeline_config : honore `raw_dir` (source externe/UNC) si déclaré
        return pcfg.raw_source_dir(code)
    matches = sorted(p for p in RAW_ROOT.iterdir()
                     if p.is_dir() and p.name.startswith(code))
    if not matches:
        raise SystemExit(f"❌ Aucun dossier brut trouvé pour le code {code} dans {RAW_ROOT}")
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description="Preflight coût d'ingestion (zéro AWS).")
    parser.add_argument("--copro", required=True, help="Code NCG (ex: 5412)")
    parser.add_argument("--no-zip-overlap", action="store_true",
                        help="Ne pas inspecter le contenu des ZIP (plus rapide)")
    args = parser.parse_args()

    code = args.copro
    raw_dir = resolve_raw_dir(code)
    out_dir = PER_COPRO_ROOT / code
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "cost_preflight.json"

    print(f"📌 Preflight coût — copro {code}")
    print(f"   Source : {raw_dir}")
    print(f"   Sortie : {out_file}\n")

    files_stats = defaultdict(int)
    pdf = {"count": 0, "native": 0, "ocr_bound": 0,
           "pages_total": 0, "pages_native": 0, "pages_ocr": 0, "native_chars": 0}

    # Dedup : sha256 -> liste de rel_paths. On compte aussi les pages OCR par hash
    # pour chiffrer les pages économisables par dedup.
    hash_to_paths = defaultdict(list)
    hash_ocr_pages = {}        # sha -> pages_ocr du fichier (0 si non-OCR)
    hash_size = {}             # sha -> taille
    loose_crc_size = set()     # (crc32, size) des fichiers loose -> overlap ZIP
    zip_files = []

    type_dist_all = defaultdict(int)                 # doc_type -> nb fichiers gardés
    type_dist_ocr = defaultdict(lambda: {"files": 0, "pages": 0})  # doc_type -> OCR

    n_seen = 0
    for filepath in raw_dir.rglob("*"):
        if not filepath.is_file():
            continue
        n_seen += 1
        if n_seen % 500 == 0:
            print(f"   ... {n_seen} fichiers scannés")

        fname = filepath.name
        ext = filepath.suffix.lower()
        rel_path = str(filepath.relative_to(raw_dir))

        if _is_system_file(fname):
            files_stats["exclus_systeme"] += 1
            continue

        # ── Décision de filtrage (réplique 01) ──
        if ext in EXCLUDE_EXTENSIONS:
            files_stats["exclus_archives"] += 1
            if ext == ".zip":
                zip_files.append(filepath)
            continue

        if ext in IMAGE_EXTENSIONS:
            # 01 garde l'image seulement si c'est un PLAN. Heuristique déterministe
            # (sans Sonnet Vision) : mot-clé plan -> gardé/OCR ; sinon -> photo exclue.
            path_lower = str(filepath).lower()
            if _path_has(path_lower, PHOTO_KEYWORDS) or not _path_has(path_lower, PLAN_KEYWORDS):
                files_stats["exclus_images"] += 1
                continue
            # plan-image gardé -> part en OCR dans 02
            kept_kind = "image_ocr"
        elif ext in KEEP_EXTENSIONS or ext in PLAN_EXTENSIONS:
            kept_kind = "doc"
        else:
            kept_kind = "doc"  # extension inconnue -> 01 garde par sécurité

        files_stats["gardes"] += 1

        # ── Hash (dedup + overlap ZIP) ──
        try:
            sha, crc, size = hash_file(filepath)
        except OSError:
            files_stats["erreurs_lecture"] += 1
            continue
        first_seen = sha not in hash_to_paths
        hash_to_paths[sha].append(rel_path)
        hash_size[sha] = size
        loose_crc_size.add((crc, size))

        # ── doc_type (déterministe) ──
        dtype = detect_doc_type_static(rel_path, fname)
        if first_seen:
            type_dist_all[dtype] += 1

        # ── Pages / OCR ──
        ocr_pages_this = 0
        if ext == ".pdf":
            pdf["count"] += 1
            is_native, page_count, total_chars = analyze_pdf(filepath)
            pdf["pages_total"] += page_count
            if is_native:
                pdf["native"] += 1
                pdf["pages_native"] += page_count
                pdf["native_chars"] += total_chars
            else:
                pdf["ocr_bound"] += 1
                pdf["pages_ocr"] += page_count
                ocr_pages_this = page_count
        elif ext in OCR_IMAGE_EXTS and kept_kind == "image_ocr":
            ocr_pages_this = 1  # 1 image = 1 page Textract
            pdf["pages_ocr"] += 1

        if ocr_pages_this and first_seen:
            type_dist_ocr[dtype]["files"] += 1
            type_dist_ocr[dtype]["pages"] += ocr_pages_this
        if first_seen:
            hash_ocr_pages[sha] = ocr_pages_this

    # ── Dedup ──
    kept_files = sum(len(v) for v in hash_to_paths.values())
    unique_files = len(hash_to_paths)
    dup_files = kept_files - unique_files
    dup_bytes = sum(hash_size[s] * (len(p) - 1) for s, p in hash_to_paths.items())
    ocr_pages_saved = sum(hash_ocr_pages.get(s, 0) * (len(p) - 1) for s, p in hash_to_paths.items())
    total_bytes = sum(hash_size[s] * len(p) for s, p in hash_to_paths.items())

    # pages OCR après dedup (chiffre net réel à payer)
    pages_ocr_dedup = pdf["pages_ocr"] - ocr_pages_saved

    # ── Overlap ZIP (les ZIP sont-ils des copies des fichiers loose ?) ──
    zip_overlap = None
    if zip_files and not args.no_zip_overlap:
        zm_total = 0
        zm_match_loose = 0
        zm_pdf = 0
        for zf in zip_files:
            try:
                with zipfile.ZipFile(zf) as z:
                    for info in z.infolist():
                        if info.is_dir():
                            continue
                        zm_total += 1
                        if info.filename.lower().endswith(".pdf"):
                            zm_pdf += 1
                        # CRC + taille dispo dans le central directory, sans décompresser
                        if (info.CRC, info.file_size) in loose_crc_size:
                            zm_match_loose += 1
            except (zipfile.BadZipFile, OSError):
                continue
        zip_overlap = {
            "zip_count": len(zip_files),
            "members_total": zm_total,
            "members_pdf": zm_pdf,
            "members_matching_loose_files": zm_match_loose,
            "overlap_rate": round(zm_match_loose / zm_total, 4) if zm_total else 0.0,
            "note": "ZIP exclus par 01_filtrage (EXCLUDE_EXTENSIONS). Overlap = part du contenu ZIP qui duplique déjà un fichier loose ingéré.",
        }

    # ── Estimation grossière chunks (H1) — à confirmer en lançant 03 ──
    # Hypothèse : ~1800 chars/page OCR, chunk ~1500 chars utiles.
    OCR_CHARS_PER_PAGE = 1800
    CHARS_PER_CHUNK = 1500
    est_total_chars = pdf["native_chars"] + pages_ocr_dedup * OCR_CHARS_PER_PAGE
    chunks_estimate = round(est_total_chars / CHARS_PER_CHUNK)

    report = {
        "copro": code,
        "folder": raw_dir.name,
        "source_dir": str(raw_dir),
        "files": {
            "scanned": n_seen,
            "kept": files_stats["gardes"],
            "excluded_system": files_stats["exclus_systeme"],
            "excluded_archives": files_stats["exclus_archives"],
            "excluded_images": files_stats["exclus_images"],
            "read_errors": files_stats["erreurs_lecture"],
        },
        "pdf": {
            **pdf,
            "scan_rate": round(pdf["ocr_bound"] / pdf["count"], 4) if pdf["count"] else 0.0,
            "avg_pages_per_pdf": round(pdf["pages_total"] / pdf["count"], 2) if pdf["count"] else 0.0,
        },
        "ocr": {
            "pages_ocr_gross": pdf["pages_ocr"],
            "pages_ocr_after_dedup": pages_ocr_dedup,
            "textract_usd_gross": round(pdf["pages_ocr"] * TEXTRACT_USD_PER_PAGE, 2),
            "textract_usd_after_dedup": round(pages_ocr_dedup * TEXTRACT_USD_PER_PAGE, 2),
        },
        "dedup": {
            "kept_files": kept_files,
            "unique_files": unique_files,
            "duplicate_files": dup_files,
            "dup_rate_files": round(dup_files / kept_files, 4) if kept_files else 0.0,
            "duplicate_bytes": dup_bytes,
            "total_bytes": total_bytes,
            "dup_rate_bytes": round(dup_bytes / total_bytes, 4) if total_bytes else 0.0,
            "ocr_pages_saved_by_dedup": ocr_pages_saved,
            "textract_usd_saved_by_dedup": round(ocr_pages_saved * TEXTRACT_USD_PER_PAGE, 2),
        },
        "zip_overlap": zip_overlap,
        "type_distribution_all_files": dict(sorted(type_dist_all.items(), key=lambda x: -x[1])),
        "type_distribution_ocr_pages": {
            k: v for k, v in sorted(type_dist_ocr.items(), key=lambda x: -x[1]["pages"])
        },
        "ocr_juridique_vs_routine": _juridique_split(type_dist_ocr),
        "haiku_estimate": {
            "chunks_estimate_rough": chunks_estimate,
            "haiku_usd_estimate_rough": round(chunks_estimate * HAIKU_USD_PER_CHUNK, 2),
            "assumptions": f"~{OCR_CHARS_PER_PAGE} chars/page OCR, ~{CHARS_PER_CHUNK} chars/chunk. À confirmer en lançant 03_chunking.",
        },
    }

    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(report)
    print(f"\n📋 Rapport : {out_file}")


def _juridique_split(type_dist_ocr):
    # Split en 3 voies : AUTRE n'est PAS "routine" — c'est du non-classé qui passe
    # par Haiku (pass 3 de 03) et peut contenir du juridique. Le router aveuglément
    # vers Tesseract (levier C) serait risqué. On l'isole.
    jur_pages = sum(v["pages"] for k, v in type_dist_ocr.items() if k in JURIDIQUE_TYPES)
    autre_pages = sum(v["pages"] for k, v in type_dist_ocr.items() if k == "AUTRE")
    rout_pages = sum(v["pages"] for k, v in type_dist_ocr.items()
                     if k not in JURIDIQUE_TYPES and k != "AUTRE")
    total = jur_pages + rout_pages + autre_pages
    return {
        "juridique_pages": jur_pages,
        "routine_confident_pages": rout_pages,
        "a_classer_pages": autre_pages,
        "routine_confident_share": round(rout_pages / total, 4) if total else 0.0,
        "a_classer_share": round(autre_pages / total, 4) if total else 0.0,
        "tesseract_addressable_usd": round(rout_pages * TEXTRACT_USD_PER_PAGE, 2),
        "note": "Routine confiante = candidats Tesseract (levier C), classés par dossier/nom. "
                "AUTRE = non classé (Haiku pass 3 requis avant tout routage). "
                "Juridique (PV_AG/RCP/CONTRAT/ASSURANCE/SINISTRE) = Textract obligatoire.",
    }


def _print_summary(r):
    print("=" * 56)
    print(f"PREFLIGHT COÛT — {r['copro']} ({r['folder']})")
    print("=" * 56)
    f = r["files"]
    print(f"\nFichiers : {f['scanned']} scannés, {f['kept']} gardés "
          f"(exclus: {f['excluded_archives']} archives, {f['excluded_images']} images, {f['excluded_system']} système)")
    p = r["pdf"]
    print(f"\nPDF : {p['count']} ({p['native']} natifs / {p['ocr_bound']} OCR), "
          f"scan_rate {p['scan_rate']:.0%}, moy {p['avg_pages_per_pdf']} p/PDF")
    print(f"      {p['pages_total']} pages totales, {p['pages_ocr']} pages OCR brutes")
    o = r["ocr"]
    print(f"\nTextract : {o['pages_ocr_gross']} pages → {o['textract_usd_gross']} $ "
          f"(après dedup : {o['pages_ocr_after_dedup']} pages → {o['textract_usd_after_dedup']} $)")
    d = r["dedup"]
    print(f"\nDedup (H3) : {d['duplicate_files']}/{d['kept_files']} fichiers dupes "
          f"({d['dup_rate_files']:.1%}), {d['dup_rate_bytes']:.1%} des octets, "
          f"économise {d['ocr_pages_saved_by_dedup']} pages OCR ({d['textract_usd_saved_by_dedup']} $)")
    if r["zip_overlap"]:
        z = r["zip_overlap"]
        print(f"\nZIP : {z['zip_count']} archives, {z['members_total']} membres "
              f"({z['members_pdf']} PDF), overlap fichiers loose {z['overlap_rate']:.1%}")
    s = r["ocr_juridique_vs_routine"]
    print(f"\nOCR (H5) : {s['juridique_pages']} jur / {s['routine_confident_pages']} routine confiante "
          f"({s['routine_confident_share']:.0%}) / {s['a_classer_pages']} à classer ({s['a_classer_share']:.0%})")
    print(f"          gisement Tesseract adressable (routine confiante) : {s['tesseract_addressable_usd']} $")
    h = r["haiku_estimate"]
    print(f"\nHaiku (H1, grossier) : ~{h['chunks_estimate_rough']} chunks → "
          f"~{h['haiku_usd_estimate_rough']} $ (confirmer via 03)")


if __name__ == "__main__":
    main()
