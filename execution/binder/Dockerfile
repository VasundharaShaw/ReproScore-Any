FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV NB_USER=jovyan
ENV NB_UID=1000
ENV HOME=/home/${NB_USER}

# ---------------------------------------------------------------
# System packages — includes pyenv build dependencies
# ---------------------------------------------------------------
RUN apt-get update && \
    apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        wget \
        git \
        sqlite3 \
        python3.10 \
        python3.10-venv \
        python3-pip \
        make \
        build-essential \
        libssl-dev \
        zlib1g-dev \
        libbz2-dev \
        libreadline-dev \
        libsqlite3-dev \
        libffi-dev \
        liblzma-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------
# Install Jupyter via pip (no conda at all)
# ---------------------------------------------------------------
RUN python3.10 -m pip install --no-cache-dir --upgrade pip && \
    python3.10 -m pip install --no-cache-dir \
        notebook \
        jupyterlab \
        nbconvert

# ---------------------------------------------------------------
# Create jovyan user (matches NFDI expectation)
# ---------------------------------------------------------------
RUN groupadd --gid ${NB_UID} ${NB_USER} && \
    useradd \
        --comment "Default user" \
        --create-home \
        --gid ${NB_UID} \
        --no-log-init \
        --shell /bin/bash \
        --uid ${NB_UID} \
        ${NB_USER}

# ---------------------------------------------------------------
# Install pyenv as jovyan user
# ---------------------------------------------------------------
USER ${NB_USER}

ENV PYENV_ROOT=${HOME}/.pyenv
ENV PATH=${PYENV_ROOT}/bin:${PYENV_ROOT}/shims:${PATH}

RUN curl https://pyenv.run | bash && \
    pyenv --version

# ---------------------------------------------------------------
# Copy repo files in and set working directory
# ---------------------------------------------------------------
COPY --chown=${NB_USER}:${NB_USER} . ${HOME}

WORKDIR ${HOME}
