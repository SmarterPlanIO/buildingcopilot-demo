#!/usr/bin/env python3
"""
qualifier_publipostage.py — P1 de PLAN_PUBLIPOSTAGE_FACTORISATION.md

Mesure la REDONDANCE INTERNE de chaque document (part des chunks dont le texte
est strictement identique a un autre chunk du MEME document) et qualifie le
document. Cas vise : les bundles de publipostage, ou un corps commun (PV,
convocation) est recopie une fois par destinataire.

NE MODIFIE AUCUN CHUNK. Lecture de `chunks`, ecriture des seuls attributs
d'observation sur `documents` (chunks_bruts, chunks_uniques,
redondance_interne, profil_repetitif). Aucun appel LLM, aucun re-run pipeline :
tout se calcule depuis la base.

Seuils (deduits de la mesure du 23/08, cf. plan §6) :
  PUBLIPOSTAGE       redondance >= 0.80 ET >= 20 chunks ET >= 2 textes uniques
  REPETITIF_SUSPECT  redondance dans [0.60, 0.80[  -> observation seule

La bande 60-80 % ne doit RIEN declencher : elle contient des documents
legitimes verifies (rapport d'expertise 8050 a 1 560 chunks / 519 uniques,
document de synthese de diagnostic a 1 341 / 447) dont la repetition vient de
tableaux et d'en-tetes recurrents.

Usage :
  PALIM_CLIENT=ncg      PYTHONIOENCODING=utf-8 python qualifier_publipostage.py --dry-run
  PALIM_CLIENT=delacour PYTHONIOENCODING=utf-8 python qualifier_publipostage.py
  (sans DB_PASSWORD : lecture du secret admin du profil via Secrets Manager)
"""
import argparse
import sys

import psycopg2
from psycopg2.extras import execute_values

import pipeline_config as pcfg
from registre_backfill import connect  # meme resolution de credentials

SEUIL_PUBLIPOSTAGE = 0.80
SEUIL_SUSPECT = 0.60
MIN_CHUNKS = 20
MIN_UNIQUES = 2

# md5(text) plutot que text : meme resultat, GROUP BY beaucoup plus leger sur
# des centaines de milliers de chunks parfois tres longs.
SQL_MESURE = """
SELECT source_file, code_ncg, doc_type,
       count(*)                  AS bruts,
       count(DISTINCT md5(text)) AS uniques
FROM chunks
GROUP BY source_file, code_ncg, doc_type
"""

SQL_MAJ = """
UPDATE documents d SET
    chunks_bruts       = v.bruts,
    chunks_uniques     = v.uniques,
    redondance_interne = v.redondance,
    profil_repetitif   = v.profil
FROM (VALUES %s) AS v(source_file, bruts, uniques, redondance, profil)
WHERE d.source_file = v.source_file
"""


def profil(bruts, uniques):
    """Qualification d'un document, ou None s'il n'a rien de repetitif."""
    if bruts <= 0:
        return None, 0.0
    redondance = (bruts - uniques) / bruts
    if redondance >= SEUIL_PUBLIPOSTAGE and bruts >= MIN_CHUNKS and uniques >= MIN_UNIQUES:
        return "PUBLIPOSTAGE", redondance
    if redondance >= SEUIL_SUSPECT:
        return "REPETITIF_SUSPECT", redondance
    return None, redondance


def main():
    ap = argparse.ArgumentParser(description="Qualification publipostage (P1, observation).")
    ap.add_argument("--dry-run", action="store_true", help="rapport seul, aucune ecriture")
    ap.add_argument("--top", type=int, default=15, help="nb de documents detailles au rapport")
    args = ap.parse_args()

    conn = connect()
    with conn.cursor() as cur:
        cur.execute(SQL_MESURE)
        lignes = cur.fetchall()

    rows, stats = [], {}
    for source_file, code, doc_type, bruts, uniques in lignes:
        p, red = profil(bruts, uniques)
        rows.append((source_file, bruts, uniques, round(red, 4), p))
        cle = p or "(aucun)"
        s = stats.setdefault(cle, {"docs": 0, "chunks": 0, "redondants": 0, "copros": set()})
        s["docs"] += 1
        s["chunks"] += bruts
        s["redondants"] += bruts - uniques
        s["copros"].add(code)

    print(f"Client {pcfg.CLIENT_CODE} ({pcfg.CLIENT_NAME}) — {len(rows)} documents mesures"
          f"{' [DRY-RUN]' if args.dry_run else ''}\n")
    print(f"{'PROFIL':20} {'DOCS':>6} {'CHUNKS':>9} {'REDONDANTS':>11} {'COPROS':>7}")
    for cle in ("PUBLIPOSTAGE", "REPETITIF_SUSPECT", "(aucun)"):
        s = stats.get(cle)
        if s:
            print(f"{cle:20} {s['docs']:6} {s['chunks']:9} {s['redondants']:11} {len(s['copros']):7}")

    gain = stats.get("PUBLIPOSTAGE", {}).get("redondants", 0)
    total = sum(s["chunks"] for s in stats.values())
    print(f"\nFactorisation P2 : {gain} chunks retirables sur {total} "
          f"({100 * gain / total:.1f} % de la base)" if total else "")

    detail = sorted((r for r in rows if r[4] == "PUBLIPOSTAGE"), key=lambda r: r[1] - r[2], reverse=True)
    if detail:
        print(f"\nTop {min(args.top, len(detail))} documents PUBLIPOSTAGE :")
        print(f"{'BRUTS':>7} {'UNIQ':>6} {'RED%':>5}  FICHIER")
        for sf, b, u, red, _ in detail[:args.top]:
            print(f"{b:7} {u:6} {100 * red:4.0f}%  {sf.split(chr(92))[-1][:62]}")

    if args.dry_run:
        print("\n[DRY-RUN] aucune ecriture.")
        conn.close()
        return

    a_ecrire = [r for r in rows if r[4] is not None]
    with conn.cursor() as cur:
        execute_values(cur, SQL_MAJ, a_ecrire, page_size=500)
        maj = cur.rowcount
    conn.commit()
    conn.close()
    print(f"\n{maj} ligne(s) de `documents` qualifiee(s) sur {len(a_ecrire)} document(s) concerne(s)"
          + ("" if maj == len(a_ecrire)
             else f" — {len(a_ecrire) - maj} sans ligne dans `documents` (chunks orphelins)"))


if __name__ == "__main__":
    main()
