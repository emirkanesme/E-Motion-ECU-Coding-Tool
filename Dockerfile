FROM nvcr.io/nvidia/pytorch:24.04-py3

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CUDA_DEVICE_ORDER=PCI_BUS_ID

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/ecu_tuning

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY src/ /workspace/ecu_tuning/src/
COPY tests/ /workspace/ecu_tuning/tests/

EXPOSE 8888

CMD ["jupyter-lab", "--allow-root", "--ip=0.0.0.0", "--port=8888", "--no-browser"]
