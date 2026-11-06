FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY paperless_ai_claude ./paperless_ai_claude
COPY tools ./tools
COPY prompt.md .
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8720
HEALTHCHECK --interval=60s --timeout=10s CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/api/status' % os.environ.get('PORT','8720'))" || exit 1
CMD ["python", "-m", "paperless_ai_claude", "serve"]
