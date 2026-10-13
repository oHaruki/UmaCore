# Runtime image only: compose.yml bind-mounts the checkout at /app, so bot.log,
# backups/ and the tally portrait cache stay in the checkout on the host.
FROM python:3.10-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
# chromium + chromedriver: ChronoGenesis fallback scraper and plotly/kaleido charts.
# postgresql-client-14: pg_dump for the built-in DB backups, same major as the server.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg chromium chromium-driver fonts-liberation \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update && apt-get install -y --no-install-recommends postgresql-client-14 \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt constraints.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt -c /tmp/constraints.txt
WORKDIR /app
CMD ["python", "main.py"]
