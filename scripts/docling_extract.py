#!/usr/bin/env python3
"""
Docling document extractor — converts PDF/DOCX/PPTX/XLSX to Markdown
via the Docling Serve API at https://docling.infinitek.pe

Usage:
    python3 docling_extract.py <input_file> [output_file] [--async] [--json]

Examples:
    # Sync conversion to markdown (default)
    python3 docling_extract.py "EETT DEFINITIVO FINAL COMPLETO (4).pdf" eett.md

    # Async conversion (for large files)
    python3 docling_extract.py big.pdf big.md --async

    # Output raw JSON instead of extracting just the markdown
    python3 docling_extract.py doc.docx doc_raw.json --json

    # Stdout (no output file)
    python3 docling_extract.py doc.docx
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

DOCLING_BASE = "https://docling.infinitek.pe"
DEFAULT_TIMEOUT = 600  # 10 min for large PDFs
ASYNC_POLL_INTERVAL = 5  # seconds
ASYNC_MAX_WAIT = 900  # 15 min max


def convert_sync(file_path, include_images=False):
    """Synchronous conversion — blocks until done."""
    filename = os.path.basename(file_path)

    # Build multipart manually (no requests dependency)
    boundary = f"docling_boundary_{os.getpid()}_{int(time.time())}"

    # Form fields
    fields = [
        ("image_export_mode", "placeholder"),
        ("include_images", "false" if not include_images else "true"),
    ]

    # Build body
    parts = []
    for name, value in fields:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        )

    # File part
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    )

    body = "".join(parts).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    url = f"{DOCLING_BASE}/v1/convert/file"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )

    ctx = ssl_context()
    with urllib.request.urlopen(req, context=ctx, timeout=DEFAULT_TIMEOUT) as resp:
        result = json.loads(resp.read().decode())

    return result


def convert_async(file_path, include_images=False):
    """Asynchronous conversion — polls until done."""
    filename = os.path.basename(file_path)
    boundary = f"docling_boundary_{os.getpid()}_{int(time.time())}"

    fields = [
        ("image_export_mode", "placeholder"),
        ("include_images", "false" if not include_images else "true"),
    ]

    parts = []
    for name, value in fields:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        )

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    )

    body = "".join(parts).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    # Submit job
    url = f"{DOCLING_BASE}/v1/convert/file/async"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    ctx = ssl_context()
    with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
        submit = json.loads(resp.read().decode())

    task_id = submit.get("task_id")
    if not task_id:
        raise RuntimeError(f"No task_id in response: {submit}")

    print(f"  Task submitted: {task_id}", file=sys.stderr)

    # Poll
    elapsed = 0
    while elapsed < ASYNC_MAX_WAIT:
        time.sleep(ASYNC_POLL_INTERVAL)
        elapsed += ASYNC_POLL_INTERVAL

        status_url = f"{DOCLING_BASE}/v1/status/poll/{task_id}"
        req = urllib.request.Request(status_url)
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            status_data = json.loads(resp.read().decode())

        # Docling Serve versions differ: some return `status`, current service returns `task_status`.
        status = status_data.get("status") or status_data.get("task_status", "unknown")
        print(f"  [{elapsed}s] Status: {status}", file=sys.stderr)

        if status in ("success", "completed"):
            # Get result
            result_url = f"{DOCLING_BASE}/v1/result/{task_id}"
            req = urllib.request.Request(result_url)
            with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
                result = json.loads(resp.read().decode())
            return result

        if status == "failure":
            raise RuntimeError(f"Docling conversion failed: {status_data}")

    raise TimeoutError(f"Conversion did not complete in {ASYNC_MAX_WAIT}s")


def extract_markdown(result):
    """Extract markdown content from Docling API response."""
    if not result:
        raise ValueError(f"Unexpected response format: {json.dumps(result)[:500]}")

    # Docling Serve may return either a list of file results or a single result object.
    if isinstance(result, list):
        if len(result) == 0:
            raise ValueError(f"Unexpected response format: {json.dumps(result)[:500]}")
        doc = result[0]
    elif isinstance(result, dict):
        doc = result
    else:
        raise ValueError(f"Unexpected response format: {json.dumps(result)[:500]}")

    status = doc.get("status") or doc.get("task_status") or "success"
    if status not in ("success", "completed"):
        raise RuntimeError(f"Conversion status: {status}, errors: {doc.get('errors', [])}")

    md_content = doc.get("document", {}).get("md_content")
    if not md_content:
        raise ValueError("No md_content in response")

    title = doc.get("document", {}).get("title", "")
    return md_content, title


def ssl_context():
    """Create SSL context that doesn't verify (self-signed/internal)."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def main():
    parser = argparse.ArgumentParser(description="Convert documents via Docling Serve API")
    parser.add_argument("input", help="Input file (PDF, DOCX, PPTX, XLSX, etc.)")
    parser.add_argument("output", nargs="?", help="Output file (default: stdout)")
    parser.add_argument("--async", dest="use_async", action="store_true",
                        help="Use async conversion (recommended for large files)")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON instead of extracting markdown")
    parser.add_argument("--images", action="store_true",
                        help="Include images in output")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    file_size = os.path.getsize(args.input)
    print(f"Converting: {args.input} ({file_size:,} bytes)", file=sys.stderr)

    if args.use_async:
        result = convert_async(args.input, include_images=args.images)
    else:
        result = convert_sync(args.input, include_images=args.images)

    if args.json:
        output = json.dumps(result, indent=2, ensure_ascii=False)
    else:
        md_content, title = extract_markdown(result)
        if title:
            output = f"# {title}\n\n{md_content}"
        else:
            output = md_content

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Output written: {args.output} ({len(output):,} chars)", file=sys.stderr)
    else:
        print(output)

    # Stats
    if not args.json:
        lines = output.count("\n") + 1
        print(f"Stats: {len(output):,} chars, {lines:,} lines", file=sys.stderr)


if __name__ == "__main__":
    main()
