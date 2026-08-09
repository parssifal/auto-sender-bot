# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: run the test suite. The build FAILS here if any test fails, so an
# image can never be produced (and therefore never started on the server)
# unless the whole suite passes.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS test

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
COPY requirements-dev.txt /app/requirements-dev.txt

RUN pip install --no-cache-dir -r /app/requirements.txt -r /app/requirements-dev.txt

COPY main.py /app/main.py
COPY core /app/core
COPY telegram /app/telegram
COPY tests /app/tests
COPY deploy /app/deploy

# Run the suite; only touch the success marker if pytest exits cleanly.
RUN python -m pytest -q && touch /app/tests-passed

# ---------------------------------------------------------------------------
# Stage 2: runtime image (production). It COPYs the marker from the test
# stage, which forces that stage to build (and thus the tests to pass) before
# this image can be produced.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app --home /app app

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

# Gate: fails the build unless stage "test" produced the marker (i.e. tests passed).
COPY --from=test /app/tests-passed /app/tests-passed

COPY main.py /app/main.py
COPY core /app/core
COPY telegram /app/telegram

RUN mkdir -p /app/data && chown -R app:app /app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import os, sys, urllib.request; port=os.getenv('HEALTHCHECK_PORT', '8080'); urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=2).read(); sys.exit(0)" || exit 1

USER app

CMD ["python", "main.py"]
