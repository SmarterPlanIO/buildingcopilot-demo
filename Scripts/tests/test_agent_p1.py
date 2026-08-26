"""Tests LIVE de la boucle agentique (plan P1, option A) — criteres du plan.

Couteux (appels Sonnet reels, ~0,2 $ le run complet) : gates par DEUX variables
d'env pour ne jamais tourner par accident :
    MCP_URL="https://<lambda>/<slug>" AGENT_LIVE=1 pytest tests/test_agent_p1.py
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "Streamlit Cloud"))

pytestmark = pytest.mark.skipif(
    not (os.environ.get("MCP_URL") and os.environ.get("AGENT_LIVE")),
    reason="tests live agent : definir MCP_URL et AGENT_LIVE=1",
)


def _run(question, copros=None):
    from agent import run_agent, MAX_TOOL_ITERATIONS
    res = run_agent(question, copro_codes=copros)
    assert res.iterations <= MAX_TOOL_ITERATIONS
    assert res.answer.strip()
    return res


def test_factuel_scope_appelle_search_chunks():
    res = _run("Quelle est la date de la derniere assemblee generale ?", ["5757"])
    names = [tc["name"] for tc in res.tool_calls]
    assert "PALIM_search_chunks" in names
    # le perimetre impose est respecte dans les arguments
    for tc in res.tool_calls:
        if tc["name"] == "PALIM_search_chunks":
            assert tc["arguments"].get("copro_codes") == ["5757"]


def test_juridique_charge_le_skill_avant_de_repondre():
    res = _run("Quelle majorite faut-il pour modifier le reglement de copropriete ?", ["5757"])
    names = [tc["name"] for tc in res.tool_calls]
    assert "charger_skill" in names
    skill_pos = names.index("charger_skill")
    args = res.tool_calls[skill_pos]["arguments"]
    assert args.get("nom") == "ncg-note-juridique"
    # jamais de nom de tool dans la reponse visible (Bloc 3)
    assert "PALIM_" not in res.answer and "charger_skill" not in res.answer


def test_pole_rodin_resolu_en_codes():
    res = _run("Combien de sinistres en cours sur le pole Rodin ?")
    args_all = str([tc["arguments"] for tc in res.tool_calls])
    assert "5750" in args_all and "5784" in args_all and "5440" in args_all
    assert "PALIM_" not in res.answer


def test_analytique_parc_entier():
    res = _run("Combien de documents au total par copropriete sur tout le parc ?")
    names = [tc["name"] for tc in res.tool_calls]
    assert "PALIM_run_analytical_query" in names
