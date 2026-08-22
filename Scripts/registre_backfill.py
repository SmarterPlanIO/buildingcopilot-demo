#!/usr/bin/env python3
"""
registre_backfill.py — P0 de PLAN_REGISTRE_INGESTION.md

Reconstruit `ingestion_registre` pour les copros deja ingerees, a partir des
artefacts deja presents sur disque et en base. NE RE-TRAITE RIEN : aucune
extraction, aucun appel LLM, aucun embedding. Lecture seule sur les artefacts,
ecriture seule sur `ingestion_registre` / `ingestion_runs`.

Sources croisees, par copro :
  filtrage_rapport.json      -> tout ce que 01 a vu, et sa decision
  dedup_manifest.json        -> sha256 + copies exactes retirees par 00b (+ le garde)
  Archives_Extraites/<copro> -> ce que 02 a reellement extrait (nb_caracteres)
  chunks.jsonl               -> ce que 03 a chunke (nb_chunks, doc_type)
  documents_metadata.jsonl   -> doc_type_corrige (04), plus fiable que celui de 03
  table chunks               -> ce qui est reellement en base (INGERE)

ATTENTION — statuts RECONSTRUITS, pas enregistres a chaud :
  Le pipeline actuel ne journalise pas ses rejets. Deux motifs sont donc INFERES
  et non lus :
    - un fichier extrait mais absent de chunks.jsonl est rejoue a travers
      `content_filter.analyze_file_quality` : verdict SKIP -> NON_EXPLOITABLE,
      sinon -> DOUBLON_PROCHE (la regle de similarite de 03, seul autre chemin
      de sortie possible a cette etape) ;
    - un fichier garde par 01, non deduplique, mais sans JSON d'extraction est
      classe TEXTE_VIDE (cas `stats["vides"]` de 02, qui ecrit un compteur
      anonyme). Un run 02 interrompu produirait la meme signature : c'est la
      limite connue du backfill, levee par P1 (ecritures a chaud).

Usage :
  PALIM_CLIENT=delacour DB_PASSWORD=... python registre_backfill.py --all
  PALIM_CLIENT=ncg      DB_PASSWORD=... python registre_backfill.py --copro 8050
  (sans DB_PASSWORD : lecture du secret admin du profil via Secrets Manager)
  Ajouter --dry-run pour n'afficher que le rapport, sans ecrire.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import unicodedata

import psycopg2
from psycopg2.extras import execute_values

import pipeline_config as pcfg
from content_filter import analyze_file_quality

# Aligne sur 01_filtrage.py : sert a distinguer FILTRAGE_PHOTO de FILTRAGE_AUTRE
# dans le journal de filtrage, qui ne conserve que la decision brute "EXCLURE".
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".webp"}


def _nfc(s):
    """Cle d'appariement : forme composee NFC. Les accents des fichiers crees
    sur Mac voyagent en NFD et chaque filesystem traverse (SMB, NTFS,
    GoogleDriveFS) restitue sa propre forme ; sans normalisation, le meme
    document apparait 'orphelin' de part et d'autre de la jointure."""
    return unicodedata.normalize("NFC", s or "")

COLS = ("source_file", "code_ncg", "nom_fichier", "taille_octets", "sha256", "signature",
        "statut", "motif", "etape", "ref_source_file", "score", "doc_type",
        "nb_caracteres", "nb_chunks", "run_id", "last_seen")

UPSERT = f"""
INSERT INTO ingestion_registre ({", ".join(COLS)})
VALUES %s
ON CONFLICT (source_file) DO UPDATE SET
    code_ncg        = EXCLUDED.code_ncg,
    nom_fichier     = EXCLUDED.nom_fichier,
    taille_octets   = COALESCE(EXCLUDED.taille_octets, ingestion_registre.taille_octets),
    sha256          = COALESCE(EXCLUDED.sha256, ingestion_registre.sha256),
    signature       = COALESCE(EXCLUDED.signature, ingestion_registre.signature),
    statut          = EXCLUDED.statut,
    motif           = EXCLUDED.motif,
    etape           = EXCLUDED.etape,
    ref_source_file = EXCLUDED.ref_source_file,
    score           = EXCLUDED.score,
    doc_type        = EXCLUDED.doc_type,
    nb_caracteres   = EXCLUDED.nb_caracteres,
    nb_chunks       = EXCLUDED.nb_chunks,
    run_id          = EXCLUDED.run_id,
    last_seen       = EXCLUDED.last_seen,
    updated_at      = now();
"""


def db_password():
    pw = os.environ.get("DB_PASSWORD", "")
    if pw:
        return pw
    if not pcfg.DB_SECRET_ADMIN:
        sys.exit("ERREUR : ni DB_PASSWORD ni secret_admin dans le profil client.")
    import boto3
    region = os.environ.get("AWS_REGION_SECRETS", "eu-west-1")
    raw = boto3.client("secretsmanager", region_name=region).get_secret_value(
        SecretId=pcfg.DB_SECRET_ADMIN)["SecretString"]
    # Deux formes coexistent : chaine brute (palim/delacour/ragadmin) ou JSON
    # {"DB_PASSWORD": ...} (palim/ragadmin, NCG). On tolere les deux.
    if raw.strip().startswith("{"):
        d = json.loads(raw)
        return d.get("DB_PASSWORD") or d.get("password") or d.get("DB_PASS")
    return raw


def connect():
    return psycopg2.connect(host=pcfg.require_db_host(), port=pcfg.DB_PORT,
                            dbname=pcfg.DB_NAME, user=pcfg.DB_USER_ADMIN,
                            password=db_password(), connect_timeout=20)


def scan_copro(code):
    """Croise les artefacts d'une copro. Retourne (rows, stats_lecture)."""
    paths = pcfg.paths_for(code)
    folder = paths["folder_name"]
    pref = folder + "\\"
    full = lambda rel: pref + rel

    rows = {}          # source_file -> dict de colonnes
    lu = {"filtrage": 0, "dedup": 0, "extraits": 0, "chunkes": 0}

    # ── 1. Journal de filtrage : la population totale vue a la source ──
    rap = paths["filtrage_report"]
    if not rap.exists():
        return None, f"pas de filtrage_rapport.json ({rap})"
    with open(rap, encoding="utf-8") as f:
        decisions = json.load(f).get("decisions", [])
    for d in decisions:
        rel = d.get("fichier") or ""
        if not rel:
            continue
        lu["filtrage"] += 1
        dec = d.get("decision")
        ext = (d.get("extension") or "").lower()
        r = {c: None for c in COLS}
        r["source_file"] = full(rel)
        r["code_ncg"] = code
        r["nom_fichier"] = os.path.basename(rel)
        if dec == "GARDER":
            r["statut"], r["etape"] = "DECOUVERT", "01"
        elif dec == "EXCLURE_GOOGLE_NATIF":
            r["statut"], r["motif"], r["etape"] = "REJETE", "FILTRAGE_GOOGLE_NATIF", "01"
        elif dec == "ERREUR_COPIE":
            r["statut"], r["motif"], r["etape"] = "ERREUR", "COPIE_KO", "01"
        else:  # EXCLURE
            r["statut"], r["etape"] = "REJETE", "01"
            r["motif"] = "FILTRAGE_PHOTO" if ext in IMAGE_EXTENSIONS else "FILTRAGE_AUTRE"
        rows[_nfc(r["source_file"])] = r

    # ── 2. Dedup exacte SHA-256 (00b) ──
    man = paths["per_copro"] / "dedup_manifest.json"
    if man.exists():
        with open(man, encoding="utf-8") as f:
            manifest = json.load(f)
        for sha, grp in manifest.items():
            kept, size = grp.get("kept"), grp.get("size")
            for rel in ([kept] if kept else []) + list(grp.get("removed") or []):
                sf = full(rel)
                r = rows.get(_nfc(sf))
                if r is None:
                    continue
                r["sha256"] = sha
                r["taille_octets"] = size
            for rel in (grp.get("removed") or []):
                sf = full(rel)
                r = rows.get(_nfc(sf))
                if r is None:
                    continue
                lu["dedup"] += 1
                r["statut"], r["motif"], r["etape"] = "REJETE", "DOUBLON_EXACT", "00b"
                r["ref_source_file"] = full(kept) if kept else None

    # ── 3. Signatures d'extraction (02), quand le checkpoint est present ──
    ckpt = paths["extraction_checkpoint"]
    if ckpt.exists():
        try:
            with open(ckpt, encoding="utf-8") as f:
                for rel, sig in (json.load(f).get("sigs") or {}).items():
                    r = rows.get(_nfc(full(rel)))
                    if r is not None and sig:
                        r["signature"] = sig
        except (json.JSONDecodeError, OSError):
            pass

    # ── 4. Ce que 02 a reellement extrait ──
    extraits = {}      # source_file -> chemin du JSON
    edir = pcfg.EXTRACTED_ROOT / folder
    for root, _, files in os.walk(edir):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            jp = os.path.join(root, fn)
            try:
                with open(jp, encoding="utf-8") as f:
                    doc = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            sf = doc.get("source_file")
            if not sf:
                continue
            lu["extraits"] += 1
            extraits[_nfc(sf)] = jp
            r = rows.get(_nfc(sf))
            if r is not None:
                r["nb_caracteres"] = doc.get("nb_caracteres")

    # ── 5. Ce que 03 a chunke ──
    chunkes = {}       # source_file -> [nb_chunks, doc_type]
    cj = paths["chunks_jsonl"]
    if cj.exists():
        with open(cj, encoding="utf-8") as f:
            for line in f:
                o = json.loads(line)
                sf = o.get("source_file")
                if not sf:
                    continue
                e = chunkes.setdefault(_nfc(sf), [0, o.get("doc_type"), sf])
                e[0] += 1
    lu["chunkes"] = len(chunkes)

    # doc_type_corrige (04) : plus fiable que celui pose par 03
    mj = paths["documents_metadata_jsonl"]
    if mj.exists():
        with open(mj, encoding="utf-8") as f:
            for line in f:
                o = json.loads(line)
                sf, dt = o.get("source_file"), o.get("doc_type_corrige") or o.get("doc_type")
                if _nfc(sf) in chunkes and dt:
                    chunkes[_nfc(sf)][1] = dt

    return {"rows": rows, "extraits": extraits, "chunkes": chunkes,
            "folder": folder, "lu": lu}, None


def resoudre(scan, en_base, run_id):
    """Attribue statut/motif final a chaque document garde par 01."""
    rows, extraits, chunkes = scan["rows"], scan["extraits"], scan["chunkes"]
    now = datetime.now(timezone.utc)
    infere = {"NON_EXPLOITABLE": 0, "DOUBLON_PROCHE": 0, "TEXTE_VIDE": 0}

    incoherences = {}

    for sf, r in rows.items():
        r["run_id"] = run_id
        r["last_seen"] = now

        # Le FAIT prime sur la DECISION : un document present dans la base est
        # INGERE, meme si 01 ou 00b avait decide de l'ecarter. Le cas existe
        # (00b retire le fichier de 'filtered' mais le JSON extrait d'un run
        # anterieur survit, donc 03 le chunke quand meme) et doit rester
        # visible : `statut='INGERE' AND ref_source_file IS NOT NULL` liste les
        # doublons exacts qui ont fuite dans le RAG.
        if sf in chunkes or sf in en_base:
            nb_shard, dt = chunkes.get(sf, (0, None, None))[:2]
            if dt:
                r["doc_type"] = dt
            if r["statut"] == "REJETE":
                k = f'INGERE_MALGRE_{r["motif"]}'
                incoherences[k] = incoherences.get(k, 0) + 1
            if sf in en_base:
                # nb_chunks porte le FAIT (ce qui est en base), pas le shard local :
                # le registre decrit l'etat reel du RAG. L'ecart shard/base est une
                # derive a signaler, pas a masquer.
                nb_db, sf_db = en_base[sf]
                # La forme STOCKEE est celle de la DB : la jointure SQL avec
                # chunks doit matcher octet a octet (NFD/NFC, cf. _nfc).
                r["source_file"] = sf_db
                r["nb_chunks"] = nb_db
                r["statut"], r["etape"], r["motif"] = "INGERE", "06b", None
                if sf not in chunkes:
                    incoherences["INGERE_HORS_SHARD"] = incoherences.get("INGERE_HORS_SHARD", 0) + 1
                elif nb_db != nb_shard:
                    incoherences["CHUNKS_HORS_SHARD"] = (
                        incoherences.get("CHUNKS_HORS_SHARD", 0) + nb_db - nb_shard)
            else:
                r["nb_chunks"] = nb_shard
                r["statut"], r["etape"], r["motif"] = "ERREUR", "06b", "CHARGEMENT_KO"
            continue

        if r["statut"] != "DECOUVERT":       # deja tranche par 01 ou 00b
            continue

        if sf in extraits:
            # Extrait mais jamais chunke : 03 l'a ecarte. Deux sorties possibles,
            # on rejoue le filtre qualite pour trancher (cf. docstring).
            motif = "DOUBLON_PROCHE"
            try:
                with open(extraits[sf], encoding="utf-8") as f:
                    doc = json.load(f)
                q = analyze_file_quality(doc.get("texte", ""), doc.get("nom_fichier", ""))
                if q["verdict"] == "SKIP":
                    motif = "NON_EXPLOITABLE"
            except (json.JSONDecodeError, OSError):
                pass
            r["statut"], r["motif"], r["etape"] = "REJETE", motif, "03"
            infere[motif] += 1
            continue

        # Garde par 01, non deduplique, aucun JSON d'extraction.
        r["statut"], r["motif"], r["etape"] = "REJETE", "TEXTE_VIDE", "02"
        infere["TEXTE_VIDE"] += 1

    return infere, incoherences


def ecrire(conn, rows, run_id, code, stats):
    with conn.cursor() as cur:
        execute_values(cur, UPSERT, [tuple(r[c] for c in COLS) for r in rows], page_size=1000)
        cur.execute(
            "INSERT INTO ingestion_runs (run_id, code_ncg, started_at, finished_at, ok, stats) "
            "VALUES (%s, %s, now(), now(), TRUE, %s) ON CONFLICT (run_id) DO UPDATE SET "
            "finished_at = now(), ok = TRUE, stats = EXCLUDED.stats",
            (run_id, code, json.dumps(stats, ensure_ascii=False)))
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description="Backfill du registre d'ingestion (P0).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--copro", help="code copro (canonique ou alias)")
    g.add_argument("--all", action="store_true", help="toutes les copros du profil client")
    ap.add_argument("--dry-run", action="store_true", help="rapport seul, aucune ecriture")
    args = ap.parse_args()

    codes = sorted(pcfg.COPRO_META) if args.all else [pcfg.resolve(args.copro)]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"Client {pcfg.CLIENT_CODE} ({pcfg.CLIENT_NAME}) — {len(codes)} copro(s)"
          f"{' [DRY-RUN]' if args.dry_run else ''}")

    conn = connect()
    total = {}
    orphelins_db = 0

    for code in codes:
        scan, err = scan_copro(code)
        if err:
            print(f"  {code:12} SAUTE : {err}")
            continue

        with conn.cursor() as cur:
            cur.execute("SELECT source_file, count(*) FROM chunks WHERE code_ncg = %s "
                        "GROUP BY source_file", (code,))
            en_base = {_nfc(sf): (n, sf) for sf, n in cur.fetchall()}

        infere, incoh = resoudre(scan, en_base, f"{code}-backfill-{stamp}")
        rows = list(scan["rows"].values())

        # Lignes de chunks en base sans document correspondant au registre :
        # chunks virtuels Airtable (08) ou reliquats. Comptes, jamais inventes.
        orph = [sf for sf in en_base if sf not in scan["rows"]]  # cles deja en NFC des 2 cotes
        orphelins_db += len(orph)

        par_statut = {}
        for r in rows:
            k = r["statut"] if r["statut"] not in ("REJETE", "ERREUR") else f'{r["statut"]}/{r["motif"]}'
            par_statut[k] = par_statut.get(k, 0) + 1
            total[k] = total.get(k, 0) + 1

        detail = " ".join(f"{k}={v}" for k, v in sorted(par_statut.items()))
        print(f"  {code:12} {len(rows):6} docs | {detail}"
              + (f" | orphelins_db={len(orph)}" if orph else "")
              + (" | " + " ".join(f"{k}={v}" for k, v in sorted(incoh.items())) if incoh else ""))

        if not args.dry_run:
            ecrire(conn, rows, f"{code}-backfill-{stamp}",
                   code, {"backfill": True, "lu": scan["lu"], "par_statut": par_statut,
                          "infere": infere, "incoherences": incoh,
                          "orphelins_db": len(orph)})

    print("\nTOTAL")
    for k, v in sorted(total.items(), key=lambda kv: -kv[1]):
        print(f"  {k:28} {v:7}")
    print(f"  {'(chunks DB sans doc registre)':28} {orphelins_db:7}")
    conn.close()


if __name__ == "__main__":
    main()
