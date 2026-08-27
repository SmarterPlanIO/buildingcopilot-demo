"""recette_rattrapage_ocr.py — valide un rattrapage de couches OCR degradees.

6 tests, du plus factuel au plus global. Lecture seule (user mcp_*_reader) : ne
modifie jamais la base. Reutilisable pour NCG en changeant PALIM_CLIENT et le
fichier de temoins.

Usage :
  PALIM_CLIENT=delacour python recette_rattrapage_ocr.py \
      --snapshot <snapshot_avant.json> --temoins <temoins.json> [--copro CODE]

T1 Temoins factuels : les faits verifies A LA MAIN sur le PDF source (vision)
   doivent etre presents dans le texte en base. C'est LE test qui prouve la
   qualite ; les autres mesurent l'ampleur et l'absence de casse.
T2 Score de charabia avant/apres sur les docs flagues (baisse attendue).
T3 Non-regression volumetrie : aucun document ne doit disparaitre.
T4 Docs non flagues inchanges : le rattrapage ne touche pas ce qui allait bien.
T5 Couverture : part des docs flagues effectivement re-traites.
T6 Sante globale : part du parc encore au-dessus du seuil de charabia.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent))
from ocr_quality import SEUIL_PROPRE, score_texte  # noqa: E402

CLIENT = os.environ.get("PALIM_CLIENT", "ncg")
DB = {
    "delacour": ("sp-rag-delacour-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com",
                 "mcp_delacour_reader", "palim/delacour/mcp_reader"),
    "ncg": ("sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com",
            "mcp_ncg_reader", "palim/mcp_ncg_reader"),
}

OK, KO, WARN = "  [OK]  ", "  [KO]  ", " [WARN] "
resultats = []


def note(test, ok, msg):
    resultats.append((test, ok))
    print(f"{OK if ok else KO}{test} : {msg}")


def connect():
    host, user, secret = DB[CLIENT]
    s = subprocess.run(["aws", "secretsmanager", "get-secret-value", "--secret-id", secret,
        "--region", "eu-west-1", "--query", "SecretString", "--output", "text"],
        capture_output=True, text=True).stdout.strip()
    pw = list(json.loads(s).values())[0] if s.startswith("{") else s
    return psycopg2.connect(host=host, port=5432, dbname="postgres", user=user,
                            password=pw, sslmode="require")


def texte_du_doc(cur, source_file, full=False):
    """Texte reconstitue d'un document (tous ses chunks, ou les 2 premiers)."""
    if full:
        cur.execute("SELECT STRING_AGG(text, ' ' ORDER BY chunk_index) FROM chunks "
                    "WHERE source_file = %s", (source_file,))
    else:
        cur.execute("SELECT STRING_AGG(LEFT(text, 2000), ' ' ORDER BY chunk_index) FROM chunks "
                    "WHERE source_file = %s AND chunk_index <= 1", (source_file,))
    r = cur.fetchone()
    return (r[0] or "") if r else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True, help="snapshot AVANT (json)")
    ap.add_argument("--temoins", required=True, help="temoins a verite terrain (json)")
    ap.add_argument("--copro", help="restreindre a une copro")
    args = ap.parse_args()

    snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    temoins = json.loads(Path(args.temoins).read_text(encoding="utf-8"))
    conn = connect()
    cur = conn.cursor()
    print(f"===== RECETTE RATTRAPAGE OCR — client {CLIENT} =====\n")

    # ---------- T1 : temoins factuels ----------
    print("--- T1 : temoins a verite terrain (verifies a la main sur le PDF source)")
    for t in temoins:
        if args.copro and t["code"] != args.copro:
            continue
        texte = texte_du_doc(cur, t["source_file"], full=True)
        if not texte:
            note(f"T1 {t['label']}", False, "document ABSENT de la base")
            continue
        manquants = [f for f in t["faits_attendus"] if f.lower() not in texte.lower()]
        trouves = len(t["faits_attendus"]) - len(manquants)
        note(f"T1 {t['label']}", not manquants,
             f"{trouves}/{len(t['faits_attendus'])} faits presents"
             + (f" | manquants : {manquants}" if manquants else ""))

    # ---------- T2 a T6 : mesures sur les docs flagues ----------
    print("\n--- T2 : score de charabia avant / apres (docs flagues)")
    cur.execute("""SELECT code_ncg, source_file,
                          STRING_AGG(LEFT(text, 2000), ' ' ORDER BY chunk_index)
                   FROM chunks WHERE chunk_index <= 1 AND source_file ILIKE '%.pdf'
                   GROUP BY 1, 2""")
    apres = {sf: (code, txt) for code, sf, txt in cur.fetchall()}

    flagues = [sf for sf, v in snap["textes"].items()
               if score_texte(v["texte"] or "") >= 0.35
               and (not args.copro or v["code"] == args.copro)]
    ameliores = degrades = disparus = 0
    av_tot = ap_tot = 0.0
    for sf in flagues:
        s_av = score_texte(snap["textes"][sf]["texte"] or "")
        if sf not in apres:
            disparus += 1
            continue
        s_ap = score_texte(apres[sf][1] or "")
        av_tot += s_av
        ap_tot += s_ap
        if s_ap < s_av - 0.05:
            ameliores += 1
        elif s_ap > s_av + 0.05:
            degrades += 1
    n = max(len(flagues) - disparus, 1)
    note("T2 baisse du charabia", ap_tot / n < av_tot / n * 0.7,
         f"score moyen {av_tot/n:.3f} -> {ap_tot/n:.3f} | {ameliores} ameliores, "
         f"{degrades} degrades sur {n} docs flagues")

    print("\n--- T3 : non-regression volumetrie (aucun document perdu)")
    cur.execute("SELECT code_ncg, COUNT(DISTINCT source_file), COUNT(*) FROM chunks GROUP BY 1")
    vol_ap = {c: {"documents": d, "chunks": k} for c, d, k in cur.fetchall()}
    pertes = []
    for code, v in snap["volumetrie"].items():
        if args.copro and code != args.copro:
            continue
        apres_v = vol_ap.get(code, {"documents": 0, "chunks": 0})
        if apres_v["documents"] < v["documents"]:
            pertes.append(f"{code} {v['documents']}->{apres_v['documents']} docs")
    note("T3 volumetrie", not pertes,
         "aucune perte de document" if not pertes else f"PERTES : {pertes}")

    print("\n--- T4 : documents NON flagues inchanges")
    sains = [sf for sf, v in snap["textes"].items()
             if score_texte(v["texte"] or "") < 0.20
             and (not args.copro or v["code"] == args.copro)][:400]
    modifies = [sf for sf in sains
                if sf in apres and (apres[sf][1] or "")[:500] != (snap["textes"][sf]["texte"] or "")[:500]]
    note("T4 docs sains intacts", len(modifies) <= len(sains) * 0.02,
         f"{len(modifies)}/{len(sains)} documents sains modifies (tolerance 2%)")

    print("\n--- T5 : couverture du rattrapage")
    note("T5 couverture", disparus == 0,
         f"{len(flagues)} docs flagues, {disparus} absents de la base apres rattrapage")

    print("\n--- T6 : sante globale du parc")
    scores = sorted(score_texte(t or "") for _, t in apres.values())
    if scores:
        med = scores[len(scores) // 2]
        p90 = scores[int(len(scores) * 0.9)]
        au_dessus = sum(1 for s in scores if s >= 0.35)
        note("T6 sante parc", med < SEUIL_PROPRE,
             f"mediane {med:.3f}, p90 {p90:.3f}, {au_dessus}/{len(scores)} docs encore >= 0.35 "
             f"({au_dessus/len(scores):.1%})")

    conn.close()
    ko = [t for t, ok in resultats if not ok]
    print(f"\n===== BILAN : {len(resultats)-len(ko)}/{len(resultats)} tests OK"
          + (f" | ECHECS : {ko}" if ko else "") + " =====")
    sys.exit(1 if ko else 0)


if __name__ == "__main__":
    main()
