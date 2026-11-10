# The credential console: the only process that may write the shared store.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

# ccxt verifies CEX keys and reads their fee tier; eth-keys proves an EVM key
# derives the address stored beside it. Grid carries only the second and arb
# only the first, which is why neither could validate the other's accounts.
# eth-keys resolves its keccak backend lazily, so the backend must be
# installed explicitly or address derivation fails at call time.
RUN pip install --no-cache-dir "ccxt>=4.4" "eth-keys>=0.5" "eth-utils>=4" \
    "eth-hash[pycryptodome]>=0.7"

WORKDIR /app
COPY src/ /app/src/
ENV PYTHONPATH=/app/src

EXPOSE 8772
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('http://127.0.0.1:8772/healthz', timeout=3)); assert d['status']=='ok'"

CMD ["python", "-m", "trading_infra.console.server"]
