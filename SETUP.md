# MedVision AI — Backend Setup

## 1. Install Python dependencies
```bash
pip install flask flask-cors requests pydicom pynetdicom
```

## 2. Install Orthanc (CT Scanner connection)
- Download from: https://www.orthanc-server.com/download.php
- Windows: download the `.exe` installer
- Run it — it starts on `http://localhost:8042`
- Default login: `orthanc` / `orthanc`

## 3. Update index.html
Change these two lines in your `index.html`:

```js
// OLD (Vercel)
const WORKFLOW_URL = '/api/analyze';

// NEW (local backend)
const WORKFLOW_URL = 'http://localhost:5000/api/analyze';
```

Also update the LLM fetch URL:
```js
// OLD
const response = await fetch(MIMO_URL, { ... });

// NEW
const response = await fetch('http://localhost:5000/api/llm', { ... });
```

## 4. Run the server
```bash
python server.py
```

Open: http://localhost:5000

## 5. Connect a real CT scanner
- In Orthanc config (`orthanc.json`), add your scanner as a DICOM node:
```json
"DicomModalities": {
  "myscanner": ["SCANNER_AET", "192.168.1.100", 104]
}
```
- The scanner must be configured to push (C-STORE) to Orthanc's AET on port 4242

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/orthanc/status` | Check if Orthanc is running |
| `GET /api/orthanc/patients` | List all patients |
| `GET /api/orthanc/search?bodyPart=SKULL&modality=CT` | Search by body part |
| `POST /api/orthanc/request-scan` | Send scan instruction |
| `GET /api/orthanc/instances/:id/preview` | Get image as JPEG |
| `POST /api/analyze` | Roboflow detection proxy |
| `POST /api/llm` | Mimo LLM proxy |
