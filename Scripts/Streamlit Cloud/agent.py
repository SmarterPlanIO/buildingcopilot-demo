"""Boucle agentique PALIM pour l'app Streamlit (plan P1, option A).

Bedrock Converse (Sonnet 4.6) + tools du serveur MCP PALIM (contrat charge
dynamiquement via tools/list) + pseudo-tool charger_skill (skills embarques).
Le prompt systeme est instructions_system.md (derive v3.3) + catalogue des skills.

P1 : run_agent() non-streaming, testable en CLI (le streaming du texte final est
un raffinement UI prevu en P2). Usage CLI :
    MCP_URL="https://.../mcp/<slug>" PYTHONIOENCODING=utf-8 \
      python agent.py "ta question" --copros 5757 [--verbose]
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from mcp_client import McpClient, McpError
from skills import SkillsBundle

MODEL_ID = "eu.anthropic.claude-sonnet-4-6"
MAX_TOOL_ITERATIONS = 8       # tours d'outils max par question (garde anti-boucle)
MAX_TOKENS_PER_TURN = 4096
MAX_TOOL_RESULT_CHARS = 30_000  # troncature des retours d'outils (protection contexte)
_PRICE_IN, _PRICE_OUT = 3.0 / 1e6, 15.0 / 1e6  # Sonnet 4.6 USD/token
_PRICE_CACHE_W, _PRICE_CACHE_R = 3.75 / 1e6, 0.30 / 1e6  # ecriture 1.25x, lecture 0.1x
# Prompt caching Converse : cache le prompt systeme (26k chars) et le contrat des
# tools entre les iterations ET entre les questions (TTL ~5 min). Desactive tout
# seul si le modele/la region le refuse (ValidationException au 1er appel).
ENABLE_PROMPT_CACHE = True

# Libelles metier des etapes (jamais de nom de tool a l'ecran — Bloc 3)
STEP_LABELS = {
    "PALIM_search_chunks": "consultation des documents",
    "PALIM_get_full_document": "chargement d'un document",
    "PALIM_get_chunks": "relecture des passages cites",
    "PALIM_search_dossiers": "consultation des dossiers",
    "PALIM_run_analytical_query": "analyse du portefeuille",
    "PALIM_list_copros": "identification de la copropriété",
    "PALIM_discover_copros": "identification de la copropriété",
    "PALIM_copro_overview": "fiche de synthèse de la copropriété",
    "PALIM_assynco_get_copro": "vérification du dossier assurance",
    "PALIM_assynco_list_polices": "vérification des polices d'assurance",
    "PALIM_assynco_search_sinistres": "vérification du suivi des sinistres",
    "PALIM_get_visite_3d": "recherche d'une visite 3D",
    "PALIM_log_feedback": "enregistrement du retour",
    "charger_skill": "préparation de la méthode de travail",
}


@dataclass
class AgentResult:
    answer: str
    tool_calls: list[dict] = field(default_factory=list)  # {name, arguments, ok, chars}
    iterations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    stop_reason: str = ""

    @property
    def cost_usd(self) -> float:
        return (self.input_tokens * _PRICE_IN + self.output_tokens * _PRICE_OUT
                + self.cache_write_tokens * _PRICE_CACHE_W
                + self.cache_read_tokens * _PRICE_CACHE_R)


def _load_system_prompt(client: str = "ncg") -> str:
    path = Path(__file__).parent / "skills_bundle" / client / "instructions_system.md"
    return path.read_text(encoding="utf-8")


def _tool_config(mcp: McpClient, bundle: SkillsBundle) -> dict:
    tools = []
    for t in mcp.list_tools():
        tools.append({"toolSpec": {
            "name": t["name"],
            "description": (t.get("description") or "")[:1024],
            "inputSchema": {"json": t.get("inputSchema") or {"type": "object"}},
        }})
    tools.append({"toolSpec": {
        "name": "charger_skill",
        "description": ("Charge la procedure complete (gabarits inclus) d'un skill liste dans "
                        "« Skills disponibles ». A appeler DES qu'un signal de l'Axe 2 designe "
                        "un skill, AVANT de rediger la reponse."),
        "inputSchema": {"json": {
            "type": "object",
            "properties": {"nom": {"type": "string", "enum": bundle.names}},
            "required": ["nom"],
        }},
    }})
    return {"tools": tools, "toolChoice": {"auto": {}}}


def _execute_tool(name: str, arguments: dict, mcp: McpClient, bundle: SkillsBundle) -> tuple[dict | str, bool]:
    """Retourne (resultat, ok). Une erreur devient un texte explicite pour le modele."""
    try:
        if name == "charger_skill":
            return bundle.load(str(arguments.get("nom", ""))), True
        return mcp.call_tool(name, arguments), True
    except McpError as e:
        return f"ECHEC_OUTIL : {e}", False


def _to_tool_result_block(tool_use_id: str, result: dict | str, ok: bool) -> dict:
    if isinstance(result, (dict, list)):
        text = json.dumps(result, ensure_ascii=False)
    else:
        text = str(result)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + "\n[... resultat tronque ...]"
    return {"toolResult": {
        "toolUseId": tool_use_id,
        "content": [{"text": text}],
        "status": "success" if ok else "error",
    }}


def run_agent(
    question: str,
    copro_codes: list[str] | None = None,
    history: list[dict] | None = None,   # [{"role": "user"|"assistant", "text": ...}]
    bedrock=None,
    mcp: McpClient | None = None,
    bundle: SkillsBundle | None = None,
    on_step=None,                        # callback(label_metier) a chaque appel d'outil
    tracer=None,                         # objet trace Langfuse (optionnel, span par outil)
) -> AgentResult:
    """Deroule la boucle agentique complete pour une question et rend la reponse finale."""
    if bedrock is None:
        import boto3
        from botocore.config import Config
        bedrock = boto3.client("bedrock-runtime", region_name="eu-west-1",
                               config=Config(read_timeout=300, connect_timeout=10,
                                             retries={"max_attempts": 3}))
    mcp = mcp or McpClient()
    bundle = bundle or SkillsBundle("ncg")

    system_text = _load_system_prompt() + "\n\n" + bundle.catalog_prompt()
    tool_config = _tool_config(mcp, bundle)

    messages: list[dict] = []
    for h in (history or []):
        messages.append({"role": h["role"], "content": [{"text": h["text"]}]})
    user_text = question
    if copro_codes:
        user_text = f"[Perimetre impose : codes {', '.join(copro_codes)}]\n{question}"
    messages.append({"role": "user", "content": [{"text": user_text}]})

    res = AgentResult(answer="")
    forced_final = False
    use_cache = ENABLE_PROMPT_CACHE
    while True:
        system_blocks = [{"text": system_text}]
        tc = tool_config
        if use_cache:
            system_blocks = system_blocks + [{"cachePoint": {"type": "default"}}]
            tc = {**tool_config,
                  "tools": tool_config["tools"] + [{"cachePoint": {"type": "default"}}]}
        try:
            resp = bedrock.converse(
                modelId=MODEL_ID,
                system=system_blocks,
                messages=messages,
                toolConfig=tc,
                inferenceConfig={"maxTokens": MAX_TOKENS_PER_TURN, "temperature": 0.2},
            )
        except Exception as e:
            if use_cache and "cachePoint" in str(e):
                use_cache = False  # modele/region sans prompt caching : repli silencieux
                continue
            raise
        usage = resp.get("usage", {})
        res.input_tokens += usage.get("inputTokens", 0)
        res.output_tokens += usage.get("outputTokens", 0)
        res.cache_read_tokens += usage.get("cacheReadInputTokens", 0)
        res.cache_write_tokens += usage.get("cacheWriteInputTokens", 0)
        res.stop_reason = resp.get("stopReason", "")
        out_msg = resp["output"]["message"]
        messages.append(out_msg)

        if res.stop_reason != "tool_use":
            res.answer = "".join(c.get("text", "") for c in out_msg["content"]).strip()
            return res

        res.iterations += 1
        if res.iterations > MAX_TOOL_ITERATIONS and not forced_final:
            # Garde anti-boucle : on repond aux tool calls par un refus, puis on
            # somme le modele de conclure avec l'existant.
            blocks = [
                _to_tool_result_block(c["toolUse"]["toolUseId"],
                                      "LIMITE_OUTILS_ATTEINTE : plus aucun appel disponible.", False)
                for c in out_msg["content"] if "toolUse" in c
            ]
            blocks.append({"text": "Limite d'appels d'outils atteinte : reponds maintenant "
                                   "avec les elements deja obtenus, en signalant ce qui manque."})
            messages.append({"role": "user", "content": blocks})
            forced_final = True
            continue

        result_blocks = []
        for c in out_msg["content"]:
            if "toolUse" not in c:
                continue
            tu = c["toolUse"]
            name, arguments = tu["name"], tu.get("input") or {}
            if on_step:
                on_step(STEP_LABELS.get(name, "recherche en cours"))
            span = tracer.span(name=name, input=arguments) if tracer else None
            result, ok = _execute_tool(name, arguments, mcp, bundle)
            if span:
                span.end(output=str(result)[:2000], level="DEFAULT" if ok else "ERROR")
            res.tool_calls.append({"name": name, "arguments": arguments,
                                   "ok": ok, "chars": len(str(result))})
            result_blocks.append(_to_tool_result_block(tu["toolUseId"], result, ok))
        messages.append({"role": "user", "content": result_blocks})


def _cli():
    import argparse
    import time
    ap = argparse.ArgumentParser(description="CLI de test de la boucle agentique PALIM.")
    ap.add_argument("question")
    ap.add_argument("--copros", help="codes copro imposes, separes par des virgules")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    codes = [c.strip() for c in args.copros.split(",")] if args.copros else None
    t0 = time.time()
    steps: list[str] = []
    result = run_agent(args.question, copro_codes=codes,
                       on_step=lambda s: (steps.append(s), print(f"  … {s}"))[1])
    dt = time.time() - t0

    print("\n" + "=" * 70)
    print(result.answer)
    print("=" * 70)
    print(f"{result.iterations} iteration(s), {len(result.tool_calls)} appel(s) d'outil, "
          f"{dt:.1f}s, {result.input_tokens}+{result.output_tokens} tokens "
          f"(cache w{result.cache_write_tokens}/r{result.cache_read_tokens}), "
          f"~${result.cost_usd:.4f}, stop={result.stop_reason}")
    if args.verbose:
        for tc in result.tool_calls:
            print(f"  - {tc['name']}({json.dumps(tc['arguments'], ensure_ascii=False)[:140]}) "
                  f"ok={tc['ok']} {tc['chars']} chars")


if __name__ == "__main__":
    _cli()
