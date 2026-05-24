#!/usr/bin/env python3
"""
LandingAI ADE document extraction for tender_procurement.

Parses PDF/image documents into structured Markdown using LandingAI's
document extraction API (dpt-2 models). Supports both mini (fast) and
latest (best quality) models.

Usage:
    python landingai_extract.py document.pdf
    python landingai_extract.py document.pdf --model dpt-2-latest
    python landingai_extract.py document.pdf --output-dir /path/to/output
    python landingai_extract.py document.pdf --no-clean  # skip post-processing

Requirements:
    - landingai-ade package (pip install landingai-ade)
    - VISION_AGENT_API_KEY in .env_landingai or environment
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Add parent dir for imports
sys.path.insert(0, os.path.dirname(__file__))
from common import sanitize_filename, clean_ade_output, fix_ligatures


def load_api_key():
    """Load LandingAI API key from .env file or environment."""
    key = os.environ.get("VISION_AGENT_API_KEY", "")
    if key:
        return key

    # Try .env_landingai in same directory as this script
    env_path = os.path.join(os.path.dirname(__file__), ".env_landingai")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("VISION_AGENT_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    if key:
                        return key

    print("ERROR: VISION_AGENT_API_KEY not found.", file=sys.stderr)
    print("  Set env var or create scripts/extractors/.env_landingai", file=sys.stderr)
    sys.exit(1)


def extract_document(
    filepath,
    model="dpt-2-mini",
    output_dir=None,
    clean=True,
    *,
    max_wait=3600,
    poll_interval=2,
    download_timeout=120,
):
    """Extract document using LandingAI ADE.

    Args:
        filepath: Path to PDF or image file.
        model: Model name (dpt-2-mini or dpt-2-latest).
        output_dir: Directory for output files (default: same as input).
        clean: Apply clean_ade_output + fix_ligatures post-processing.

    Returns:
        dict with keys: markdown, raw_markdown, chunks, metadata, output_path, json_path
    """
    from landingai_ade import LandingAIADE

    api_key = load_api_key()
    client = LandingAIADE(apikey=api_key)

    filename = os.path.basename(filepath)
    stem = sanitize_filename(filepath)

    print(f"Extracting: {filename}")
    print(f"  Model: {model}")
    print(f"  File size: {os.path.getsize(filepath):,} bytes")

    start = time.time()

    # Parse document — create returns only job_id
    create_resp = client.parse_jobs.create(
        document=open(filepath, "rb"),
        model=model,
    )
    job_id = create_resp.job_id

    # Poll until complete (get() returns ParseJobGetResponse with status/data)
    job = client.parse_jobs.get(job_id=job_id)
    waited = 0.0
    while job.status not in ("completed", "complete", "failed"):
        if waited >= max_wait:
            print(
                f"ERROR: LandingAI job {job_id} did not complete within {max_wait}s "
                f"(last status: {job.status})",
                file=sys.stderr,
            )
            sys.exit(2)
        time.sleep(poll_interval)
        waited += poll_interval
        job = client.parse_jobs.get(job_id=job_id)

    elapsed = time.time() - start

    if job.status == "failed":
        print(f"ERROR: Job failed. reason: {getattr(job, 'failure_reason', 'unknown')}", file=sys.stderr)
        sys.exit(1)

    # SDK may return data=None with output_url for large documents
    # In that case, download from S3 directly
    if job.data is not None:
        response = job.data
        raw_markdown = response.markdown
        chunks = response.chunks
        metadata = response.metadata
    elif job.output_url:
        import urllib.request
        print("  Downloading result from output_url...")
        raw_bytes = urllib.request.urlopen(job.output_url, timeout=download_timeout).read()
        import json as _json
        result_data = _json.loads(raw_bytes)
        raw_markdown = result_data.get("markdown", "")
        chunks_data_raw = result_data.get("chunks", [])
        metadata = result_data.get("metadata")

        # Wrap raw chunks into simple objects for downstream compatibility
        class _Chunk:
            def __init__(self, d):
                self.id = d.get("id", "")
                self.type = d.get("type", "")
                self.markdown = d.get("markdown", "")
                self.grounding = None
                if "grounding" in d:
                    g = d["grounding"]
                    class _G:
                        page = g.get("page", 0)
                    self.grounding = _G()
        chunks = [_Chunk(c) for c in chunks_data_raw]
    else:
        print("ERROR: Job completed but no data or output_url", file=sys.stderr)
        sys.exit(1)

    print(f"  Completed in {elapsed:.1f}s")
    print(f"  Chunks: {len(chunks)}")
    if metadata:
        print(f"  Credits: {metadata.credit_usage}")
        print(f"  Pages: {metadata.page_count}")
        if metadata.failed_pages:
            print(f"  Failed pages: {metadata.failed_pages}")

    # Count chunk types
    from collections import Counter
    types = Counter(c.type for c in chunks)
    print(f"  Chunk types: {dict(types)}")

    # Post-process
    if clean:
        markdown = clean_ade_output(raw_markdown)
        markdown = fix_ligatures(markdown)
    else:
        markdown = raw_markdown

    print(f"  Markdown: {len(raw_markdown):,} raw -> {len(markdown):,} chars ({'cleaned' if clean else 'raw'})")

    # Save outputs
    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(output_dir, exist_ok=True)

    model_tag = model.replace("dpt-2-", "")
    out_base = f"{stem}_ade_{model_tag}"
    out_md = os.path.join(output_dir, f"{out_base}.md")
    out_json = os.path.join(output_dir, f"{out_base}.json")

    with open(out_md, "w") as f:
        f.write(markdown)

    # Build serializable chunks
    chunks_data = []
    for c in chunks:
        chunk_dict = {
            "id": c.id,
            "type": c.type,
            "markdown": c.markdown,
        }
        if c.grounding:
            chunk_dict["page"] = c.grounding.page
        chunks_data.append(chunk_dict)

    json_data = {
        "source": filename,
        "extractor": f"landingai_ade_{model}",
        "model": model,
        "job_id": job.job_id,
        "chunks": len(chunks),
        "markdown_length": len(markdown),
        "raw_markdown_length": len(raw_markdown),
        "elapsed_seconds": round(elapsed, 1),
        "chunks_data": chunks_data,
    }
    if metadata:
        json_data["metadata"] = {
            "credit_usage": metadata.credit_usage,
            "page_count": metadata.page_count,
            "duration_ms": metadata.duration_ms,
            "version": metadata.version,
            "failed_pages": metadata.failed_pages,
        }

    with open(out_json, "w") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    print(f"  Saved: {out_md}")
    print(f"  Saved: {out_json}")

    return {
        "markdown": markdown,
        "raw_markdown": raw_markdown,
        "chunks": chunks_data,
        "metadata": json_data.get("metadata"),
        "output_path": out_md,
        "json_path": out_json,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract document using LandingAI ADE")
    parser.add_argument("document", help="Path to PDF or image file")
    parser.add_argument("--model", default="dpt-2-mini",
                        choices=["dpt-2-mini", "dpt-2-latest"],
                        help="Extraction model (default: dpt-2-mini)")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: same as input file)")
    parser.add_argument("--no-clean", action="store_true",
                        help="Skip clean_ade_output post-processing")
    parser.add_argument("--max-wait", type=int, default=3600,
                        help="Max seconds to wait for parse job completion (default: 3600)")
    parser.add_argument("--poll-interval", type=int, default=2,
                        help="Seconds between status polls (default: 2)")
    parser.add_argument("--download-timeout", type=int, default=120,
                        help="Timeout seconds for output_url download (default: 120)")
    args = parser.parse_args()

    if not os.path.exists(args.document):
        print(f"ERROR: File not found: {args.document}", file=sys.stderr)
        sys.exit(1)

    result = extract_document(
        args.document,
        model=args.model,
        output_dir=args.output_dir,
        clean=not args.no_clean,
        max_wait=args.max_wait,
        poll_interval=args.poll_interval,
        download_timeout=args.download_timeout,
    )


if __name__ == "__main__":
    main()
