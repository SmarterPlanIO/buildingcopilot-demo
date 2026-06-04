import os
import psycopg2
conn = psycopg2.connect(host="sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com", port=5432, dbname="postgres", user="ragadmin", password=os.environ.get("DB_PASSWORD", ""))
cur = conn.cursor()
cur.execute("""
    SELECT doc_type, COUNT(*), LEFT(text, 150)
    FROM chunks
    WHERE source_file ILIKE '%%TARIEL%%' OR source_file ILIKE '%%5390%%'
    GROUP BY doc_type, LEFT(text, 150)
    ORDER BY doc_type;
""")
for row in cur.fetchall():
    print(f"  doc_type={row[0]}  chunks={row[1]}")
    print(f"  aperçu: {row[2]}\n")
cur.close(); conn.close()