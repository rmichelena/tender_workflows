#!/usr/bin/env python3
"""Download ICAO_tender files from Google Drive."""
import json, requests, os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

TOKEN_PATH = "/opt/data/google_token_personal.json"
INPUTS = "/opt/data/workspace/tender_procurement/proyecto/inputs"

with open(TOKEN_PATH) as f:
    creds = Credentials.from_authorized_user_info(json.load(f))
if creds.expired:
    creds.refresh(Request())
token = creds.token

files = [
    ("1CCCyjTqp3-FdFkLi8xoaxbpw3JU6iiB0", "ITB-ICAO-00068_V2.docx"),
    ("16z7pC7q3IXmXSmC90K4tC6gliTHdaTJ8", "CLARIFICATIONS_SET3_V4.docx"),
    ("16ejSUQIRJczxQWJQSs1zVUPz00EkXVNS", "ITB-ICAO-00068_Tech_Specs_V12.docx"),
    ("1fhBoFquROZHmA-2MZX7IKMdbgh-PEq-g", "CLARIFICATIONS_SET2.docx"),
    ("1aYKCFM6lujuEXNHH8xQNOkFpvIjeQjBC", "CLARIFICATIONS_SET1.docx"),
    ("1i3GOBHZa-LobfCB6XW7S0Fc_em13VcIu", "CLARIFICATIONS_SET4_v2.pdf"),
]

for fid, name in files:
    dest = os.path.join(INPUTS, name)
    url = f"https://www.googleapis.com/drive/v3/files/{fid}?alt=media&supportsAllDrives=true"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=120)
    print(f"{name}: status={r.status_code}, size={len(r.content)} bytes")
    with open(dest, "wb") as f:
        f.write(r.content)

print("\nDone! Listing:")
os.system(f"ls -lh {INPUTS}/")
