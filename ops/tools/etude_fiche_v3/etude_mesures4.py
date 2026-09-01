# -*- coding: utf-8 -*-
"""T24-T26 : forme d'un index thematique (sous_type x annee), proxy 'contrat en
vigueur' par date, dates de suivi Assynco des dossiers."""
import os, sys
import psycopg2
SCRIPTS = r"G:/Mon Drive/Projet SmarterPlan/Sales/Prospects/NCG/202512 Mission Déploiement IA interne/Scripts"
sys.path.insert(0, SCRIPTS)
import pipeline_config as pcfg
conn = psycopg2.connect(host=pcfg.require_db_host(), port=pcfg.DB_PORT, dbname=pcfg.DB_NAME,
                        user=pcfg.DB_USER_ADMIN, password=os.environ["DB_PASSWORD"])
cur = conn.cursor()
def q(sql, p=None):
    cur.execute(sql, p); return cur.fetchall()

print("=== T24. index thematique sous_type x annee — copro demo 5757 (docs par sous_type, derniere annee, dernier doc) ===")
rows = q("""SELECT sous_type, COUNT(*), MIN(annee), MAX(annee),
                   (array_agg(nom_fichier ORDER BY date_document DESC NULLS LAST))[1]
            FROM documents WHERE code_ncg='5757' AND COALESCE(sous_type,'')<>''
            GROUP BY 1 ORDER BY 2 DESC LIMIT 14""")
for st, n, a, b, last in rows:
    print(f"  {st:18s} n={n:3d} {a}-{b}  dernier: {(last or '')[:55]}")
print("  taille estimee de cet index (14 lignes x ~120 o) : ~1,7 Ko")

print("\n=== T25. proxy 'contrat en vigueur' = dernier CONTRAT date par sous_type (5757 et 8050) ===")
for code in ("5757", "8050"):
    rows = q("""SELECT DISTINCT ON (sous_type) sous_type, date_document, statut, nom_fichier
                FROM documents WHERE code_ncg=%s AND doc_type='CONTRAT' AND COALESCE(sous_type,'')<>''
                AND date_document IS NOT NULL
                ORDER BY sous_type, date_document DESC""", (code,))
    print(f"  {code} :")
    for st, d, statut, nf in rows[:8]:
        print(f"     {st:18s} {d}  statut_llm={str(statut):9s} {nf[:50]}")

print("\n=== T26. dossiers : taux de remplissage des dates de suivi (source Airtable) ===")
cols = ["date_declaration", "date_mission_expert", "date_premiere_visite", "date_depot_rapport",
        "date_reglement", "date_derniere_relance", "date_cloture", "date_prescription"]
n_at = q("SELECT COUNT(*) FROM dossiers WHERE airtable_record_id IS NOT NULL")[0][0]
n_all = q("SELECT COUNT(*) FROM dossiers")[0][0]
print(f"  dossiers sources Assynco : {n_at}/{n_all}")
for c in cols:
    a = q(f"SELECT COUNT(*) FROM dossiers WHERE {c} IS NOT NULL AND airtable_record_id IS NOT NULL")[0][0]
    print(f"     {c:24s} {a:3d}/{n_at}  ({100*a/n_at:.0f}% des dossiers Assynco)")
print("  -> pour les dossiers RAG (05c), seule date_ouverture existe :",
      q("SELECT COUNT(date_ouverture) FROM dossiers WHERE airtable_record_id IS NULL")[0][0], "/",
      q("SELECT COUNT(*) FROM dossiers WHERE airtable_record_id IS NULL")[0][0])
conn.close()
