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
import pipeline_config as pcfg  # noqa: E402
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
    ap.add_argument("--echantillon", type=int, default=30,
                    help="taille de l'echantillon arbitre par Haiku (T2)")
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
    # T2 : le score heuristique est INUTILISABLE comme juge ici (57 % de faux
    # positifs sur les tableaux, et le separateur de colonnes ` | ` gonfle sa
    # composante ponctuation). Le juge est Haiku, sur un echantillon des docs
    # dont le texte a REELLEMENT change (~0,03 $ par passage de recette).
    print("\n--- T2 : qualite reelle avant/apres (arbitrage Haiku, echantillon)")
    cur.execute("""SELECT code_ncg, source_file,
                          STRING_AGG(LEFT(text, 2000), ' ' ORDER BY chunk_index)
                   FROM chunks WHERE chunk_index <= 1 AND source_file ILIKE '%.pdf'
                   GROUP BY 1, 2""")
    apres = {sf: (code, txt) for code, sf, txt in cur.fetchall()}

    flagues = [sf for sf, v in snap["textes"].items()
               if score_texte(v["texte"] or "") >= 0.35
               and (not args.copro or v["code"] == args.copro)]
    disparus = [sf for sf in flagues if sf not in apres]
    modifies = [sf for sf in flagues if sf in apres
                and (apres[sf][1] or "")[:400] != (snap["textes"][sf]["texte"] or "")[:400]]
    if not modifies:
        note("T2 qualite", False, "aucun document au texte modifie : rattrapage sans effet")
    else:
        import random

        import boto3

        from ocr_quality import _arbitrage_haiku
        random.seed(11)
        ech = random.sample(modifies, min(args.echantillon, len(modifies)))
        bedrock = boto3.client("bedrock-runtime", region_name="eu-west-1")
        av = sum(1 for sf in ech
                 if _arbitrage_haiku(snap["textes"][sf]["texte"] or "", bedrock) == "PROPRE")
        ap = sum(1 for sf in ech if _arbitrage_haiku(apres[sf][1] or "", bedrock) == "PROPRE")
        note("T2 qualite (Haiku)", ap > av,
             f"{len(modifies)} docs re-OCRises | juges PROPRE : {av}/{len(ech)} "
             f"({av/len(ech):.0%}) -> {ap}/{len(ech)} ({ap/len(ech):.0%})")

    # T3 : une baisse du nombre de documents n'est pas forcement une perte — la
    # dedup exacte SHA-256 (00b) retire des doublons byte-identiques, et les copros
    # ingerees avant son existence en perdent legitimement au premier re-passage
    # (Delacour 27/08 : 46/46 disparitions expliquees par les manifests de dedup).
    print("\n--- T3 : non-regression volumetrie (hors doublons retires par la dedup)")
    cur.execute("SELECT code_ncg, COUNT(DISTINCT source_file) FROM chunks GROUP BY 1")
    docs_ap = dict(cur.fetchall())
    pertes, dedup_total = [], 0
    for code, v in snap["volumetrie"].items():
        if args.copro and code != args.copro:
            continue
        manque = v["documents"] - docs_ap.get(code, 0)
        if manque <= 0:
            continue
        man = Path(pcfg.per_copro_dir(code)) / "dedup_manifest.json"
        n_dedup = 0
        if man.exists():
            d = json.loads(man.read_text(encoding="utf-8"))
            n_dedup = sum(len(e["removed"]) for e in d.values())
        dedup_total += min(manque, n_dedup)
        if manque > n_dedup:
            pertes.append(f"{code} -{manque} docs (dedup n'en explique que {n_dedup})")
    note("T3 volumetrie", not pertes,
         f"aucune perte inexpliquee ({dedup_total} docs retires par la dedup exacte)"
         if not pertes else f"PERTES INEXPLIQUEES : {pertes}")

    print("\n--- T4 : documents NON flagues inchanges")
    sains = [sf for sf, v in snap["textes"].items()
             if score_texte(v["texte"] or "") < 0.20
             and (not args.copro or v["code"] == args.copro)][:400]
    sains_modifies = [sf for sf in sains
                      if sf in apres and (apres[sf][1] or "")[:500] != (snap["textes"][sf]["texte"] or "")[:500]]
    note("T4 docs sains intacts", len(sains_modifies) <= len(sains) * 0.02,
         f"{len(sains_modifies)}/{len(sains)} documents sains modifies (tolerance 2%)")

    print("\n--- T5 : couverture du rattrapage")
    note("T5 couverture", len(disparus) <= dedup_total,
         f"{len(flagues)} docs flagues, {len(modifies)} re-traites, {len(disparus)} absents "
         f"(doublons retires par la dedup : {dedup_total})")

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
