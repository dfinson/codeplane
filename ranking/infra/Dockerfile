FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl && \
    curl -sL https://aka.ms/InstallAzureCLIDeb | bash && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY codeplane_wheel/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# Pre-download embedding model so ACI jobs don't hit HuggingFace at runtime
RUN python3 -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

COPY index_job.sh /index_job.sh
RUN chmod +x /index_job.sh

WORKDIR /work
ENTRYPOINT ["/index_job.sh"]
