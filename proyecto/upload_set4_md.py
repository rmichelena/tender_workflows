#!/usr/bin/env python3
"""Upload single file to Drive."""
import json, requests, os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

TOKEN_PATH = "/opt/data/google_token_personal.json"
with open(TOKEN_PATH) as f:
    creds = Credentials.from_authorized_user_info(json.load(f))
if creds.expired:
    creds.refresh(Request())
token = creds.token

STEP1_ID = "1l5gL1Yjqagd43oNtZTa6tsmvB2vRlS8i"
FNAME = "CLARIFICATIONS_SET4_v2_markitdown.md"
FPATH = f"/opt/data/workspace/tender_procurement/proyecto/artifacts/step_1_normalizados/{FNAME}"

boundary = "hermes_set4_markitdown"
metadata = {"name": FNAME, "parents": [STEP1_ID]}
with open(FPATH, "rb") as f:
    file_content = f.read()

body = (
    f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
    f"{json.dumps(metadata)}\r\n--{boundary}\r\nContent-Type: text/markdown\r\n\r\n"
).encode() + file_content + f"\r\n--{boundary}--\r\n".encode()

r = requests.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
    headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/related; boundary={boundary}"},
    data=body, timeout=120)
print(f"{FNAME}: {r.status_code} ({len(file_content):,} bytes)")
