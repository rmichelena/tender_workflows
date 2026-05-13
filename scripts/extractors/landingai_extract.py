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


def extract_document(filepath, model="dpt-2-mini", output_dir=None, clean=True):
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

    # Parse document
    job = client.parse_jobs.create(
        document=open(filepath, "rb"),
        model=model,
    )

    # Poll until complete
    while job.status != "complete":
        time.sleep(2)
        job = client.parse_jobs.get(job_id=job.job_id)

    elapsed = time.time() - start

    if job.data is None:
        print(f"ERROR: Job failed. Status: {job.status}, reason: {job.failure_reason}", file=sys.stderr)
        sys.exit(1)

    response = job.data
    raw_markdown = response.markdown
    chunks = response.chunks
    metadata = response.metadata

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
    args = parser.parse_args()

    if not os.path.exists(args.document):
        print(f"ERROR: File not found: {args.document}", file=sys.stderr)
        sys.exit(1)

    result = extract_document(
        args.document,
        model=args.model,
        output_dir=args.output_dir,
        clean=not args.no_clean,
    )


if __name__ == "__main__":
    main()
