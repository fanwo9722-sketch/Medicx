# MedVision AI — Medical Imaging Backend with DICOM MWL Support

## Overview

MedVision AI is a Python/Flask backend that connects your web app to Orthanc PACS for real DICOM/CT scanner integration. It now includes a **real DICOM Modality Worklist (MWL) server** that allows you to schedule scans from the web UI and have real CT scanners pick them up automatically.

---

## How It Works

### Architecture

```
┌─────────────────┐     HTTP (port 5000)     ┌──────────────────┐
│   Web Browser   │ ◄──────────────────────► │  Flask Server    │
│   (index.html)  │                          │  (server.py)     │
└─────────────────┘                          └────────┬─────────┘
                                                      │
                           ┌──────────────────────────┼──────────────────┐
                           │                          │                  │
                           ▼                          ▼                  ▼
                   ┌───────────────┐         ┌──────────────┐    ┌────────────┐
                   │  MWL SCP      │         │  Orthanc     │    │  Roboflow  │
                   │  DICOM Server │         │  PACS        │    │  AI + LLM  │
                   │  (port 11113) │         │  (port 8042) │    │  Proxies   │
                   └───────┬───────┘         └──────────────┘    └────────────┘
                           │
                           │ DICOM C-FIND (MWL Query)
                           │
                           ▼
                   ┌───────────────┐
                   │  CT Scanner   │
                   │  (real HW)    │
                   └───────────────┘
```

### The Scan Workflow

1. **You schedule a scan** from the web UI (e.g., select "NECK scan" for a patient)
2. **The server creates an MWL entry** in a SQLite database (`worklist.db`)
3. **The MWL DICOM server** (running in background on port 11113) serves this entry to any scanner that queries it
4. **The CT scanner** automatically picks up the scheduled scan when it queries the MWL server
5. **The technologist** at the scanner sees: Patient Name, Body Part (NECK), Modality (CT), Protocol
6. **The technologist** selects the entry, positions the patient, and starts the scan manually
7. **Scan images** are automatically sent back to Orthanc via DICOM C-STORE
8. **You view the images** in the web app using the built-in DICOM viewer

---

## Setup & Installation

### Prerequisites

```bash
pip install flask flask-cors requests pydicom pynetdicom
```

### Optional: Orthanc PACS Server

Install Orthanc from https://www.orthanc-server.com for full PACS functionality.
Without Orthanc, MWL scheduling still works — you just can't browse/view stored images.

### Configuration (Environment Variables)

| Variable         | Default              | Description                              |
|------------------|----------------------|------------------------------------------|
| `ORTHANC_URL`    | `http://localhost:8042` | Orthanc PACS server URL                 |
| `ORTHANC_USER`   | `orthanc`            | Orthanc username                         |
| `ORTHANC_PASS`   | `orthanc`            | Orthanc password                         |
| `MWL_AE_TITLE`   | `MEDVISION_MWL`     | DICOM AE Title for the MWL server        |
| `MWL_PORT`       | `11113`              | Port for the DICOM MWL listener          |
| `ROBOFLOW_KEY`   | (hardcoded)          | Roboflow API key for AI analysis         |
| `MIMO_KEY`       | (hardcoded)          | Mimo LLM API key for AI diagnosis        |

### Start the Server

```bash
python server.py
```

You should see:

```
============================================================
  MedVision AI — Backend Server
============================================================
  Web app  →  http://localhost:5000
  Orthanc  →  http://localhost:8042
  MWL      →  AE: MEDVISION_MWL | Port: 11113

  Initializing MWL database...
  ✅ MWL database initialized: worklist.db
  Checking Orthanc connection...
  ✅ Orthanc connected — v1.12.1

  Starting DICOM Modality Worklist (MWL) server...
  Starting MWL SCP listener...
  AE Title:  MEDVISION_MWL
  Port:      11113
  ✅ MWL SCP listening on port 11113
  ✅ MWL SCP thread started

============================================================
  Server ready!
============================================================
```

---

## API Endpoints

### MWL (Modality Worklist) Endpoints

#### `POST /api/orthanc/request-scan` — Schedule a Scan

Creates a real MWL entry that scanners can pick up.

```bash
curl -X POST http://localhost:5000/api/orthanc/request-scan \
  -H "Content-Type: application/json" \
  -d '{
    "bodyPart": "NECK",
    "modality": "CT",
    "protocol": "ROUTINE",
    "patient": {
      "name": "John Doe",
      "id": "PAT001",
      "birthDate": "1990-01-15",
      "sex": "M"
    }
  }'
```

Response:
```json
{
  "success": true,
  "accessionNumber": "ACC1712345678",
  "message": "CT scan of NECK scheduled successfully",
  "details": {
    "patient": "John Doe",
    "bodyPart": "NECK",
    "modality": "CT",
    "protocol": "ROUTINE",
    "aeTitle": "MEDVISION_MWL",
    "mwlPort": 11113
  },
  "instructions": "The scanner will automatically pick up this worklist entry..."
}
```

#### `GET /api/mwl/status` — MWL Server Status

```bash
curl http://localhost:5000/api/mwl/status
```

Response:
```json
{
  "available": true,
  "aeTitle": "MEDVISION_MWL",
  "port": 11113,
  "pendingEntries": 2,
  "database": "worklist.db"
}
```

#### `GET /api/mwl/pending` — List Pending Scans

```bash
curl http://localhost:5000/api/mwl/pending
```

Response:
```json
{
  "count": 2,
  "entries": [
    {
      "id": 1,
      "patient_name": "John Doe",
      "patient_id": "PAT001",
      "modality": "CT",
      "body_part": "NECK",
      "status": "PENDING",
      "accession_number": "ACC1712345678",
      ...
    }
  ]
}
```

#### `POST /api/mwl/complete` — Mark Scan as Done

```bash
curl -X POST http://localhost:5000/api/mwl/complete \
  -H "Content-Type: application/json" \
  -d '{"accessionNumber": "ACC1712345678"}'
```

#### `POST /api/mwl/delete` — Delete Worklist Entry

```bash
curl -X POST http://localhost:5000/api/mwl/delete \
  -H "Content-Type: application/json" \
  -d '{"id": 1}'
```

### Orthanc PACS Endpoints

| Method | Endpoint                                          | Description                        |
|--------|---------------------------------------------------|------------------------------------|
| GET    | `/api/orthanc/status`                             | Check if Orthanc is running        |
| GET    | `/api/orthanc/patients`                           | List all patients                  |
| GET    | `/api/orthanc/patients/{id}/studies`              | List studies for a patient         |
| GET    | `/api/orthanc/studies/{id}/series`                | List series for a study            |
| GET    | `/api/orthanc/instances/{id}/preview`             | Get DICOM instance as JPEG base64  |
| GET    | `/api/orthanc/instances/{id}/dicom`               | Get raw DICOM file as base64       |
| GET    | `/api/orthanc/search?bodyPart=NECK&modality=CT`  | Search by body part/modality       |
| GET    | `/api/orthanc/poll`                               | Poll for new studies               |

### AI Proxy Endpoints

| Method | Endpoint      | Description                    |
|--------|---------------|--------------------------------|
| POST   | `/api/analyze` | Proxy to Roboflow for AI analysis |
| POST   | `/api/llm`     | Proxy to Mimo LLM for AI diagnosis |

---

## Connecting a Real CT Scanner

### Scanner Configuration

To connect your CT scanner to this MWL server, configure the scanner with:

| Setting          | Value             |
|------------------|-------------------|
| MWL AE Title     | `MEDVISION_MWL`   |
| MWL Host/IP      | Your server's IP  |
| MWL Port         | `11113`            |

### What Happens When the Scanner Queries

1. The scanner sends a DICOM C-FIND request to your server
2. The MWL server responds with all pending worklist entries
3. The entries appear on the scanner's worklist screen
4. The technologist selects an entry and starts the scan

### After the Scan

1. The scanner sends images to Orthanc via DICOM C-STORE (port 4242)
2. Images appear in the web app automatically
3. You can view them using the built-in DICOM viewer
4. Run AI analysis on the images via Roboflow

---

## Supported Body Parts

When scheduling scans, use these body part codes:

| UI Label  | Body Part Code |
|-----------|----------------|
| Head      | `HEAD`         |
| Neck      | `NECK`         |
| Chest     | `CHEST`        |
| Abdomen   | `ABDOMEN`      |
| Pelvis    | `PELVIS`       |
| Spine     | `SPINE`        |
| Upper Ext | `UPPER_EXTREMITY` |
| Lower Ext | `LOWER_EXTREMITY` |

---

## Files

| File              | Description                              |
|-------------------|------------------------------------------|
| `server.py`       | Main Flask backend + MWL DICOM server    |
| `index.html`      | Web frontend                             |
| `worklist.db`     | SQLite database for MWL entries (auto-created) |
| `uploads/`        | Directory for uploaded DICOM files       |

---

## Troubleshooting

### MWL Server Not Starting

```
⚠️  pynetdicom/pydicom not installed — MWL server disabled
```

Install the required packages:
```bash
pip install pydicom pynetdicom
```

### Orthanc Not Connected

```
⚠️  Orthanc not running — install from https://www.orthanc-server.com
```

MWL scheduling still works without Orthanc. You just can't browse/view stored images.

### Scanner Can't See MWL Entries

1. Check that the scanner is configured with the correct AE Title (`MEDVISION_MWL`)
2. Check that the scanner can reach your server's IP on port `11113`
3. Check firewall settings — port 11113 must be open
4. Verify pending entries exist: `curl http://localhost:5000/api/mwl/pending`

### Port Already in Use

If port 11113 is taken, set a different port:
```bash
MWL_PORT=11114 python server.py
```

Remember to update the scanner configuration with the new port.