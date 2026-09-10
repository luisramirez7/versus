FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY versus ./versus
RUN pip install --no-cache-dir -e ".[postgres]"
# sqlite lives on a volume at /data when DATABASE_URL points there
VOLUME ["/data"]
ENV DATABASE_URL=sqlite+aiosqlite:////data/versus.db
CMD ["python", "-m", "versus"]
