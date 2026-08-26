# Image container Lambda + Lambda Web Adapter (streaming) pour le serveur MCP PALIM.
# Build depuis Scripts/mcp_server/. Pré-requis : vendoriser les modules de "Streamlit Cloud"
#   cp "../Streamlit Cloud/dossiers_api.py" ./dossiers_api_vendored.py
#   cp "../Streamlit Cloud/rerank.py"       ./rerank_vendored.py
#
# NE PAS utiliser la base AWS Lambda (public.ecr.aws/lambda/python) : son ENTRYPOINT
# est le Runtime Interface Client qui attend un nom de handler et casse uvicorn
# (exit status 142, "entrypoint requires the handler name to be the first argument").
# Pattern LWA canonique = base Python standard ; l'adapter (extension) implémente la
# Runtime API et proxifie les requêtes vers uvicorn sur $PORT.
FROM public.ecr.aws/docker/library/python:3.12-slim

# Lambda Web Adapter (extension) — fait tourner un serveur HTTP/ASGI en Lambda
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
ENV AWS_LWA_INVOKE_MODE=response_stream
ENV PORT=8000

WORKDIR /var/task
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Modules PALIM + dossiers_api_vendored.py + rerank_vendored.py (top-level *.py)
COPY *.py ./
# Données statiques embarquées (registre liens visite 3D, lu par PALIM_visites.py)
COPY visites_3d.txt ./

# Démarre l'app ASGI FastMCP (streamable HTTP) ; LWA proxifie la Function URL
CMD ["uvicorn", "PALIM_server:app", "--host", "0.0.0.0", "--port", "8000"]
