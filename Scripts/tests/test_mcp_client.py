"""Tests LIVE du client MCP streamable-HTTP contre le serveur PALIM deploye.

Necessitent MCP_URL en variable d'env (URL complete avec slug). Sans elle, skip :
    MCP_URL="https://<lambda>/<slug>" pytest tests/test_mcp_client.py
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "Streamlit Cloud"))

pytestmark = pytest.mark.skipif(
    not os.environ.get("MCP_URL"), reason="MCP_URL non defini (test live)"
)


@pytest.fixture(scope="module")
def client():
    from mcp_client import McpClient
    return McpClient()


def test_initialize(client):
    result = client.initialize()
    assert result["serverInfo"]["name"] == "PALIM"


def test_tools_list_contrat_complet(client):
    tools = client.list_tools()
    names = {t["name"] for t in tools}
    assert len(names) == 13, f"attendu 13 tools, recu {sorted(names)}"
    for expected in ("PALIM_search_chunks", "PALIM_run_analytical_query",
                     "PALIM_assynco_get_copro", "PALIM_log_feedback"):
        assert expected in names
    for t in tools:  # schemas exploitables pour construire le toolConfig Converse
        assert t.get("inputSchema", {}).get("type") == "object", t["name"]


def test_call_tool_list_copros(client):
    result = client.call_tool("PALIM_list_copros", {})
    assert isinstance(result, dict)
    codes = {c.get("code_ncg") for c in result.get("copros", [])}
    assert "5757" in codes  # ABBE GREGOIRE (flotte GE) doit etre servie


def test_call_tool_search_chunks_scope(client):
    result = client.call_tool("PALIM_search_chunks", {
        "query": "derniere assemblee generale", "copro_codes": ["5757"], "top_k": 3,
    })
    assert isinstance(result, dict)
    results = result.get("results", [])
    assert results, "aucun passage retourne sur 5757"
    assert all(r.get("citation") for r in results)


def test_call_tool_erreur_scope_manquant(client):
    from mcp_client import McpError
    try:
        out = client.call_tool("PALIM_search_chunks", {"query": "assemblee generale"})
    except McpError:
        return  # erreur explicite = comportement attendu
    # Certains serveurs renvoient l'erreur en payload plutot qu'en isError
    assert "MISSING_COPRO_SCOPE" in str(out)
