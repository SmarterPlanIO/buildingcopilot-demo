import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2, pipeline_config as p
c = psycopg2.connect(host=p.require_db_host(), port=p.DB_PORT, dbname=p.DB_NAME,
                     user=p.DB_USER_ADMIN, password=os.environ["DB_PASSWORD"]).cursor()
c.execute("SELECT code_ncg, immatriculation, nom_residence, adresse FROM copros WHERE code_ncg='AJ6978050'")
print("Nocard          :", c.fetchone())
c.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE nom_residence IS NULL) FROM copros")
print("copros / sans nom :", c.fetchone(), "  (cible finale : (25, 0))")
c.execute("""SELECT COUNT(DISTINCT d.source_file) FROM documents d JOIN chunks k USING (source_file)
             WHERE d.doc_type_corrige IS NOT NULL AND k.doc_type <> d.doc_type_corrige
               AND k.doc_type <> 'BORDEREAU_AR'""")
print("docs désalignés  :", c.fetchone(), "  (cible finale : (0,))")
