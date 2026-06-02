# Image container Lambda + Lambda Web Adapter (streaming) pour le serveur MCP PALIM.
# Build depuis Scripts/mcp_server/. Pré-requis : vendoriser dossiers_api.py
#   cp "../Streamlit Cloud/dossiers_api.py" ./dossiers_api_vendored.py
FROM public.ecr.aws/lambda/python:3.12

# Lambda Web Adapter (extension) — permet à un serveur HTTP/ASGI de tourner en Lambda
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
ENV AWS_LWA_INVOKE_MODE=response_stream
ENV PORT=8000

WORKDIR /var/task
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Modules PALIM + dossiers_api_vendored.py (top-level *.py uniquement)
COPY *.py ./

# Démarre l'app ASGI FastMCP (streamable HTTP) ; LWA proxifie la Function URL
CMD ["uvicorn", "PALIM_server:app", "--host", "0.0.0.0", "--port", "8000"]
