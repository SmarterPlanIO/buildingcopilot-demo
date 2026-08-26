"""
smoke_langfuse_local.py — Test local de l'intégration Langfuse du serveur MCP.

Valide : clés, compat version (langfuse==2.60.4), et que le module PALIM_tracing
émet bien une trace + spans vers le projet Langfuse configuré. N'a PAS besoin de
DB/Bedrock/mcp (chemin retrieval déjà couvert par la régression).

Usage (depuis Scripts/mcp_server/, avec le venv qui a langfuse==2.60.4) :
    LANGFUSE_PUBLIC_KEY=pk-lf-... LANGFUSE_SECRET_KEY=sk-lf-... \
    python tests/smoke_langfuse_local.py

Attendu : "auth_check OK", une trace 'smoke_test_mcp' visible dans le projet
"PALIM MCP", avec 2 spans (embed_query, sql_retrieval) et un output.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import PALIM_config as cfg
import PALIM_tracing as lf


def main():
    if not (cfg.LANGFUSE_PUBLIC_KEY and cfg.LANGFUSE_SECRET_KEY):
        print("ECHEC : LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY absents de l'env.")
        return 1
    print(f"Host        : {cfg.LANGFUSE_HOST}")
    print(f"Public key  : {cfg.LANGFUSE_PUBLIC_KEY[:8]}...")

    # 1) auth_check direct (valide que les clés pointent vers un projet existant)
    client = lf._get_client()
    if client is None:
        print("ECHEC : client Langfuse non initialisé (clés ou version).")
        return 1
    try:
        client.auth_check()
        print("auth_check  : OK")
    except Exception as exc:
        print(f"ECHEC auth_check : {type(exc).__name__}: {exc}")
        return 1

    # 2) Trace + spans via le VRAI module PALIM_tracing (mime un appel de tool)
    tr = lf.start_trace(
        "smoke_test_mcp",
        input={"query": "test connectivité tracing MCP", "copro_codes": ["5390"]},
        tags=["mcp", "smoke_test"],
    )
    if tr is None:
        print("ECHEC : start_trace a retourné None malgré des clés présentes.")
        return 1

    sp1 = lf.span(tr, "embed_query", chars=33, mode="equilibre")
    lf.end_span(sp1, dim=1024)
    sp2 = lf.span(tr, "sql_retrieval", n_copros=1, prefilter_active=False)
    lf.end_span(sp2, n_rows=12)
    lf.update_trace(
        tr,
        output={"n_results": 12, "inferred_scope": "single", "warnings": []},
        metadata={"latency_ms": 0, "max_chunks": 12},
    )

    trace_id = getattr(tr, "id", None) or getattr(tr, "trace_id", None)
    try:
        url = tr.get_trace_url()
    except Exception:
        url = None

    lf.flush()
    print(f"trace_id    : {trace_id}")
    if url:
        print(f"URL         : {url}")
    print("OK : trace + 2 spans envoyés. Vérifier le projet 'PALIM MCP' dans Langfuse.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
