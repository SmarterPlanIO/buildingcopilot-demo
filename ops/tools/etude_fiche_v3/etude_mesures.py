# -*- coding: utf-8 -*-
"""Batterie de mesures T2-T10 sur la base NCG (read-only) — chaque hypothese
de l'etude d'architecture est testee ici avant d'etre tenue pour vraie."""
import os, re, sys
import psycopg2

SCRIPTS = r"G:/Mon Drive/Projet SmarterPlan/Sales/Prospects/NCG/202512 Mission Déploiement IA interne/Scripts"
sys.path.insert(0, SCRIPTS)
import pipeline_config as pcfg

conn = psycopg2.connect(host=pcfg.require_db_host(), port=pcfg.DB_PORT, dbname=pcfg.DB_NAME,
                        user=pcfg.DB_USER_ADMIN, password=os.environ["DB_PASSWORD"])
cur = conn.cursor()


def q(sql, p=None):
    cur.execute(sql, p)
    return cur.fetchall()


def one(sql, p=None):
    return q(sql, p)[0][0]


def pct(num, den):
    return f"{100 * num / den:.1f}%" if den else "n/a"


print("=== T2a. couverture des METADONNEES documents (socle d'un index thematique) ===")
n = one("SELECT COUNT(*) FROM documents")
print(f"  documents : {n}")
print(f"  date_document renseignee : {pct(one('SELECT COUNT(*) FROM documents WHERE date_document IS NOT NULL'), n)}")
SQL_ST = "SELECT COUNT(*) FROM documents WHERE COALESCE(sous_type,'')<>''"
print(f"  sous_type renseigne      : {pct(one(SQL_ST), n)}")
print(f"  statut renseigne         : {pct(one('SELECT COUNT(*) FROM documents WHERE statut IS NOT NULL'), n)}")
SQL_RES = "SELECT COUNT(*) FROM documents WHERE length(COALESCE(resume,''))>40"
print(f"  resume (>40c)            : {pct(one(SQL_RES), n)}")
print(f"  parties_concernees       : {pct(one('SELECT COUNT(*) FROM documents WHERE cardinality(parties_concernees)>0'), n)}")
print("  sous_type top 15 :")
for st, c in q("SELECT sous_type, COUNT(*) FROM documents WHERE COALESCE(sous_type,'')<>'' GROUP BY 1 ORDER BY 2 DESC LIMIT 15"):
    print(f"     {st:18s} {c}")
print("  date renseignee par doc_type :")
for dt, tot, dated in q("""SELECT COALESCE(doc_type_corrige,doc_type), COUNT(*), COUNT(date_document)
                           FROM documents GROUP BY 1 ORDER BY 2 DESC LIMIT 12"""):
    print(f"     {dt:14s} {tot:6d}  {pct(dated, tot)}")

print("\n=== T2b. themes sur les chunks ===")
nc = one("SELECT COUNT(*) FROM chunks")
print(f"  chunks : {nc} ; avec themes : {pct(one('SELECT COUNT(*) FROM chunks WHERE cardinality(themes)>0'), nc)}")
print("  themes top 15 :", q("SELECT t, COUNT(*) FROM chunks, UNNEST(themes) t GROUP BY 1 ORDER BY 2 DESC LIMIT 15"))

print("\n=== T2c. qualite des resumes documents (echantillon aleatoire) ===")
for sf, dt, resume, pt in q("""SELECT nom_fichier, doc_type, resume, LEFT(premier_texte,160) FROM documents
                              WHERE length(COALESCE(resume,''))>40 AND doc_type IN ('CONTRAT','PV_AG','SINISTRE','COURRIER')
                              ORDER BY random() LIMIT 5"""):
    print(f"  [{dt}] {sf[:55]}")
    print(f"     RESUME : {resume[:220]}")
    print(f"     TEXTE  : {(pt or '').replace(chr(10), ' ')[:130]}")

print("\n=== T3. resolutions INDETERMINEES : bruit ou vraies resolutions ? ===")
ni = one("SELECT COUNT(*) FROM resolutions WHERE resultat='indetermine'")
print(f"  indeterminees : {ni}")
for lab, sql in [
    ("avec numero", "SELECT COUNT(*) FROM resolutions WHERE resultat='indetermine' AND numero IS NOT NULL"),
    ("groupe_orphelin", "SELECT COUNT(*) FROM resolutions WHERE resultat='indetermine' AND 'groupe_orphelin' = ANY(flags)"),
    ("decompte_incomplet", "SELECT COUNT(*) FROM resolutions WHERE resultat='indetermine' AND 'decompte_incomplet' = ANY(flags)"),
    ("decompte_illisible", "SELECT COUNT(*) FROM resolutions WHERE resultat='indetermine' AND 'decompte_illisible' = ANY(flags)"),
    ("majorite_absolue_requise", "SELECT COUNT(*) FROM resolutions WHERE resultat='indetermine' AND 'majorite_absolue_requise' = ANY(flags)"),
    ("resolution_tronquee", "SELECT COUNT(*) FROM resolutions WHERE resultat='indetermine' AND 'resolution_tronquee' = ANY(flags)"),
    ("objet = vrai en-tete (commence par numero)", r"SELECT COUNT(*) FROM resolutions WHERE resultat='indetermine' AND objet_court ~ '^\s*\d{1,2}\s*[-.:)]'"),
]:
    print(f"  {lab:44s} {pct(one(sql), ni)}")

print("\n=== T3b. troncature : cas 5750 res.05 (comptes 2021) — le chunk SUIVANT ===")
rows = q("""SELECT source_file, chunk_ids FROM resolutions
            WHERE code_ncg='5750' AND numero='05' AND objet_court LIKE '%%2021%%' LIMIT 1""")
if rows:
    sf, cids = rows[0]
    idx = one("SELECT chunk_index FROM chunks WHERE chunk_id=%s", (cids[0],))
    for i, t, dt in q("SELECT chunk_index, LEFT(text,260), doc_type FROM chunks WHERE source_file=%s AND chunk_index IN (%s,%s) ORDER BY chunk_index", (sf, idx, idx + 1)):
        print(f"  chunk {i} [{dt}] : {t.replace(chr(10), ' ')}")

print("\n=== T3c. documents PV : part sans AUCUNE resolution etablie ===")
for a, c in q("""SELECT etab>0, COUNT(*) FROM (
            SELECT source_file, COUNT(*) FILTER (WHERE resultat IN ('adoptee','rejetee','retiree')) etab
            FROM resolutions GROUP BY source_file) s GROUP BY 1"""):
    print(f"  a_des_etablies={a} : {c} documents")

print("\n=== T4. dossiers : qualite des champs qui pilotent 'dossiers chauds' ===")
nd = one("SELECT COUNT(*) FROM dossiers")
print(f"  dossiers : {nd}")
SQL_LESE = "SELECT COUNT(*) FROM dossiers WHERE COALESCE(lese_nom,'')='' OR lese_nom ILIKE '%%INCONNU%%'"
print(f"  lese vide/INCONNU        : {pct(one(SQL_LESE), nd)}")
print(f"  date_ouverture NULL      : {pct(one('SELECT COUNT(*) FROM dossiers WHERE date_ouverture IS NULL'), nd)}")
SQL_JAN = "SELECT COUNT(*) FROM dossiers WHERE to_char(date_ouverture,'MM-DD')='01-01'"
print(f"  date_ouverture = 01/01   : {pct(one(SQL_JAN), nd)}")
print(f"  montant_estime renseigne : {pct(one('SELECT COUNT(*) FROM dossiers WHERE montant_estime IS NOT NULL'), nd)}")
print("  montant_estime p50/p90/p99 :", q("SELECT percentile_cont(ARRAY[0.5,0.9,0.99]) WITHIN GROUP (ORDER BY montant_estime) FROM dossiers WHERE montant_estime IS NOT NULL")[0][0])
print(f"  montant_estime > 100 000 : {one('SELECT COUNT(*) FROM dossiers WHERE montant_estime > 100000')} dossiers")
print("  statut :", q("SELECT statut, COUNT(*) FROM dossiers GROUP BY 1 ORDER BY 2 DESC"))
print("  at_situation :", q("SELECT at_situation, COUNT(*) FROM dossiers GROUP BY 1 ORDER BY 2 DESC LIMIT 6"))
print(f"  documents_lies vide      : {pct(one('SELECT COUNT(*) FROM dossiers WHERE documents_lies IS NULL OR cardinality(documents_lies)=0'), nd)}")
print(f"  updated_at / derniere activite connue ? colonnes :", [r[0] for r in q("SELECT column_name FROM information_schema.columns WHERE table_name='dossiers' AND column_name LIKE '%%date%%' OR table_name='dossiers' AND column_name LIKE '%%at'")])

print("\n=== T5. doublons de PV : (copro, date_ag) portes par plusieurs source_file ===")
for nf, c in q("""SELECT n_files, COUNT(*) FROM (
            SELECT code_ncg, date_ag, COUNT(DISTINCT source_file) n_files FROM resolutions
            WHERE date_ag IS NOT NULL GROUP BY 1,2) s GROUP BY 1 ORDER BY 1"""):
    print(f"  {nf} fichier(s) pour la meme AG : {c} AG")

print("\n=== T6. watermark freshness : nb_documents stocke (colonne v1) vs live ===")
for code, stored, live in q("""SELECT s.code_ncg, s.nb_documents,
        (SELECT COUNT(DISTINCT source_file) FROM documents d WHERE d.code_ncg=s.code_ncg)
        FROM copro_synthese s ORDER BY 1 LIMIT 5"""):
    print(f"  {code}: stocke={stored} live={live} -> {'STALE a tort' if stored != live else 'ok'}")

print("\n=== T7. taille des fiches v2 (octets ; tokens ~ octets/4) ===")
print("  min/mediane/max :", q("SELECT MIN(pg_column_size(faits_v2)), percentile_cont(0.5) WITHIN GROUP (ORDER BY pg_column_size(faits_v2))::int, MAX(pg_column_size(faits_v2)) FROM copro_synthese WHERE faits_v2 IS NOT NULL")[0])
print("  part mediane de pv_recents :", q("SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY pg_column_size(faits_v2->'pv_recents')::float/pg_column_size(faits_v2)) FROM copro_synthese WHERE faits_v2 IS NOT NULL")[0][0])

print("\n=== T8. 'contrat en vigueur' par sous_type : derivable par regle ? ===")
for r in q("""SELECT code_ncg, sous_type, COUNT(*), COUNT(date_document), MAX(annee),
                     COUNT(*) FILTER (WHERE statut='actif')
              FROM documents WHERE doc_type='CONTRAT' AND COALESCE(sous_type,'')<>''
              GROUP BY 1,2 HAVING COUNT(*)>=2 ORDER BY 3 DESC LIMIT 10"""):
    print(f"  {r[0]} {r[1]:14s} n={r[2]:3d} dates={r[3]:3d} max_annee={r[4]} actifs={r[5]}")
print("  statut des CONTRAT :", q("SELECT statut, COUNT(*) FROM documents WHERE doc_type='CONTRAT' GROUP BY 1 ORDER BY 2 DESC"))

print("\n=== T9. echeance du mandat de syndic : extractible par regle ? (12 resolutions au hasard) ===")
rows = q("""SELECT r.code_ncg, r.date_ag, string_agg(c.text, ' ') FROM resolutions r
            JOIN chunks c ON c.chunk_id = ANY(r.chunk_ids)
            WHERE r.resultat='adoptee' AND r.objet_court ~* '(RENOUVELLEMENT|DESIGNATION|NOMINATION).{0,40}(SYNDIC|CABINET)|MANDAT DU SYNDIC|CONTRAT DE SYNDIC'
            GROUP BY 1,2 ORDER BY random() LIMIT 12""")
pat = re.compile(r"(jusqu.au\s+\d|prend(ra)?\s+fin|pour une dur[ée]e d|à compter du\s+\d|du \d{1,2}[/ .]\d{1,2}[/ .]\d{2,4}\s+au\s+\d|expir)", re.I)
hits = 0
for code, dag, txt in rows:
    m = pat.search(txt or "")
    hits += bool(m)
    ext = (txt or "")[max(0, m.start() - 30):m.end() + 70] if m else (txt or "")[:90]
    print(f"  [{code} {dag}] {'BORNE' if m else '  --  '} {ext.replace(chr(10), ' ')}")
print(f"  => bornes de mandat presentes dans {hits}/{len(rows)}")

print("\n=== T10. dossiers RAG vs Assynco (sync 08 a jour ?) ===")
for code, a, t in q("""SELECT code_ncg, COUNT(*) FILTER (WHERE airtable_record_id IS NOT NULL), COUNT(*)
                       FROM dossiers GROUP BY 1 ORDER BY 1"""):
    print(f"  {code}: assynco={a:3d} / total={t}")
conn.close()
