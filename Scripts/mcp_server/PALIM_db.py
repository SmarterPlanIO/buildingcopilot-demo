"""
PALIM_db.py — Connexion PostgreSQL (RDS pgvector) pour le serveur MCP.

Singleton module-level réutilisé entre invocations Lambda warm, avec
reconnexion sur échec (repris de get_db_connection streamlit_app.py:316-339,
sans la dépendance st.session_state).

Connexion read-only via mcp_ncg_reader, SSL strict.
"""
import json

import psycopg2

import PALIM_config as cfg

_conn = None
_secret_cache = None


def _get_secret_dict():
    """Récupère le secret DB depuis AWS Secrets Manager (mis en cache).

    Accepte un secret JSON (clé 'password', éventuellement 'host'/'username'/
    'dbname') ou une chaîne brute (= le mot de passe). Le mot de passe n'est
    jamais lu/loggé ailleurs.
    """
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache
    import boto3  # local : évite l'import si DB_SECRET_ARN absent
    sm = boto3.client("secretsmanager", region_name=cfg.AWS_REGION_SECRETS)
    raw = sm.get_secret_value(SecretId=cfg.DB_SECRET_ARN)["SecretString"]
    try:
        data = json.loads(raw)
        _secret_cache = data if isinstance(data, dict) else {"password": str(data)}
    except (json.JSONDecodeError, TypeError):
        _secret_cache = {"password": raw}
    return _secret_cache


def _resolve_credentials():
    """(host, port, dbname, user, password) — password depuis Secrets Manager si DB_SECRET_ARN."""
    host, port, name, user, pwd = cfg.DB_HOST, cfg.DB_PORT, cfg.DB_NAME, cfg.DB_USER, cfg.DB_PASSWORD
    if cfg.DB_SECRET_ARN:
        s = _get_secret_dict()
        pwd = s.get("password", pwd)
        host = host or s.get("host", "")          # env prioritaire, secret en repli
        user = user or s.get("username", "")
        name = name or s.get("dbname", "")
    return host, port, name, user, pwd


def _new_conn():
    host, port, name, user, pwd = _resolve_credentials()
    conn = psycopg2.connect(
        host=host, port=port, dbname=name,
        user=user, password=pwd,
        sslmode="require",
        connect_timeout=10,
        keepalives=1, keepalives_idle=300, keepalives_interval=30, keepalives_count=3,
    )
    conn.autocommit = True
    # Préserve le rappel ANN quand on filtre par code_ncg (cf. PLAN_ACTION §4).
    # Session-level (pas LOCAL) car autocommit : persiste sur la connexion réutilisée.
    with conn.cursor() as cur:
        cur.execute("SET ivfflat.probes = %s", (cfg.IVFFLAT_PROBES,))
    return conn


def get_conn():
    """Retourne une connexion vivante (reconnecte si la TCP a expiré)."""
    global _conn
    if _conn is not None:
        try:
            with _conn.cursor() as cur:
                cur.execute("SELECT 1")
            return _conn
        except Exception:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
    _conn = _new_conn()
    return _conn
