"""
ÉTAPE 9b — Table `resolutions` : le nœud décisionnel du graphe (C2).

Lit les chunks PV_AG en base, regroupe les fragments d'une même résolution
(marqueur « [Suite résolution …] » du chunker), indexe chaque résolution
reconstituée via resolution_index (résultat CALCULÉ du décompte ou LU dans la
proclamation — jamais généré), et upserte la table `resolutions`.

Lance :
    DB_PASSWORD=... python 09b_resolutions.py --copro 8050
    DB_PASSWORD=... python 09b_resolutions.py --all

Upsert per-copro : DELETE WHERE code_ncg + INSERT (jamais de TRUNCATE global).
resolution_id content-addressed (code + source_file + premier chunk_id) : stable
cross-run tant que le chunking ne change pas. Zéro appel Bedrock.
Tier-2 : à relancer après 06b quand des PV_AG ont bougé (même gate que 09).
"""
import argparse
import hashlib
import os
from collections import Counter, defaultdict

import psycopg2
from psycopg2.extras import execute_values

import pipeline_config as pcfg
from resolution_index import index_document

parser = argparse.ArgumentParser(description="Table resolutions (C2) — per-copro ou --all.")
g = parser.add_mutually_exclusive_group(required=True)
g.add_argument("--copro", help="Code copro (toute graphie)")
g.add_argument("--all", action="store_true", help="Toutes les copros du registre client")
args, _ = parser.parse_known_args()

DB_PASSWORD = os.environ.get("DB_PASSWORD")
if not DB_PASSWORD:
    raise SystemExit("❌ DB_PASSWORD manquant. Lance : DB_PASSWORD=... python 09b_resolutions.py --all")

conn = psycopg2.connect(host=pcfg.require_db_host(), port=pcfg.DB_PORT, dbname=pcfg.DB_NAME,
                        user=pcfg.DB_USER_ADMIN, password=DB_PASSWORD)
cur = conn.cursor()

if args.copro:
    codes = [pcfg.resolve(args.copro)]
else:
    cur.execute("SELECT DISTINCT code_ncg FROM chunks WHERE code_ncg IS NOT NULL")
    en_base = {r[0] for r in cur.fetchall()}
    codes = sorted(set(pcfg.COPRO_META) & en_base)
    print(f"📌 --all : {len(codes)} copros du registre présentes en base")


def resolution_id(code, source_file, first_chunk_id):
    h = hashlib.sha1(f"{code}|{source_file}|{first_chunk_id}".encode("utf-8")).hexdigest()
    return f"res_{h[:20]}"


total_stats = Counter()
for code in codes:
    cur.execute("""
        SELECT c.chunk_id, c.chunk_index, c.text, c.source_file, d.date_document
        FROM chunks c
        LEFT JOIN documents d ON d.source_file = c.source_file
        WHERE c.code_ncg = %s AND c.doc_type = 'PV_AG' AND c.chunk_index > 0
              AND c.retrieval_exclu = FALSE
        ORDER BY c.source_file, c.chunk_index
    """, (code,))
    by_doc = defaultdict(list)
    dates = {}
    for chunk_id, idx, text, source_file, date_doc in cur.fetchall():
        by_doc[source_file].append((chunk_id, idx, text))
        dates[source_file] = date_doc

    rows, stats = [], Counter()
    for source_file, doc_chunks in by_doc.items():
        for r in index_document(doc_chunks):
            # Un groupe sans AUCUN signal de résolution (ni numéro, ni décompte même
            # illisible, ni proclamation, ni retrait) est du contenu de PV hors vote
            # (feuille de présence, annexes) : pas une ligne de la table resolutions.
            if (r["numero"] is None and r["resultat"] == "indetermine"
                    and not r["decompte"] and not r["proclamation_detectee"]
                    and "decompte_illisible" not in r["flags"]):
                stats["hors_vote_ignore"] += 1
                continue
            stats[r["resultat"]] += 1
            rows.append((
                resolution_id(code, source_file, r["chunk_ids"][0]),
                code, source_file, dates.get(source_file),
                r["numero"], r["objet_court"], r["chunk_ids"],
                (r["decompte"] or {}).get("pour"),
                (r["decompte"] or {}).get("contre"),
                (r["decompte"] or {}).get("abstention"),
                r["article_majorite"], r["resultat"], r["source_resultat"],
                r["confiance"], r["flags"],
            ))

    cur.execute("DELETE FROM resolutions WHERE code_ncg = %s", (code,))
    if rows:
        execute_values(cur, """
            INSERT INTO resolutions
            (resolution_id, code_ncg, source_file, date_ag, numero, objet_court,
             chunk_ids, decompte_pour, decompte_contre, decompte_abstention,
             article_majorite, resultat, source_resultat, confiance, flags)
            VALUES %s
            ON CONFLICT (resolution_id) DO UPDATE SET
                date_ag = EXCLUDED.date_ag, numero = EXCLUDED.numero,
                objet_court = EXCLUDED.objet_court, chunk_ids = EXCLUDED.chunk_ids,
                decompte_pour = EXCLUDED.decompte_pour,
                decompte_contre = EXCLUDED.decompte_contre,
                decompte_abstention = EXCLUDED.decompte_abstention,
                article_majorite = EXCLUDED.article_majorite,
                resultat = EXCLUDED.resultat, source_resultat = EXCLUDED.source_resultat,
                confiance = EXCLUDED.confiance, flags = EXCLUDED.flags,
                updated_at = now()
        """, rows)
    conn.commit()
    total_stats.update(stats)
    etabli = sum(v for k, v in stats.items() if k in ("adoptee", "rejetee", "retiree"))
    n = sum(v for k, v in stats.items() if k != "hors_vote_ignore")
    pct = f"{100 * etabli / n:.0f}%" if n else "n/a"
    print(f"  {code}: {n} résolutions retenues ({dict(stats)}) — établi {pct}")

n = sum(v for k, v in total_stats.items() if k != "hors_vote_ignore")
etabli = sum(v for k, v in total_stats.items() if k in ("adoptee", "rejetee", "retiree"))
print(f"\n✅ {n} résolutions en table sur {len(codes)} copro(s) : {dict(total_stats)}")
if n:
    print(f"   Taux de résultat établi (sur résolutions retenues) : {100 * etabli / n:.1f}%")
cur.close()
conn.close()
