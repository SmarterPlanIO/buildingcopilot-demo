"""Client MCP streamable-HTTP minimal pour le backend PALIM (plan P0, option A).

Parle le protocole MCP en JSON-RPC sur HTTP (POST unique par requete, serveur
stateless) : initialize, tools/list, tools/call. Utilise par agent.py (boucle
agentique) et testable hors Streamlit.

Resolution de l'URL (jamais en dur dans le repo) :
  1. variable d'env MCP_URL (tests, CLI) ;
  2. st.secrets["mcp"]["url"] (deploiement Streamlit Cloud).
"""
from __future__ import annotations

import json
import os
import uuid

import requests

_PROTOCOL_VERSION = "2025-03-26"
_TIMEOUT_S = 60


class McpError(RuntimeError):
    """Erreur transport ou erreur JSON-RPC renvoyee par le serveur MCP."""


def _resolve_url() -> str:
    url = os.environ.get("MCP_URL", "").strip()
    if url:
        return url
    try:
        import streamlit as st
        try:
            return st.secrets["mcp"]["url"]
        except (KeyError, TypeError):
            pass
    except Exception:
        pass
    raise McpError("URL MCP introuvable : definir MCP_URL (env) ou [mcp] url dans st.secrets")


def _parse_response(resp: requests.Response) -> dict:
    """Reponse JSON pure ou flux SSE (event/data) -> objet JSON-RPC."""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise McpError(f"flux SSE sans ligne data (statut {resp.status_code})")
    return resp.json()


class McpClient:
    """Session MCP : un initialize par instance, puis des appels tools/*."""

    def __init__(self, url: str | None = None):
        self.url = url or _resolve_url()
        self._http = requests.Session()
        self._session_id: str | None = None
        self._initialized = False

    # ── transport ───────────────────────────────────────────────────────────
    def _post(self, payload: dict, retry: bool = True) -> dict | None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            resp = self._http.post(self.url, json=payload, headers=headers, timeout=_TIMEOUT_S)
        except requests.RequestException as e:
            if retry:
                return self._post(payload, retry=False)
            raise McpError(f"transport MCP : {e}") from e
        if resp.status_code >= 400:
            if retry and resp.status_code >= 500:
                return self._post(payload, retry=False)
            raise McpError(f"HTTP {resp.status_code} : {resp.text[:300]}")
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        if "id" not in payload:  # notification : pas de reponse attendue
            return None
        data = _parse_response(resp)
        if data.get("error"):
            raise McpError(f"JSON-RPC : {data['error']}")
        return data.get("result", {})

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method}
        if params is not None:
            payload["params"] = params
        return self._post(payload)

    # ── protocole ───────────────────────────────────────────────────────────
    def initialize(self) -> dict:
        result = self._rpc("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "palim-streamlit-agent", "version": "0.8.0"},
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True
        return result

    def _ensure_init(self):
        if not self._initialized:
            self.initialize()

    def list_tools(self) -> list[dict]:
        """[{name, description, inputSchema}, ...] tels qu'exposes par le serveur."""
        self._ensure_init()
        return self._rpc("tools/list", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None) -> dict | str:
        """Resultat du tool : dict si le serveur renvoie du JSON, sinon texte brut.

        Un tool en erreur metier (isError) leve McpError avec le message serveur.
        """
        self._ensure_init()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        raw = "\n".join(t for t in texts if t)
        if result.get("isError"):
            raise McpError(f"tool {name} : {raw[:500]}")
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
