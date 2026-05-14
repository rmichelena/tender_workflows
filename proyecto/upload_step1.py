#!/usr/bin/env python3
"""Upload step_1_normalizados to Google Drive."""
import json, requests, os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

TOKEN_PATH = "/opt/data/google_token_personal.json"
with open(TOKEN_PATH) as f:
    creds = Credentials.from_authorized_user_info(json.load(f))
if creds.expired:
    creds.refresh(Request())
token = creds.token

HEADERS = {"Authorization": f"Bearer {token}"}
PARENT_ID = "19l8U9uPq5DgeGPggZcRqxG5uEPQ2eqGO"

# 1. Create "artifacts" folder
r = requests.post("https://www.googleapis.com/drive/v3/files",
    headers={**HEADERS, "Content-Type": "application/json"},
    json={"name": "artifacts", "mimeType": "application/vnd.google-apps.folder", "parents": [PARENT_ID]},
    timeout=30
)
artifacts_folder = r.json()
artifacts_id = artifacts_folder["id"]
print(f"Created artifacts folder: {artifacts_id}")

# 2. Create "step_1_normalizados" subfolder
r = requests.post("https://www.googleapis.com/drive/v3/files",
    headers={**HEADERS, "Content-Type": "application/json"},
    json={"name": "step_1_normalizados", "mimeType": "application/vnd.google-apps.folder", "parents": [artifacts_id]},
    timeout=30
)
step1_folder = r.json()
step1_id = step1_folder["id"]
print(f"Created step_1_normalizados folder: {step1_id}")

# 3. Upload all .md files from local step_1_normalizados
LOCAL_DIR = "/opt/data/workspace/tender_procurement/proyecto/artifacts/step_1_normalizados"
md_files = [f for f in os.listdir(LOCAL_DIR) if f.endswith('.md')]
print(f"\nUploading {len(md_files)} markdown files...")

for fname in sorted(md_files):
    fpath = os.path.join(LOCAL_DIR, fname)
    size = os.path.getsize(fpath)
    
    # Multipart upload
    boundary = f"hermes_{fname.replace('.','_')}"
    metadata = {"name": fname, "parents": [step1_id]}
    
    with open(fpath, "rb") as f:
        file_content = f.read()
    
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n--{boundary}\r\nContent-Type: text/markdown\r\n\r\n"
    ).encode() + file_content + f"\r\n--{boundary}--\r\n".encode()
    
    r = requests.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        headers={**HEADERS, "Content-Type": f"multipart/related; boundary={boundary}"},
        data=body, timeout=120)
    
    if r.status_code in (200, 201):
        print(f"  ✅ {fname} ({size:,} bytes)")
    else:
        print(f"  ❌ {fname}: {r.status_code} {r.text[:200]}")

print("\nDone!")
