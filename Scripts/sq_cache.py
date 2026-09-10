# -*- coding: utf-8 -*-
"""Cache versionné des questions synthétiques (chantier A2 de PLAN_05B_QUESTIONS_SYNTHETIQUES.md).

Module pur (aucun appel réseau), testé par tests/test_sq_cache.py.

Clé = chunk_id (content-addressed : md5(source_file || texte) côté 03, donc un même
texte dans un même fichier donne toujours la même clé — le cache est correct par
construction). Chaque entrée porte la version de prompt et le modèle : changer l'un
ou l'autre invalide l'entrée au lieu de servir d'anciennes sorties.

Le refus de Haiku (SKIP) est caché comme un succès : c'est ~2 appels sur 3, ne pas
le cacher reviendrait à repayer éternellement l'appel le plus inutile.

Amorçage (seed) : les shards `chunks_avec_embeddings_sq.jsonl` existants contiennent
déjà les sorties d'un run 05b complet. `amorcer_depuis_sq` les recycle : chunk
éligible avec questions -> valeur ; chunk éligible sans questions -> SKIP avéré.
Les entrées amorcées sont marquées `seed: true` (héritage température ~1.0, conservé
par décision du 26/08 : leur fonction de pont lexical est intacte).

Limite documentée : une évolution du chunking change les chunk_id -> cache inopérant
sur ce cas (re-runs à chunking constant seulement). Pas de purge des entrées mortes.
"""
import json
import os

# Bump à chaque modification de SQ_PROMPT dans 05b (invalide tout le cache non-seed).
PROMPT_VERSION = "sq_v1"

# Marqueur interne : questions absentes = refus Haiku (ou texte trop court).
_SKIP = None


def entree(questions, prompt_version, model_id, seed=False):
    """Construit une entrée de cache. questions=None -> SKIP caché."""
    e = {"q": questions if questions else None,
         "pv": prompt_version, "model": model_id}
    if seed:
        e["seed"] = True
    return e


def valide(e, prompt_version, model_id):
    """Une entrée n'est servie que si prompt et modèle correspondent.
    Les seeds suivent la même règle : ils ont été générés avec le prompt et le
    modèle courants (seule la température différait, héritage assumé)."""
    return (isinstance(e, dict)
            and e.get("pv") == prompt_version
            and e.get("model") == model_id)


def questions_de(e):
    """Valeur d'une entrée valide : la chaîne de questions, ou None (SKIP)."""
    return e.get("q") or None


def charger(path):
    """Charge le cache ; fichier absent ou corrompu -> cache vide (jamais bloquant)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def sauver(path, cache):
    """Écriture atomique (tmp + replace) : un run interrompu ne corrompt pas le cache."""
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, path)


def amorcer_depuis_sq(sq_path, est_eligible, prompt_version, model_id):
    """Peuple un cache depuis un shard _sq existant (zéro appel Bedrock).

    Pour chaque ligne éligible du shard : questions non vides -> entrée valeur ;
    questions vides -> SKIP avéré (le shard est la sortie d'un run complet, un
    éligible sans questions y est un refus réel de Haiku).
    """
    cache = {}
    try:
        f = open(sq_path, "r", encoding="utf-8")
    except (FileNotFoundError, OSError):
        return cache
    with f:
        for line in f:
            try:
                c = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not est_eligible(c):
                continue
            q = (c.get("synthetic_questions") or "").strip()
            cache[c["chunk_id"]] = entree(q or None, prompt_version, model_id, seed=True)
    return cache
