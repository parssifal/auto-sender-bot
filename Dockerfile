FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app --home /app app

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY main.py /app/main.py
COPY core /app/core
COPY telegram /app/telegram

RUN mkdir -p /app/data && chown -R app:app /app

USER app

CMD ["python", "main.py"]
