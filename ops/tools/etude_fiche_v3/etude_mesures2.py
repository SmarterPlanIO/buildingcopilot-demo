# -*- coding: utf-8 -*-
"""T11-T13 : ce que les vraies questions exigent, confronte a ce que la base sait."""
import os, sys
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


print("=== T11. questions AG les plus frequentes : indexables depuis `resolutions` ? ===")
print("  (part des copros ayant >=1 resolution ETABLIE sur le sujet, et nb median par copro)")
THEMES = [
    ("budget previsionnel", "BUDGET"),
    ("approbation des comptes", "COMPTE"),
    ("conseil syndical (election/membres)", "CONSEIL SYNDICAL"),
    ("syndic (mandat/designation)", "SYNDIC"),
    ("travaux", "TRAVAUX"),
    ("ravalement", "RAVALEMENT"),
    ("assurance", "ASSURANCE"),
    ("fonds travaux / ALUR", "FONDS"),
    ("contrat", "CONTRAT"),
]
n_copros = one("SELECT COUNT(DISTINCT code_ncg) FROM resolutions")
for lab, kw in THEMES:
    rows = q("""SELECT code_ncg, COUNT(*) FROM resolutions
                WHERE resultat IN ('adoptee','rejetee') AND objet_court ILIKE %s
                GROUP BY 1""", (f"%{kw}%",))
    ns = sorted(c for _, c in rows)
    med = ns[len(ns) // 2] if ns else 0
    print(f"  {lab:38s} copros couvertes {len(rows):2d}/{n_copros}  median/copro={med}")

print("\n=== T11b. le meme, TOUTES resolutions (etablies + indeterminees a signal) ===")
for lab, kw in THEMES[:5]:
    rows = q("""SELECT code_ncg, COUNT(*) FROM resolutions WHERE objet_court ILIKE %s GROUP BY 1""", (f"%{kw}%",))
    print(f"  {lab:38s} copros couvertes {len(rows):2d}/{n_copros}")

print("\n=== T12. chronologie des travaux votes : couverture par annee (copro 5757 demo + 8050) ===")
for code in ("5757", "8050", "5390"):
    rows = q("""SELECT EXTRACT(YEAR FROM date_ag)::int, COUNT(*) FILTER (WHERE resultat='adoptee'),
                       COUNT(*) FILTER (WHERE resultat='indetermine')
                FROM resolutions WHERE code_ncg=%s AND objet_court ILIKE '%%TRAVAUX%%' AND date_ag IS NOT NULL
                GROUP BY 1 ORDER BY 1""", (code,))
    print(f"  {code}: " + ", ".join(f"{y}:{a}a/{i}i" for y, a, i in rows))
print("  (a = adoptees, i = indeterminees)")
print("  AG par an (toutes copros) : distinct (copro,date_ag) =", one("SELECT COUNT(DISTINCT (code_ncg, date_ag)) FROM resolutions WHERE date_ag IS NOT NULL"))
print("  PV_AG sans date_document :", one("""SELECT COUNT(*) FROM documents WHERE COALESCE(doc_type_corrige,doc_type)='PV_AG' AND date_document IS NULL"""),
      "/", one("SELECT COUNT(*) FROM documents WHERE COALESCE(doc_type_corrige,doc_type)='PV_AG'"))

print("\n=== T13. 'ou en est le dossier X ?' (35% des questions) : richesse des dossiers ===")
nd = one("SELECT COUNT(*) FROM dossiers")
SQL_ET = "SELECT COUNT(*) FROM dossiers WHERE jsonb_array_length(COALESCE(etapes,'[]'::jsonb))>0"
print(f"  etapes non vides   : {100*one(SQL_ET)/nd:.1f}%")
SQL_RI = "SELECT COUNT(*) FROM dossiers WHERE length(COALESCE(resume_ia,''))>40"
print(f"  resume_ia present  : {100*one(SQL_RI)/nd:.1f}%")
print(f"  documents_lies>=1  : {100*one('SELECT COUNT(*) FROM dossiers WHERE cardinality(documents_lies)>0')/nd:.1f}%")
print("  derniere activite DERIVABLE (max date_document des documents lies) :")
rows = q("""SELECT d.dossier_id, d.date_ouverture, MAX(doc.date_document) derniere, COUNT(doc.source_file)
            FROM dossiers d LEFT JOIN documents doc ON doc.source_file = ANY(d.documents_lies)
            GROUP BY 1,2""")
avec = sum(1 for r in rows if r[2])
print(f"     dossiers avec une date de derniere activite : {100*avec/len(rows):.1f}%")
print("  echantillon (dossier, ouverture -> derniere activite, n docs lies) :")
for r in [x for x in rows if x[2]][:5]:
    print(f"     {r[0][:55]:55s} {r[1]} -> {r[2]}  ({r[3]} docs)")
print("  etapes : exemple de structure :", q("SELECT etapes FROM dossiers WHERE jsonb_array_length(COALESCE(etapes,'[]'::jsonb))>0 LIMIT 1")[0][0] if one("SELECT COUNT(*) FROM dossiers WHERE jsonb_array_length(COALESCE(etapes,'[]'::jsonb))>0") else "aucune")
print("  resume_ia : exemple :", (q("SELECT LEFT(resume_ia,300) FROM dossiers WHERE length(COALESCE(resume_ia,''))>40 ORDER BY random() LIMIT 1") or [["aucun"]])[0][0])

print("\n=== T14. le dossier LEMEAU (le plus demande du harness) : ce que la base en sait ===")
for r in q("""SELECT dossier_id, statut, date_ouverture, montant_estime, cardinality(documents_lies),
                     length(COALESCE(resume_ia,'')), jsonb_array_length(COALESCE(etapes,'[]'::jsonb)), airtable_record_id IS NOT NULL
              FROM dossiers WHERE nom_dossier ILIKE '%%LEMEAU%%' OR lese_nom ILIKE '%%LEMEAU%%'"""):
    print("  ", r)
conn.close()
