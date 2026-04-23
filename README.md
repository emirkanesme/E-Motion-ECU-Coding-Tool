# E-Motion ECU Coding Tool
# PLEASE ENSURE THAT YOUR SYSTEM REQUIREMENTS SPECIFY
This repository now includes a production-oriented baseline for:

- WinOLS-like map parsing to tensor format (`src/data/parser.py`)
- Physics-informed ECU loss function (`src/losses/pinn_loss.py`)
- Unit tests for parser and loss behavior (`tests/`)
- GPU-ready container setup (`Dockerfile`, `docker-compose.yml`)

## Quick Start

Install dependencies:

```bash
pip install -r requirements.txt
```

Run tests:

```bash
pytest -q
```

Run Jupyter with NVIDIA PyTorch container:

```bash
docker compose up --build
```

