FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    gcc \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install pyenv dependencies
RUN apt-get update && apt-get install -y \
    curl \
    libssl-dev \
    zlib1g-dev \
    libbz2-dev \
    libreadline-dev \
    libsqlite3-dev \
    libncursesw5-dev \
    xz-utils \
    tk-dev \
    libxml2-dev \
    libxmlsec1-dev \
    libffi-dev \
    liblzma-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency manifests
COPY requirements.txt .
COPY requirements-service.txt .

# Copy application entrypoints
COPY app.py .
COPY service.py .

# Copy pipeline code
COPY pipeline/ ./pipeline/
COPY config/ ./config/

# Install Python dependencies (service pins on top of base requirements)
RUN pip install --no-cache-dir -r requirements.txt -r requirements-service.txt

# Install pyenv for per-repo Python version management
RUN curl https://pyenv.run | bash
ENV PYENV_ROOT="/root/.pyenv"
ENV PATH="$PYENV_ROOT/bin:$PYENV_ROOT/shims:$PATH"

# Create output directories
RUN mkdir -p /app/output/db /app/output/logs /app/output/csv /app/output/cloned_repos

EXPOSE 7860

CMD ["python", "service.py"]
