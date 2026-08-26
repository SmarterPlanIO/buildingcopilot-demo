"""Comptage REEL des tokens Bedrock + cout Claude Haiku 4.5.

Remplace les formules de cout hardcodees des etapes 03/04/05b, qui
sous-estimaient le cout d'un facteur ~5-10 (verifie contre la facture AWS
de mai 2026 : Haiku reel ~87 $ vs ~17 $ auto-reporte).

Pourquoi elles mentaient :
  - 04 : `llm_calls * 1000 * 0.80 / 1e6` => suppose 1000 tokens/appel, prix
    Haiku 3 (0.80 $/M), et NE COMPTE PAS la sortie.
  - 03 : `calls * 1500 * 0.0000008` => idem, 1500 tokens flat, sortie ignoree.
  - 05b : `len(eligible) * 0.0001` => forfait par chunk, deconnecte des tokens.

Realite : les docs syndic font 3 000-20 000 tokens en entree, la sortie JSON
(metadonnees, questions) est facturee 5 $/M. D'ou l'ecart.

Ce module lit le champ `usage` que Bedrock renvoie dans CHAQUE reponse
invoke_model, donc le cout colle au vrai nombre de tokens, quelle que soit
la taille des documents. Thread-safe (les etapes parallelisent en threads).

Prix Bedrock Claude Haiku 4.5 (region eu, mai 2026), USD par token :
"""
import threading

HAIKU_IN_PER_TOK = 1.00 / 1_000_000   # 1.00 $ / million tokens en entree
HAIKU_OUT_PER_TOK = 5.00 / 1_000_000  # 5.00 $ / million tokens en sortie

_lock = threading.Lock()
_usage = {"calls": 0, "in_tok": 0, "out_tok": 0}


def track(result):
    """Accumule les tokens d'une reponse invoke_model deja parsee (json.loads).

    No-op silencieux si `usage` est absent (compatibilite descendante)."""
    u = (result or {}).get("usage") or {}
    it = u.get("input_tokens", 0) or 0
    ot = u.get("output_tokens", 0) or 0
    with _lock:
        _usage["calls"] += 1
        _usage["in_tok"] += it
        _usage["out_tok"] += ot


def cost():
    """Cout USD accumule depuis le debut du process."""
    with _lock:
        return _usage["in_tok"] * HAIKU_IN_PER_TOK + _usage["out_tok"] * HAIKU_OUT_PER_TOK


def summary():
    """Dict {calls, in_tok, out_tok, cost} pour l'affichage de fin d'etape."""
    with _lock:
        c = _usage["in_tok"] * HAIKU_IN_PER_TOK + _usage["out_tok"] * HAIKU_OUT_PER_TOK
        return {
            "calls": _usage["calls"],
            "in_tok": _usage["in_tok"],
            "out_tok": _usage["out_tok"],
            "cost": c,
        }


def format_line():
    """Ligne prete a imprimer, format homogene entre les etapes."""
    s = summary()
    return (
        f"  Cout Haiku REEL      : ${s['cost']:.4f} "
        f"({s['calls']} appels, {s['in_tok']:,} tok in / {s['out_tok']:,} tok out)"
    )
