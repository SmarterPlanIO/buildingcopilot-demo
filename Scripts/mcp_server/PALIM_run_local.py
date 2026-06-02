"""
PALIM_run_local.py — Lancement local du serveur MCP en stdio.

Pour tester avec MCP Inspector :
    npx @modelcontextprotocol/inspector \
        python "PALIM_run_local.py"

Variables d'env requises : DB_HOST, DB_USER, DB_PASSWORD (+ creds AWS pour Bedrock).
"""
from PALIM_server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
