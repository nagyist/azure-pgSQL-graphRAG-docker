# ------------------------------------------------------------
# Stage 1: Build Python venv with dependencies (Python 3.12)
# ------------------------------------------------------------
FROM python:3.12-slim-bookworm AS pydeps

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /build


# System deps to compile common Python wheels (kept minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ \
    git curl ca-certificates pkg-config \
    libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Create venv
RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy and install requirements
COPY graphRAG/requirements.txt /build/requirements.txt
RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r /build/requirements.txt \
    && pip install --no-cache-dir --upgrade openai \
    && pip install --no-cache-dir "graphrag==3.0.5"

# ------------------------------------------------------------
# Stage 2: Final image based on Postgres 16 + AGE + pgvector
# ------------------------------------------------------------
FROM postgres:16-bookworm

ENV DEBIAN_FRONTEND=noninteractive

# --- Install build + runtime deps for AGE/pgvector + pyodbc ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git \
    build-essential make clang \
    postgresql-server-dev-16 \
    libreadline-dev zlib1g-dev flex bison \
    unixodbc unixodbc-dev \
    libssl3 libffi8 \
    && rm -rf /var/lib/apt/lists/*

# --- Optional: Microsoft ODBC Driver 18 for SQL Server (Debian 12 / bookworm) ---
# If you don't need Azure SQL via pyodbc, you can remove this whole block.
RUN curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /usr/share/keyrings/microsoft.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/microsoft-prod.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

# --- Copy Python 3.12 runtime + venv from builder stage ---
COPY --from=pydeps /usr/local /usr/local
COPY --from=pydeps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# --- Build & install pgvector ---
ARG PGVECTOR_VERSION=v0.7.4
RUN git clone --depth 1 --branch ${PGVECTOR_VERSION} https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector \
    && make \
    && make install \
    && rm -rf /tmp/pgvector

# --- Build & install Apache AGE (PG16 compatible) ---
# You requested PG16-compatible branch/tag, keeping it explicit:
ARG AGE_REF=release/PG16/1.5.0
RUN git clone --depth 1 --branch ${AGE_REF} https://github.com/apache/age.git /tmp/age \
    && cd /tmp/age \
    && make install \
    && rm -rf /tmp/age

# Install pyodbc and Jupyter
RUN pip install pyodbc jupyter requests httpx

# --- App workspace folders (shared by your docker-compose volumes) ---
WORKDIR /app
RUN mkdir -p \
    /app/graphrag-folder/input \
    /app/graphrag-folder/output \
    /app/graphrag-folder/cache \
    /app/graphrag-folder/logs \
    /app/graphrag-folder/prompts \
    /app/graphrag-folder/update-output \
    /app/graphrag-folder/restore \
    /app/plugins

# Expose ports used by your services
EXPOSE 5432 8081 8082 8083 8888 8084

# Default command remains postgres; docker-compose can override per service
CMD ["postgres"]
