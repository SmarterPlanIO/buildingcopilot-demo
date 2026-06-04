"""
DIAGNOSTIC DB — Inventaire rapide de la base sans appel Bedrock
================================================================
Vérifie la cohérence des données en base : doc_types, fichiers SINISTRE,
doublons, chunks sans embedding, etc.

Usage :
  python diag_db_inventory.py [NOM_COPRO]
"""
import os
import sys
import psycopg2
from collections import Counter

DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

SEP = "=" * 70


def main():
    copro_filter = sys.argv[1] if len(sys.argv) > 1 else None

    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )
    conn.autocommit = True

    print(f"\n{SEP}")
    print(f"DIAGNOSTIC DB — Inventaire")
    print(f"Copro : {copro_filter or '(toutes)'}")
    print(SEP)

    where = "WHERE copropriete = %s" if copro_filter else ""
    params = [copro_filter] if copro_filter else []

    with conn.cursor() as cur:
        # 1. Copropriétés disponibles
        cur.execute("SELECT copropriete, COUNT(*) FROM chunks GROUP BY copropriete ORDER BY copropriete")
        copros = cur.fetchall()
        print(f"\nCopropriétés en base :")
        for c, cnt in copros:
            marker = " ◄" if copro_filter and c == copro_filter else ""
            print(f"  {c:40s} : {cnt:5d} chunks{marker}")

        # 2. Répartition doc_type
        cur.execute(f"""
            SELECT doc_type, COUNT(*), COUNT(DISTINCT source_file)
            FROM chunks {where}
            GROUP BY doc_type ORDER BY COUNT(*) DESC
        """, params)
        print(f"\nRépartition par doc_type :")
        for dt, cnt, nf in cur.fetchall():
            print(f"  {dt:20s} : {cnt:5d} chunks, {nf:3d} fichiers")

        # 3. Détail SINISTRE
        where_sin = "WHERE doc_type = 'SINISTRE'" + (f" AND copropriete = %s" if copro_filter else "")
        params_sin = [copro_filter] if copro_filter else []

        cur.execute(f"""
            SELECT source_file, nom_fichier, copropriete,
                   COUNT(*) as n_chunks,
                   MIN(chunk_index) as ci_min, MAX(chunk_index) as ci_max,
                   array_agg(DISTINCT unnest_themes) as all_themes
            FROM chunks
            LEFT JOIN LATERAL unnest(themes) as unnest_themes ON true
            {where_sin}
            GROUP BY source_file, nom_fichier, copropriete
            ORDER BY copropriete, source_file
        """, params_sin)
        sinistres = cur.fetchall()

        print(f"\n{'─'*60}")
        print(f"Fichiers SINISTRE détaillés ({len(sinistres)} fichiers)")
        print(f"{'─'*60}")
        for sf, fn, copro, nc, cmin, cmax, themes in sinistres:
            print(f"\n  📄 {fn} ({copro})")
            print(f"     {nc} chunks (index {cmin}–{cmax})")
            print(f"     Thèmes : {themes}")
            print(f"     Path   : {sf}")

        # 4. Chunks sans embedding (problème d'ingestion)
        cur.execute(f"""
            SELECT COUNT(*) FROM chunks {where}
            AND embedding IS NULL
        """.replace("AND", "WHERE" if not where else "AND"), params)
        null_emb = cur.fetchone()[0]
        if null_emb:
            print(f"\n⚠️  {null_emb} chunks SANS embedding ! (ne seront jamais trouvés)")
        else:
            print(f"\n✅ Tous les chunks ont un embedding")

        # 5. Chunks sans text_search (BM25 cassé)
        cur.execute(f"""
            SELECT COUNT(*) FROM chunks {where}
            AND text_search IS NULL
        """.replace("AND", "WHERE" if not where else "AND"), params)
        null_ts = cur.fetchone()[0]
        if null_ts:
            print(f"⚠️  {null_ts} chunks SANS text_search ! (BM25 ignorera ces chunks)")
        else:
            print(f"✅ Tous les chunks ont un index text_search")

        # 6. Doublons potentiels (même source_file + chunk_index)
        cur.execute(f"""
            SELECT source_file, chunk_index, COUNT(*)
            FROM chunks {where}
            GROUP BY source_file, chunk_index
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
            LIMIT 20
        """, params)
        dupes = cur.fetchall()
        if dupes:
            print(f"\n⚠️  {len(dupes)} doublons détectés (même source_file + chunk_index) :")
            for sf, ci, cnt in dupes[:10]:
                print(f"  {cnt}x : chunk_index={ci} dans {sf[:80]}")
        else:
            print(f"✅ Aucun doublon source_file + chunk_index")

        # 7. total_chunks cohérence (ON CONFLICT DO NOTHING piège)
        cur.execute(f"""
            SELECT source_file, nom_fichier,
                   COUNT(*) as actual, MAX(total_chunks) as declared
            FROM chunks {where}
            GROUP BY source_file, nom_fichier
            HAVING COUNT(*) != MAX(total_chunks)
            ORDER BY ABS(COUNT(*) - MAX(total_chunks)) DESC
            LIMIT 20
        """, params)
        mismatches = cur.fetchall()
        if mismatches:
            print(f"\n⚠️  {len(mismatches)} fichiers avec total_chunks incohérent :")
            for sf, fn, actual, declared in mismatches[:10]:
                print(f"  {fn} : {actual} en base vs {declared} déclaré")
                if actual < declared:
                    print(f"     → chunks manquants ! Possible ON CONFLICT DO NOTHING")
        else:
            print(f"✅ total_chunks cohérent partout")

        # 8. Fichiers dont doc_type a pu être écrasé (piège historique)
        cur.execute(f"""
            SELECT nom_fichier, doc_type, COUNT(*)
            FROM chunks {where}
            AND (
                LOWER(nom_fichier) LIKE '%%sinistre%%'
                OR LOWER(nom_fichier) LIKE '%%anomalie%%'
                OR LOWER(nom_fichier) LIKE '%%constat%%'
                OR LOWER(source_file) LIKE '%%sinistre%%'
            )
            AND doc_type != 'SINISTRE'
            GROUP BY nom_fichier, doc_type
        """.replace("AND (", "WHERE (" if not where else "AND ("), params)
        wrong_type = cur.fetchall()
        if wrong_type:
            print(f"\n⚠️  Fichiers qui semblent être des sinistres mais ont un MAUVAIS doc_type :")
            for fn, dt, cnt in wrong_type:
                print(f"  ❌ {fn} → classé {dt} au lieu de SINISTRE ({cnt} chunks)")
        else:
            print(f"✅ Aucun fichier sinistre mal classé détecté")

    conn.close()
    print(f"\n{SEP}\nFIN\n{SEP}")


if __name__ == "__main__":
    main()
