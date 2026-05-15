FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    git \
    curl \
    ca-certificates \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/comfyui
COPY comfyui/ /opt/comfyui/

RUN python3 -m venv /opt/comfyui/venv \
    && /opt/comfyui/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/comfyui/venv/bin/pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision torchaudio \
    && /opt/comfyui/venv/bin/pip install -r requirements.txt

RUN chmod +x /opt/comfyui/entrypoint-comfyui.sh

EXPOSE 8187 8188
ENTRYPOINT ["/opt/comfyui/entrypoint-comfyui.sh"]
