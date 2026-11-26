# PPE Guard dashboard.
#   CPU (default):  docker build -t ppe-guard .
#   NVIDIA GPU:     docker build -t ppe-guard --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128 .
FROM python:3.12-slim

ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PPE_GUARD_DATA_DIR=/data \
    PPE_GUARD_MODELS_DIR=/models \
    YOLO_CONFIG_DIR=/tmp/ultralytics

# OpenCV runtime libraries
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install torch torchvision --index-url "${TORCH_INDEX}"

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

RUN useradd --create-home --uid 1000 ppe \
 && mkdir -p /data /models \
 && chown ppe:ppe /data /models
USER ppe

VOLUME ["/data", "/models"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["ppe-guard", "serve", "--host", "0.0.0.0", "--port", "8000"]
