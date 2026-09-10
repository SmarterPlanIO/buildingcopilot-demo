# -*- coding: utf-8 -*-
"""Tests du cache versionné des questions synthétiques (sq_cache.py, chantier A2).

Exécution sans pytest : PYTHONIOENCODING=utf-8 python tests/test_sq_cache.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sq_cache

PV, MODEL = "sq_v1", "eu.anthropic.claude-haiku-4-5-20251001-v1:0"


def _eligible(c):
    return (c.get("doc_type") in {"PV_AG", "RCP", "CONTRAT"}
            and c.get("chunk_index", 0) > 0
            and c.get("resolution_category") not in {"PROCEDURE_AG", "ELECTION_CS"})


def cas_validite():
    e = sq_cache.entree("Quel est le budget ?", PV, MODEL)
    assert sq_cache.valide(e, PV, MODEL)
    assert sq_cache.questions_de(e) == "Quel est le budget ?"
    # bump de prompt -> invalide (jamais servir d'anciennes sorties)
    assert not sq_cache.valide(e, "sq_v2", MODEL)
    # changement de modele -> invalide
    assert not sq_cache.valide(e, PV, "autre-modele")
    # entree corrompue -> invalide, jamais d'exception
    assert not sq_cache.valide("garbage", PV, MODEL)
    assert not sq_cache.valide(None, PV, MODEL)
    return "validité : match prompt+modèle exigé, corruption inoffensive"


def cas_skip_cache():
    e = sq_cache.entree(None, PV, MODEL)
    assert sq_cache.valide(e, PV, MODEL)
    assert sq_cache.questions_de(e) is None, "un SKIP caché doit rester un SKIP"
    e2 = sq_cache.entree("", PV, MODEL)
    assert sq_cache.questions_de(e2) is None, "chaîne vide = SKIP"
    return "le refus (SKIP) est caché et resservi comme tel"


def cas_roundtrip_atomique():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cache.json")
        cache = {"abc123": sq_cache.entree("Q ?", PV, MODEL),
                 "def456": sq_cache.entree(None, PV, MODEL, seed=True)}
        sq_cache.sauver(p, cache)
        assert not os.path.exists(p + ".tmp"), "le tmp doit être remplacé (écriture atomique)"
        relu = sq_cache.charger(p)
        assert relu == json.loads(json.dumps(cache)), "roundtrip fidèle"
        # fichier absent / corrompu -> cache vide, jamais bloquant
        assert sq_cache.charger(os.path.join(d, "absent.json")) == {}
        with open(p, "w", encoding="utf-8") as f:
            f.write("{corrompu")
        assert sq_cache.charger(p) == {}
    return "sauver/charger : atomique, roundtrip fidèle, corruption -> cache vide"


def cas_amorcage():
    rows = [
        # éligible avec questions -> valeur seed
        {"chunk_id": "a1", "doc_type": "PV_AG", "chunk_index": 3,
         "synthetic_questions": "Quel montant a été voté ?", "text": "x"},
        # éligible sans questions -> SKIP avéré
        {"chunk_id": "a2", "doc_type": "RCP", "chunk_index": 1,
         "synthetic_questions": "", "text": "x"},
        # inéligible (préambule) -> absent du cache
        {"chunk_id": "a3", "doc_type": "PV_AG", "chunk_index": 0,
         "synthetic_questions": "Q ?", "text": "x"},
        # inéligible (doc_type) -> absent
        {"chunk_id": "a4", "doc_type": "FACTURE", "chunk_index": 2, "text": "x"},
        # éligible, questions blanches -> SKIP
        {"chunk_id": "a5", "doc_type": "CONTRAT", "chunk_index": 2,
         "synthetic_questions": "   ", "text": "x"},
    ]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "sq.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.write("pas du json\n")  # ligne corrompue ignorée
        cache = sq_cache.amorcer_depuis_sq(p, _eligible, PV, MODEL)
    assert set(cache) == {"a1", "a2", "a5"}, cache.keys()
    assert sq_cache.questions_de(cache["a1"]) == "Quel montant a été voté ?"
    assert sq_cache.questions_de(cache["a2"]) is None
    assert sq_cache.questions_de(cache["a5"]) is None
    assert all(e.get("seed") for e in cache.values()), "entrées amorcées marquées seed"
    assert all(sq_cache.valide(e, PV, MODEL) for e in cache.values())
    # shard absent -> cache vide sans exception
    assert sq_cache.amorcer_depuis_sq(os.path.join("nulle", "part.jsonl"), _eligible, PV, MODEL) == {}
    return "amorçage : éligibles seulement, SKIP avéré, seed marqué, robuste"


def cas_seconde_passe_zero_appel():
    """Simule la partition hits/misses de 05b : après amorçage complet, zéro à générer."""
    rows = [{"chunk_id": f"c{i}", "doc_type": "PV_AG", "chunk_index": 1,
             "synthetic_questions": ("Q ?" if i % 3 else ""), "text": "x"} for i in range(30)]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "sq.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        cache = sq_cache.amorcer_depuis_sq(p, _eligible, PV, MODEL)
    a_generer = [r for r in rows
                 if not (r["chunk_id"] in cache
                         and sq_cache.valide(cache[r["chunk_id"]], PV, MODEL))]
    assert a_generer == [], "cache complet => aucun appel Bedrock"
    # bump de prompt => tout repart en génération
    a_generer_v2 = [r for r in rows
                    if not (r["chunk_id"] in cache
                            and sq_cache.valide(cache[r["chunk_id"]], "sq_v2", MODEL))]
    assert len(a_generer_v2) == 30, "bump prompt_version => 100% régénéré"
    return "partition 05b : cache complet = 0 appel ; bump version = 100% régénéré"


def main():
    cas = [cas_validite, cas_skip_cache, cas_roundtrip_atomique,
           cas_amorcage, cas_seconde_passe_zero_appel]
    for f in cas:
        print(f"  OK  {f():<64} [{f.__name__}]")
    print(f"\n{len(cas)}/{len(cas)} cas passes.")


if __name__ == "__main__":
    main()
