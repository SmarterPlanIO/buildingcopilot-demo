import json

METADATA = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\documents_metadata.jsonl"

records = []
with open(METADATA, "r", encoding="utf-8") as f:
    for line in f:
        records.append(json.loads(line))

nulls = [r for r in records if not r.get("sous_type")]
print(f"{len(nulls)} docs sans sous_type sur {len(records)}\n")

# Répartition par doc_type_corrige
by_type = {}
for r in nulls:
    dt = r.get("doc_type_corrige") or r["doc_type"]
    by_type.setdefault(dt, []).append(r)

print(f"{'doc_type_corrige':20s} {'Count':>6s}")
print(f"{'-'*20} {'-'*6}")
for dt, docs in sorted(by_type.items(), key=lambda x: -len(x[1])):
    print(f"{dt:20s} {len(docs):6d}")

# Échantillon par type
for dt, docs in sorted(by_type.items(), key=lambda x: -len(x[1]))[:5]:
    print(f"\n--- {dt} ({len(docs)} docs sans sous_type) ---")
    for r in docs[:8]:
        resume = (r.get("resume_une_ligne") or "-")[:80]
        print(f"  {r['nom_fichier'][:50]:50s} → {resume}")