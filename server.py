"""
MedVision AI — Python Backend
Connects your web app to Orthanc PACS for real DICOM/CT scanner integration.

Features:
    - Orthanc PACS integration (browse, search, render DICOM images)
    - DICOM Modality Worklist (MWL) server for real scanner integration
    - Roboflow AI image analysis proxy
    - Mimo LLM proxy for AI-assisted diagnosis

Requirements:
    pip install flask flask-cors requests pydicom pynetdicom

Usage:
    python server.py

Then open: http://localhost:5000
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import requests
import base64
import os
import json
import tempfile
import threading
import time
import sqlite3
from datetime import datetime

# ── DICOM MWL IMPORTS ────────────────────────────────────────────────────────
# pynetdicom provides the DICOM network services (MWL SCP)
# pydicom provides DICOM dataset manipulation
try:
    from pynetdicom import AE, evt, AllStoragePresentationContexts
    from pynetdicom.sop_class import ModalityWorklistInformationFind
    from pydicom import Dataset
    from pydicom.uid import ExplicitVRLittleEndian
    from pydicom.valuerep import DA, TM
    PNETDICOM_AVAILABLE = True
except ImportError:
    PNETDICOM_AVAILABLE = False
    print("⚠️  pynetdicom/pydicom not installed — MWL server disabled")
    print("   Install with: pip install pydicom pynetdicom")

# ── FLASK APP ─────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=".")
CORS(app)

# ── CONFIG ────────────────────────────────────────────────────────────────────
ORTHANC_URL      = os.getenv("ORTHANC_URL",   "http://localhost:8042")
ORTHANC_USER     = os.getenv("ORTHANC_USER",  "orthanc")
ORTHANC_PASS     = os.getenv("ORTHANC_PASS",  "orthanc")
ROBOFLOW_API_KEY = os.getenv("ROBOFLOW_KEY",  "op6ghK2hTwUAhTN83Ok0")
MIMO_KEY         = os.getenv("MIMO_KEY",       "sk-s4mwydcwt4cd13o52nhj0lmy7xqx7gnzcrklq07nmku7nuim")
MIMO_URL         = "https://api.xiaomimimo.com/v1/chat/completions"
MIMO_MODEL       = "mimo-v2.5"
ROBOFLOW_URL     = "https://serverless.roboflow.com/iiiii-srapi/workflows/detect-count-and-visualize-2"

# ── MWL CONFIG ────────────────────────────────────────────────────────────────
# DICOM AE Title this MWL server presents to scanners
MWL_AE_TITLE = os.getenv("MWL_AE_TITLE", "MEDVISION_MWL")
# Port for DICOM MWL listener (default 11113 to avoid conflict with Orthanc)
MWL_PORT = int(os.getenv("MWL_PORT", "11113"))
# Database for MWL entries
MWL_DB_PATH = os.path.join(os.path.dirname(__file__), "worklist.db")

orthanc_auth = (ORTHANC_USER, ORTHANC_PASS)

# ── MWL DATABASE ──────────────────────────────────────────────────────────────
# SQLite database stores scheduled worklist entries.
# The MWL SCP reads from this database when a scanner queries for work.

def init_mwl_db():
    """Initialize the MWL SQLite database with the worklist and patients tables."""
    conn = sqlite3.connect(MWL_DB_PATH)

    # Worklist table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS worklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            -- Patient info
            patient_name TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            patient_birth_date TEXT DEFAULT '',
            patient_sex TEXT DEFAULT '',
            -- Study info
            accession_number TEXT NOT NULL UNIQUE,
            referring_physician TEXT DEFAULT '',
            study_instance_uid TEXT DEFAULT '',
            -- Scheduled procedure
            modality TEXT DEFAULT 'CT',
            body_part TEXT DEFAULT '',
            scheduled_station_name TEXT DEFAULT '',
            scheduled_station_ae_title TEXT DEFAULT '',
            procedure_step_id TEXT DEFAULT '',
            requested_procedure_description TEXT DEFAULT '',
            scheduled_procedure_step_description TEXT DEFAULT '',
            -- Status
            status TEXT DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # Patients table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            date_of_birth TEXT DEFAULT '',
            sex TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            address TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()
    print(f"  ✅ MWL database initialized: {MWL_DB_PATH}")

def insert_worklist_entry(patient_name, patient_id, patient_birth_date="", patient_sex="",
                          body_part="", modality="CT", protocol="ROUTINE",
                          referring_physician="", station_name="MEDVISION_CT",
                          station_ae_title="MEDVISION_SCANNER"):
    """
    Insert a new MWL entry into the database.
    Returns the generated accession number.
    """
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    accession = f"ACC{int(time.time())}{os.getpid()}"
    uid = f"1.2.826.0.1.3680043.8.1.{int(time.time())}.{os.getpid()}"

    conn = sqlite3.connect(MWL_DB_PATH)
    conn.execute("""
        INSERT INTO worklist (
            patient_name, patient_id, patient_birth_date, patient_sex,
            accession_number, referring_physician, study_instance_uid,
            modality, body_part, scheduled_station_name, scheduled_station_ae_title,
            procedure_step_id, requested_procedure_description,
            scheduled_procedure_step_description,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
    """, (
        patient_name, patient_id, patient_birth_date, patient_sex,
        accession, referring_physician, uid,
        modality, body_part, station_name, station_ae_title,
        f"STEP{int(time.time())}",
        f"{modality} {body_part}".strip(),
        f"{modality} {body_part} - {protocol}".strip(),
        now, now
    ))
    conn.commit()
    conn.close()

    print(f"[MWL] New entry: {patient_name} | {modality} {body_part} | ACC: {accession}")
    return accession

def get_pending_worklist():
    """Retrieve all pending MWL entries from the database."""
    conn = sqlite3.connect(MWL_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM worklist WHERE status = 'PENDING' ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def complete_worklist_entry(accession_number):
    """Mark a worklist entry as completed (scan done)."""
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite3.connect(MWL_DB_PATH)
    conn.execute("""
        UPDATE worklist SET status = 'COMPLETED', updated_at = ? WHERE accession_number = ?
    """, (now, accession_number))
    conn.commit()
    conn.close()

def delete_worklist_entry(entry_id):
    """Delete a worklist entry by ID."""
    conn = sqlite3.connect(MWL_DB_PATH)
    conn.execute("DELETE FROM worklist WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()

# ── MWL DICOM SCP (Service Class Provider) ───────────────────────────────────
# This is the DICOM server that scanners query for scheduled procedures.
# It runs in a background thread alongside the Flask HTTP server.

def mwl_find_handler(event):
    """
    Handle C-FIND requests from scanners querying the Modality Worklist.

    When a scanner (e.g., CT machine) queries for scheduled scans,
    this handler returns matching worklist entries from the database.

    Args:
        event: pynetdicom event containing the C-FIND request

    Yields:
        pydicom.Dataset objects representing matching worklist entries
    """
    # Get the query dataset from the scanner
    req = event.request

    # Read pending entries from database
    entries = get_pending_worklist()

    # Extract query filters (scanner may filter by patient name, modality, etc.)
    query_patient_name = str(req.PatientName) if 'PatientName' in req else None
    query_modality = None
    if hasattr(req, 'ScheduledProcedureStepSequence'):
        if len(req.ScheduledProcedureStepSequence) > 0:
            query_modality = req.ScheduledProcedureStepSequence[0].Modality

    print(f"[MWL SCP] C-FIND query received:")
    print(f"  Patient Name filter: {query_patient_name or 'any'}")
    print(f"  Modality filter: {query_modality or 'any'}")
    print(f"  Available entries: {len(entries)}")

    matched = 0
    for entry in entries:
        # Apply filters if provided by the scanner
        if query_patient_name and query_patient_name != '*':
            if query_patient_name.lower() not in entry['patient_name'].lower():
                continue
        if query_modality and query_modality != '*':
            if entry['modality'].upper() != query_modality.upper():
                continue

        # Build DICOM dataset for this worklist entry
        ds = Dataset()

        # Patient Module (required for MWL)
        ds.PatientName = entry['patient_name']
        ds.PatientID = entry['patient_id']
        ds.PatientBirthDate = entry['patient_birth_date'].replace('-', '')  # DICOM format: YYYYMMDD
        ds.PatientSex = entry['patient_sex']

        # General Study Module
        ds.AccessionNumber = entry['accession_number']
        ds.ReferringPhysicianName = entry['referring_physician']
        ds.StudyInstanceUID = entry['study_instance_uid']

        # Scheduled Procedure Step Sequence (required for MWL)
        ds.ScheduledProcedureStepSequence = []
        step = Dataset()
        step.ScheduledStationName = entry['scheduled_station_name']
        step.ScheduledStationAETitle = entry['scheduled_station_ae_title']
        step.ScheduledProcedureStepStartDate = datetime.utcnow().strftime('%Y%m%d')
        step.ScheduledProcedureStepStartTime = datetime.utcnow().strftime('%H%M%S')
        step.Modality = entry['modality']
        step.ScheduledProcedureStepDescription = entry['scheduled_procedure_step_description']
        step.ScheduledProcedureStepID = entry['procedure_step_id']
        ds.ScheduledProcedureStepSequence.append(step)

        # Requested Procedure Module
        ds.RequestedProcedureDescription = entry['requested_procedure_description']

        # Set transfer syntax
        ds.is_little_endian = True
        ds.is_implicit_VR = False

        matched += 1
        print(f"  → Yielding: {entry['patient_name']} | {entry['modality']} {entry['body_part']}")
        yield ds

    print(f"[MWL SCP] Returned {matched} entries")

def start_mwl_scp():
    """
    Start the DICOM Modality Worklist SCP (Service Class Provider) in a background thread.

    This creates a DICOM listener that scanners can query for scheduled procedures.
    Scanners connect to this server using the configured AE title and port.

    The listener runs indefinitely until the process exits.
    """
    if not PNETDICOM_AVAILABLE:
        print("  ⚠️  MWL SCP not started — pynetdicom not installed")
        return

    try:
        # Create Application Entity with our AE title
        ae = AE(ae_title=MWL_AE_TITLE)

        # Add presentation context for Modality Worklist Find
        # This tells scanners "I can respond to MWL queries"
        ae.add_supported_context(ModalityWorklistInformationFind)

        # Bind the C-FIND handler to the event
        handlers = [(evt.EVT_C_FIND, mwl_find_handler)]

        print(f"  Starting MWL SCP listener...")
        print(f"  AE Title:  {MWL_AE_TITLE}")
        print(f"  Port:      {MWL_PORT}")
        print(f"  Scanners can query this server for scheduled procedures")

        # Start listening (non-blocking runs in this thread)
        ae.start_server(
            ('0.0.0.0', MWL_PORT),
            evt_handlers=handlers,
            block=False
        )

        print(f"  ✅ MWL SCP listening on port {MWL_PORT}")

        # Keep the thread alive
        while True:
            time.sleep(1)

    except Exception as e:
        print(f"  ❌ MWL SCP error: {e}")

def start_mwl_thread():
    """Start the MWL SCP in a daemon thread (auto-stops when main process exits)."""
    if not PNETDICOM_AVAILABLE:
        return

    thread = threading.Thread(target=start_mwl_scp, daemon=True, name="MWL-SCP")
    thread.start()
    print(f"  ✅ MWL SCP thread started")

# ── SERVE FRONTEND ────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

# ── ORTHANC STATUS ────────────────────────────────────────────────────────────
@app.route("/api/orthanc/status")
def orthanc_status():
    """Check if Orthanc is running and reachable."""
    try:
        r = requests.get(f"{ORTHANC_URL}/system", auth=orthanc_auth, timeout=3)
        data = r.json()
        return jsonify({
            "connected": True,
            "version":   data.get("Version", "unknown"),
            "name":      data.get("Name", "Orthanc"),
            "dicomPort": data.get("DicomPort", 4242),
        })
    except Exception as e:
        return jsonify({"connected": False, "error": str(e)}), 503

# ── LIST PATIENTS ─────────────────────────────────────────────────────────────
@app.route("/api/orthanc/patients")
def list_patients():
    """Return all patients stored in Orthanc."""
    try:
        ids = requests.get(f"{ORTHANC_URL}/patients", auth=orthanc_auth).json()
        patients = []
        for pid in ids[:50]:  # cap at 50
            info = requests.get(f"{ORTHANC_URL}/patients/{pid}", auth=orthanc_auth).json()
            main = info.get("MainDicomTags", {})
            patients.append({
                "id":          pid,
                "name":        main.get("PatientName", "Unknown"),
                "patientId":   main.get("PatientID", ""),
                "birthDate":   main.get("PatientBirthDate", ""),
                "sex":         main.get("PatientSex", ""),
                "studies":     info.get("Studies", []),
            })
        return jsonify(patients)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── LIST STUDIES FOR PATIENT ──────────────────────────────────────────────────
@app.route("/api/orthanc/patients/<patient_id>/studies")
def patient_studies(patient_id):
    try:
        info    = requests.get(f"{ORTHANC_URL}/patients/{patient_id}", auth=orthanc_auth).json()
        studies = []
        for sid in info.get("Studies", []):
            sinfo = requests.get(f"{ORTHANC_URL}/studies/{sid}", auth=orthanc_auth).json()
            tags  = sinfo.get("MainDicomTags", {})
            studies.append({
                "id":          sid,
                "date":        tags.get("StudyDate", ""),
                "description": tags.get("StudyDescription", ""),
                "modality":    tags.get("ModalitiesInStudy", ""),
                "series":      sinfo.get("Series", []),
            })
        return jsonify(studies)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── LIST SERIES FOR STUDY ─────────────────────────────────────────────────────
@app.route("/api/orthanc/studies/<study_id>/series")
def study_series(study_id):
    try:
        info   = requests.get(f"{ORTHANC_URL}/studies/{study_id}", auth=orthanc_auth).json()
        series = []
        for sid in info.get("Series", []):
            sinfo = requests.get(f"{ORTHANC_URL}/series/{sid}", auth=orthanc_auth).json()
            tags  = sinfo.get("MainDicomTags", {})
            series.append({
                "id":          sid,
                "description": tags.get("SeriesDescription", ""),
                "modality":    tags.get("Modality", ""),
                "bodyPart":    tags.get("BodyPartExamined", ""),
                "instances":   sinfo.get("Instances", []),
                "count":       len(sinfo.get("Instances", [])),
            })
        return jsonify(series)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── GET INSTANCE AS JPEG (for viewer) ────────────────────────────────────────
@app.route("/api/orthanc/instances/<instance_id>/preview")
def instance_preview(instance_id):
    """Return a DICOM instance rendered as JPEG base64."""
    try:
        r = requests.get(
            f"{ORTHANC_URL}/instances/{instance_id}/rendered",
            auth=orthanc_auth,
            params={"quality": 90}
        )
        b64 = base64.b64encode(r.content).decode()
        return jsonify({"base64": b64, "mimeType": "image/jpeg"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── GET DICOM FILE ────────────────────────────────────────────────────────────
@app.route("/api/orthanc/instances/<instance_id>/dicom")
def instance_dicom(instance_id):
    """Return raw DICOM file as base64."""
    try:
        r   = requests.get(f"{ORTHANC_URL}/instances/{instance_id}/file", auth=orthanc_auth)
        b64 = base64.b64encode(r.content).decode()
        return jsonify({"base64": b64})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── SEARCH BY BODY PART ───────────────────────────────────────────────────────
@app.route("/api/orthanc/search")
def search_studies():
    """
    Search Orthanc for studies matching a body part / modality.
    Query params: bodyPart, modality, patientName
    """
    body_part    = request.args.get("bodyPart", "")
    modality     = request.args.get("modality", "CT")
    patient_name = request.args.get("patientName", "*")

    query = {
        "Level": "Series",
        "Query": {
            "BodyPartExamined": body_part.upper() if body_part else "*",
            "Modality":         modality.upper(),
            "PatientName":      patient_name,
        }
    }
    try:
        r       = requests.post(f"{ORTHANC_URL}/tools/find", auth=orthanc_auth, json=query)
        results = r.json()
        series  = []
        for sid in results[:20]:
            sinfo = requests.get(f"{ORTHANC_URL}/series/{sid}", auth=orthanc_auth).json()
            tags  = sinfo.get("MainDicomTags", {})
            instances = sinfo.get("Instances", [])
            series.append({
                "seriesId":      sid,
                "description":   tags.get("SeriesDescription", ""),
                "modality":      tags.get("Modality", ""),
                "bodyPart":      tags.get("BodyPartExamined", ""),
                "instanceCount": len(instances),
                "firstInstance": instances[0] if instances else None,
            })
        return jsonify(series)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── REQUEST SCAN (NOW CREATES REAL MWL ENTRY) ─────────────────────────────────
@app.route("/api/orthanc/request-scan", methods=["POST"])
def request_scan():
    """
    Schedule a scan by creating a DICOM Modality Worklist (MWL) entry.

    When you select "neck scan" on the UI, this endpoint creates a worklist
    entry that the real CT scanner will pick up automatically when it queries
    the MWL server. The technologist at the scanner sees the scheduled scan
    and initiates it manually from the scanner console.

    Required JSON body:
        {
            "bodyPart": "NECK",         # Body part to scan
            "modality": "CT",           # CT, MR, etc.
            "patient": {                # Patient info
                "name": "John Doe",
                "id": "PAT001",
                "birthDate": "1990-01-01",
                "sex": "M"
            },
            "protocol": "ROUTINE"       # Scan protocol
        }

    Returns:
        JSON with accession number and success status
    """
    # ── STEP 1: Parse request ─────────────────────────────────────────────────
    data = request.json or {}
    body_part = data.get("bodyPart", "").upper()
    modality = data.get("modality", "CT").upper()
    patient = data.get("patient", {})
    protocol = data.get("protocol", "ROUTINE").upper()

    # ── STEP 2: Validate required fields ─────────────────────────────────────
    if not body_part:
        return jsonify({"success": False, "error": "bodyPart is required"}), 400

    # Allow unidentified patients — generate placeholder if not provided
    if not patient.get("name"):
        patient["name"] = f"UNIDENTIFIED-{int(time.time())}"
    if not patient.get("id"):
        patient["id"] = f"UNID-{int(time.time())}"

    # ── STEP 3: Create MWL entry ─────────────────────────────────────────────
    try:
        accession = insert_worklist_entry(
            patient_name=patient["name"],
            patient_id=patient["id"],
            patient_birth_date=patient.get("birthDate", ""),
            patient_sex=patient.get("sex", ""),
            body_part=body_part,
            modality=modality,
            protocol=protocol,
            referring_physician=patient.get("referringPhysician", ""),
            station_name=f"MEDVISION_{modality}",
            station_ae_title="MEDVISION_SCANNER"
        )

        # ── STEP 4: Return success ───────────────────────────────────────────
        return jsonify({
            "success": True,
            "accessionNumber": accession,
            "message": f"{modality} scan of {body_part} scheduled successfully",
            "details": {
                "patient": patient["name"],
                "bodyPart": body_part,
                "modality": modality,
                "protocol": protocol,
                "aeTitle": MWL_AE_TITLE,
                "mwlPort": MWL_PORT
            },
            "instructions": (
                "The scanner will automatically pick up this worklist entry "
                "when it queries the MWL server. The technologist at the scanner "
                "will see the scheduled scan and can initiate it."
            )
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ── MWL STATUS ENDPOINT ───────────────────────────────────────────────────────
@app.route("/api/mwl/status")
def mwl_status():
    """
    Return the current MWL server status and configuration.
    Use this to verify the MWL server is running and accessible.
    """
    entries = get_pending_worklist()
    return jsonify({
        "available": PNETDICOM_AVAILABLE,
        "aeTitle": MWL_AE_TITLE,
        "port": MWL_PORT,
        "pendingEntries": len(entries),
        "database": MWL_DB_PATH
    })

# ── LIST PENDING WORKLIST ENTRIES ─────────────────────────────────────────────
@app.route("/api/mwl/pending")
def mwl_pending():
    """
    Return all pending MWL entries.
    Use this to see what scans are scheduled and waiting for the scanner.
    """
    entries = get_pending_worklist()
    return jsonify({
        "count": len(entries),
        "entries": entries
    })

# ── COMPLETE A WORKLIST ENTRY ─────────────────────────────────────────────────
@app.route("/api/mwl/complete", methods=["POST"])
def mwl_complete():
    """
    Mark a worklist entry as completed (scan has been performed).
    The scanner won't pick up completed entries.

    JSON body: {"accessionNumber": "ACC123456"}
    """
    data = request.json or {}
    accession = data.get("accessionNumber", "")

    if not accession:
        return jsonify({"success": False, "error": "accessionNumber required"}), 400

    complete_worklist_entry(accession)
    return jsonify({"success": True, "message": f"Entry {accession} marked as completed"})

# ── DELETE A WORKLIST ENTRY ───────────────────────────────────────────────────
@app.route("/api/mwl/delete", methods=["POST"])
def mwl_delete():
    """
    Delete a worklist entry by ID.

    JSON body: {"id": 1}
    """
    data = request.json or {}
    entry_id = data.get("id")

    if not entry_id:
        return jsonify({"success": False, "error": "id required"}), 400

    delete_worklist_entry(entry_id)
    return jsonify({"success": True, "message": f"Entry {entry_id} deleted"})

# ── PATIENT MANAGEMENT ENDPOINTS ──────────────────────────────────────────────

@app.route("/api/patients", methods=["GET"])
def list_local_patients():
    """Return all patients from the local database."""
    conn = sqlite3.connect(MWL_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM patients ORDER BY created_at DESC").fetchall()
    conn.close()
    patients = []
    for row in rows:
        p = dict(row)
        patients.append({
            "id": p["id"],
            "patientId": p["patient_id"],
            "name": p["full_name"],
            "birthDate": p["date_of_birth"],
            "sex": p["sex"],
            "phone": p["phone"],
            "email": p["email"],
            "address": p["address"]
        })
    return jsonify(patients)

@app.route("/api/patients", methods=["POST"])
def create_patient():
    """Register a new patient in the local database."""
    data = request.json or {}
    name = data.get("name", "").strip()
    patient_id = data.get("patientId", "").strip()
    dob = data.get("birthDate", "")
    sex = data.get("sex", "")
    phone = data.get("phone", "")
    email = data.get("email", "")
    address = data.get("address", "")

    if not name:
        return jsonify({"success": False, "error": "Patient name is required"}), 400
    if not patient_id:
        patient_id = f"PAT{int(time.time())}"

    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite3.connect(MWL_DB_PATH)
    try:
        conn.execute("""
            INSERT INTO patients (patient_id, full_name, date_of_birth, sex, phone, email, address, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (patient_id, name, dob, sex, phone, email, address, now, now))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"success": False, "error": "Patient ID already exists"}), 400
    conn.close()
    return jsonify({"success": True, "patientId": patient_id, "message": f"Patient {name} registered"})

@app.route("/api/patients/<int:patient_db_id>", methods=["DELETE"])
def delete_patient(patient_db_id):
    """Delete a patient by database ID."""
    conn = sqlite3.connect(MWL_DB_PATH)
    conn.execute("DELETE FROM patients WHERE id = ?", (patient_db_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Patient deleted"})

# ── POLL FOR NEW STUDIES ──────────────────────────────────────────────────────
@app.route("/api/orthanc/poll")
def poll_new_studies():
    """
    Poll Orthanc for studies received in the last N minutes.
    Used after a scan request to detect when images arrive.
    """
    minutes = int(request.args.get("minutes", 10))
    try:
        r = requests.post(
            f"{ORTHANC_URL}/tools/find",
            auth=orthanc_auth,
            json={
                "Level": "Study",
                "Query": {},
                "Since": 0,
                "Limit": 10,
                "OrderBy": [{"Type": "DicomTag", "Tag": "StudyDate", "Direction": "DESC"}]
            }
        )
        ids     = r.json()
        studies = []
        for sid in ids[:5]:
            sinfo = requests.get(f"{ORTHANC_URL}/studies/{sid}", auth=orthanc_auth).json()
            tags  = sinfo.get("MainDicomTags", {})
            studies.append({
                "id":          sid,
                "date":        tags.get("StudyDate", ""),
                "description": tags.get("StudyDescription", ""),
                "patientName": sinfo.get("PatientMainDicomTags", {}).get("PatientName", ""),
            })
        return jsonify(studies)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── ROBOFLOW PROXY ────────────────────────────────────────────────────────────
@app.route("/api/analyze", methods=["POST"])
def analyze():
    """Proxy to Roboflow — avoids CORS issues from browser."""
    try:
        body = request.json or {}
        r    = requests.post(ROBOFLOW_URL, json=body, timeout=30)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── MIMO LLM PROXY ────────────────────────────────────────────────────────────
@app.route("/api/llm", methods=["POST"])
def llm_proxy():
    """Proxy to Mimo LLM — keeps API key off the frontend."""
    try:
        body = request.json or {}
        r    = requests.post(
            MIMO_URL,
            headers={"api-key": MIMO_KEY, "Content-Type": "application/json"},
            json=body,
            timeout=60
        )
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  MedVision AI — Backend Server")
    print("=" * 60)
    print(f"  Web app  →  http://localhost:5000")
    print(f"  Orthanc  →  {ORTHANC_URL}")
    print(f"  MWL      →  AE: {MWL_AE_TITLE} | Port: {MWL_PORT}")
    print()

    # Initialize MWL database
    print("  Initializing MWL database...")
    init_mwl_db()

    # Check Orthanc connection
    print("  Checking Orthanc connection...")
    try:
        r = requests.get(f"{ORTHANC_URL}/system", auth=orthanc_auth, timeout=2)
        print(f"  ✅ Orthanc connected — v{r.json().get('Version','?')}")
    except Exception:
        print("  ⚠️  Orthanc not running — install from https://www.orthanc-server.com")
        print("      App will still work for manual DICOM uploads.")

    # Start MWL SCP listener in background thread
    print()
    print("  Starting DICOM Modality Worklist (MWL) server...")
    start_mwl_thread()

    print()
    print("=" * 60)
    print("  Server ready!")
    print("  - Open http://localhost:5000 in your browser")
    print("  - Schedule scans via the UI or POST /api/orthanc/request-scan")
    print("  - Configure your scanner to query MWL server at this machine's IP")
    print("=" * 60)
    print()

    app.run(host="0.0.0.0", port=5000, debug=True)