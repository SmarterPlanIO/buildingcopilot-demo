import psycopg2

conn = psycopg2.connect(
    host="sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com",
    port=5432, dbname="postgres",
    user="ragadmin", password="SmarterRAG99!"
)
conn.autocommit = True
cur = conn.cursor()

cur.execute("TRUNCATE TABLE chunks;")
print("✅ Table chunks vidée")

cur.execute("TRUNCATE TABLE documents;")
print("✅ Table documents vidée")

cur.close()
conn.close()