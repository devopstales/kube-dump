FROM python:3.12-alpine

LABEL maintainer="devopstales2@gmail.com"
LABEL description="Backup Kubernetes resources as clean YAML manifests"

# Install git (required for GitPython)
RUN apk add --no-cache git

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the script
COPY kube-dump.py .

# Create data directory
RUN mkdir -p /data

# Set default environment variables
ENV DESTINATION_DIR=/data/kube-dump
ENV PYTHONUNBUFFERED=1

# Configure git for commits
RUN git config --global user.name "kube-dump" && \
    git config --global user.email "kube-dump@example.com" && \
    git config --global --add safe.directory /data/kube-dump

ENTRYPOINT ["python", "/app/kube-dump.py"]
CMD ["--destination-dir", "/data/kube-dump"]

