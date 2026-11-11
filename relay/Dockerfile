FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app
RUN pip install --no-cache-dir requests==2.32.3 \
    && useradd --create-home app \
    && chown -R app:app /app

COPY --chown=app:app server.py .
USER app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/health')"
CMD ["python", "server.py"]
