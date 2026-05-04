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

BMW OBD live dashboard app built with PyQt6 + python-can.

## Features

- Left panel controls:
  - COM port, CAN bus type, bitrate
  - request/response IDs, OBD mode
  - timeout and polling interval
  - PID presets, custom PID list, quick-add PID
- Advanced runtime options:
  - simulated mode (test UI without hardware)
  - auto reconnect with configurable retry delay
  - threshold alarms (RPM and coolant)
  - dark/light theme switch
- Right panel telemetry:
  - metric cards (RPM, speed, coolant, throttle, fuel, frame counter)
  - live gauges for key metrics
  - signal table with update timestamps
  - raw CAN frame stream and event/alert log
- Diagnostics and utility:
  - Read DTC (`mode 03`)
  - Clear DTC (`mode 04`)
  - JSON snapshots of current live signals
- Profiles:
  - save/load/delete local profiles
  - import/export profiles as JSON
- Logging:
  - per-session CSV logs in `logs/` with timestamped filenames
- Polling control:
  - Start / Pause / Resume / Stop (threaded, responsive UI)

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python main.py
```

## Output files

- `profiles.json` - saved profiles
- `logs/telemetry_YYYYMMDD_HHMMSS.csv` - session telemetry logs
- `logs/snapshot_YYYYMMDD_HHMMSS.json` - signal snapshots

## Notes

- Typical BMW D-CAN values:
  - Request ID: `0x7DF`
  - Response IDs: `0x7E8` to `0x7EF`
  - Bitrate: `500000`
- Make sure ignition is ON.
- Some K+DCAN USB cables do not expose a generic SLCAN CAN interface and may not work with python-can directly.

