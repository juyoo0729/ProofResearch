FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
COPY --from=ghcr.io/astral-sh/uv:0.12.4@sha256:d0a6eca6c669dc7e9c51218707b8438a3d30402733d739dcc00adb3e213e8f5c /uv /uvx /bin/
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY app.py ./
RUN uv sync --frozen --no-dev --no-editable \
    && useradd --create-home --uid 10001 researcher \
    && mkdir -p /app/.cache && chown -R researcher:researcher /app
USER researcher
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD /app/.venv/bin/python -c "from urllib.request import urlopen; assert urlopen('http://127.0.0.1:8501/_stcore/health',timeout=3).status == 200"
CMD ["/app/.venv/bin/streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.headless", "true"]
