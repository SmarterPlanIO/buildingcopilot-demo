# -*- coding: utf-8 -*-
"""Datasets 1+2 : les VRAIES questions (Langfuse = appels MCP Claude Teams ;
chat_sessions = harness Streamlit). Sortie : liste brute + classification par
type de question, pour savoir A QUOI une fiche doit servir."""
import base64, json, os, re, sys, urllib.request
from collections import Counter
import psycopg2

SCRIPTS = r"G:/Mon Drive/Projet SmarterPlan/Sales/Prospects/NCG/202512 Mission Déploiement IA interne/Scripts"
sys.path.insert(0, SCRIPTS)
import pipeline_config as pcfg

# ── Langfuse ──
pk, sk, host = os.environ["LF_PK"], os.environ["LF_SK"], os.environ["LF_HOST"]
auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()


def get(path):
    req = urllib.request.Request(host + path, headers={"Authorization": "Basic " + auth})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


traces = []
for page in range(1, 40):
    data = get(f"/api/public/traces?limit=100&page={page}").get("data", [])
    if not data:
        break
    traces.extend(data)

lf_queries = []
lf_overview_codes = Counter()
for t in traces:
    inp = t.get("input")
    if isinstance(inp, str):
        try:
            inp = json.loads(inp)
        except Exception:
            inp = {}
    if not isinstance(inp, dict):
        continue
    name = t.get("name") or ""
    if name == "PALIM_copro_overview":
        lf_overview_codes[str(inp.get("code_ncg"))] += 1
    qtxt = inp.get("query") or inp.get("question")
    if qtxt and name in ("PALIM_search_chunks", "PALIM_search_dossiers"):
        lf_queries.append((name.replace("PALIM_", ""), str(qtxt)[:170],
                           inp.get("copro_codes") or inp.get("code_ncg"), (t.get("timestamp") or "")[:10]))

print(f"LANGFUSE : {len(traces)} traces, {len(lf_queries)} requetes de recherche")
print(f"  copro_overview appele sur : {dict(lf_overview_codes)}")
print("\n--- requetes Langfuse (recherche documentaire) ---")
for name, qtxt, cc, d in lf_queries:
    print(f"  {d} [{name:15s}] {str(cc)[:22]:22s} | {qtxt}")

# ── Harness ──
conn = psycopg2.connect(host=pcfg.require_db_host(), port=pcfg.DB_PORT, dbname=pcfg.DB_NAME,
                        user=pcfg.DB_USER_ADMIN, password=os.environ["DB_PASSWORD"])
cur = conn.cursor()
cur.execute("SELECT session_id, code_ncg, chat_history, updated_at FROM chat_sessions ORDER BY updated_at")
hp = []
for sid, code, hist, upd in cur.fetchall():
    for m in (hist or []):
        if m.get("role") == "user" and m.get("content"):
            hp.append((str(upd)[:10], code, m["content"][:170]))
conn.close()
print(f"\nHARNESS : {len(hp)} prompts utilisateur")
print("--- prompts harness ---")
for d, code, qtxt in hp:
    print(f"  {d} [{code or '-':9s}] {qtxt}")

# ── Classification par type (regles lexicales, transparentes) ──
TYPES = [
    ("temporel / dernier / en vigueur", r"dernier|derni[eè]re|plus r[ée]cent|en vigueur|actuel|à jour|quand|date|depuis"),
    ("sinistre / dossier / où en est", r"sinistre|d[ée]g[aâ]t|dde|fuite|dossier|o[uù] en est|expert|indemn"),
    ("assurance / police / prime", r"assur|police|prime|franchise|mri|garantie|couvert"),
    ("AG / PV / vote / résolution", r"\bag\b|assembl|pv\b|proc[eè]s.verbal|vot|r[ée]solution|ordre du jour|majorit"),
    ("contrat / prestataire", r"contrat|prestataire|entretien|maintenance|ascenseur|chauff|nettoyage|gardien"),
    ("travaux / devis", r"travaux|devis|ravalement|toiture|[ée]tanch|r[ée]paration"),
    ("finances / comptes / budget / charges", r"compte|budget|charge|appel de fonds|impay|tr[ée]sorerie|fonds travaux|bilan|montant"),
    ("juridique / RCP / règlement", r"rcp|r[eè]glement|loi|article|droit|juridique|contest|valable|l[ée]gal"),
    ("organes / personnes / syndic / CS", r"conseil syndical|syndic\b|pr[ée]sident|membre|copropri[ée]taire|gestionnaire|qui "),
    ("inventaire / liste / combien", r"liste|combien|tous les|toutes les|inventaire|quels|quelles|recens"),
    ("portefeuille / multi-copro", r"grands ensembles|portefeuille|copropri[ée]t[ée]s|copros|parc|compar"),
]
def classify(txt):
    t = txt.lower()
    return [name for name, rx in TYPES if re.search(rx, t)] or ["autre"]

allq = [q for _, q, _, _ in lf_queries] + [q for _, _, q in hp]
c = Counter()
for qtxt in allq:
    for k in classify(qtxt):
        c[k] += 1
print(f"\n=== CLASSIFICATION ({len(allq)} questions, multi-label) ===")
for k, v in c.most_common():
    print(f"  {k:40s} {v:4d}  ({100*v/len(allq):.0f}%)")
