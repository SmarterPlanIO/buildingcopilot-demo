"""
PALIM_tracing.py — Instrumentation Langfuse optionnelle pour le serveur MCP.

Greffe non intrusive : si LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY sont absents,
toutes les fonctions sont des no-op (start_trace retourne None, les helpers
ignorent None). Aucune exception ne remonte, aucun secret n'est loggé.

API v2 (langfuse==2.60.4) : .trace() / .span() / .end() / .update().
La v3 casse cette API (cf. CLAUDE.md), d'où le pin strict.

Le client est un singleton lazy, réutilisé sur les invocations Lambda warm.
flush() est appelé par appel de tool (Lambda peut geler après la réponse).
"""
import PALIM_config as cfg

_client = None
_init_done = False


def _get_client():
    global _client, _init_done
    if _init_done:
        return _client
    _init_done = True
    if not (cfg.LANGFUSE_PUBLIC_KEY and cfg.LANGFUSE_SECRET_KEY):
        _client = None
        return None
    try:
        from langfuse import Langfuse
        _client = Langfuse(
            public_key=cfg.LANGFUSE_PUBLIC_KEY,
            secret_key=cfg.LANGFUSE_SECRET_KEY,
            host=cfg.LANGFUSE_HOST,
            enabled=True,
        )
    except Exception:
        _client = None  # langfuse absent ou init impossible → tracing désactivé
    return _client


def start_trace(name, *, input=None, metadata=None, tags=None):
    """Crée une trace Langfuse. Retourne un handle, ou None si tracing désactivé.
    Ne lève jamais."""
    client = _get_client()
    if client is None:
        return None
    try:
        meta = {"source": "mcp"}
        if metadata:
            meta.update(metadata)
        return client.trace(
            name=name,
            user_id=cfg.LANGFUSE_USER or None,
            input=input,
            metadata=meta,
            tags=tags,
        )
    except Exception:
        return None


def span(trace, name, **input_fields):
    """Ouvre un span sur la trace (ou None). Retourne un handle ou None."""
    if trace is None:
        return None
    try:
        return trace.span(name=name, input=(input_fields or None))
    except Exception:
        return None


def end_span(handle, **output_fields):
    """Termine un span. Ignore None et toute erreur."""
    if handle is None:
        return
    try:
        handle.end(output=(output_fields or None))
    except Exception:
        pass


def update_trace(trace, *, output=None, metadata=None):
    """Finalise une trace (output + metadata). Ignore None et toute erreur."""
    if trace is None:
        return
    try:
        trace.update(output=output, metadata=metadata)
    except Exception:
        pass


def trace_id(handle):
    """Id de la trace (pour rattacher un score de feedback). None si tracing off."""
    return getattr(handle, "id", None) if handle is not None else None


def log_feedback(value, *, comment=None, context=None, user=None, trace_ref=None):
    """Enregistre un feedback utilisateur comme score Langfuse.

    value : 1.0 (utile) / 0.0 (à améliorer). comment : texte libre (peut être None).
    context : dict {question, copro_codes, mode, rating} (trace autonome).
    trace_ref : id de la trace de retrieval d'origine ; si fourni, le score s'y
    rattache. Sinon une trace 'PALIM_feedback' autonome est créée.
    Retourne (ok: bool, linked: bool). Ne lève jamais, ne logge aucun secret.
    """
    client = _get_client()
    if client is None:
        return False, False
    try:
        if trace_ref:
            client.score(trace_id=trace_ref, name="user_feedback", value=value, comment=comment)
            return True, True
        tr = client.trace(name="PALIM_feedback",
                          user_id=(user or cfg.LANGFUSE_USER or None),
                          input=context or None, output={"comment": comment},
                          metadata={"source": "mcp"}, tags=["mcp", "feedback"])
        tr.score(name="user_feedback", value=value, comment=comment)
        return True, False
    except Exception:
        return False, False


def flush():
    """Vide la file Langfuse (bloquant). À appeler en fin d'appel de tool."""
    client = _get_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        pass
