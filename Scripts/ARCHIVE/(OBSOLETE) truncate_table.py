# À lancer une seule fois dans un script ou PowerShell
import psycopg2
conn = psycopg2.connect(host="sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com",
                        port=5432, dbname="postgres", user="ragadmin", password="SmarterRAG99!")
cur = conn.cursor()
cur.execute("TRUNCATE TABLE chunks;")
conn.commit()
print("✅ Table vidée")
cur.close(); conn.close()