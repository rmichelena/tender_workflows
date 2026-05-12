"""
Shared utilities for tender_procurement extractors.

Centralizes config loading, OAuth credential management, filename sanitization,
ligature fixing, table extraction, deduplication, and DocAI response parsing.

Usage in extractors:
    from common import load_config, get_creds, sanitize_filename, fix_ligatures, ...
"""

import os, re, json, configparser, unicodedata

# Config file search order: extractors.conf, ../extractors.conf, ../../extractors.conf
_CONF_CACHE = None
_CONF_PATH = None  # Resolved path of the loaded config

CONF_SEARCH_PATHS = [
    os.path.join(os.path.dirname(__file__), "extractors.conf"),
    os.path.join(os.path.dirname(__file__), "..", "extractors.conf"),
]


def load_config():
    """Load extractors.conf. Results are cached. Returns configparser.ConfigParser."""
    global _CONF_CACHE, _CONF_PATH
    if _CONF_CACHE is not None:
        return _CONF_CACHE

    for p in CONF_SEARCH_PATHS:
        if os.path.exists(p):
            _CONF_PATH = p
            break

    if _CONF_PATH is None:
        raise FileNotFoundError(
            f"extractors.conf not found. Searched: {CONF_SEARCH_PATHS}\n"
            "Copy extractors.conf.example to extractors.conf and customize."
        )

    cfg = configparser.ConfigParser()
    cfg.read(_CONF_PATH)
    _CONF_CACHE = cfg
    return cfg


def get_docai_config():
    """Return DocAI config as a dict with sensible defaults."""
    cfg = load_config()
    return {
        "project_id": cfg.get("docai", "project_id"),
        "location": cfg.get("docai", "location", fallback="us"),
        "processor_id": cfg.get("docai", "processor_id"),
        "processor_version": cfg.get("docai", "processor_version", fallback=""),
        "gcs_bucket": cfg.get("docai", "gcs_bucket", fallback=""),
        "token_path": _resolve_token_path(cfg.get("docai", "token_path")),
        "online_page_limit": cfg.getint("docai", "online_page_limit", fallback=15),
        "chunk_size": cfg.getint("docai", "chunk_size", fallback=500),
        "poll_interval": cfg.getint("docai", "poll_interval", fallback=20),
        "max_wait": cfg.getint("docai", "max_wait", fallback=3600),
    }


def get_processor_endpoint(cfg):
    """Build the DocAI process endpoint URL including version if specified.
    
    If processor_version is set, uses the version-specific endpoint:
      /v1/{processor}/processorVersions/{version}:process
    Otherwise uses the default:
      /v1/{processor}:process
    """
    base = f"projects/{cfg['project_id']}/locations/{cfg['location']}/processors/{cfg['processor_id']}"
    ver = cfg.get("processor_version", "")
    if ver:
        return f"/v1/{base}/processorVersions/{ver}:process"
    return f"/v1/{base}:process"


def _resolve_token_path(raw_path):
    """Resolve token path: absolute as-is, relative to actual conf file directory."""
    if os.path.isabs(raw_path):
        return raw_path
    # Relative to the config file that was actually loaded
    cfg_dir = os.path.dirname(os.path.abspath(_CONF_PATH)) if _CONF_PATH else os.path.dirname(__file__)
    return os.path.join(cfg_dir, raw_path)


def get_creds(token_path):
    """Load and refresh OAuth credentials. Exits with actionable message on failure."""
    import requests
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    try:
        with open(token_path) as f:
            creds = Credentials.from_authorized_user_info(json.load(f))
    except FileNotFoundError:
        raise SystemExit(f"Token file not found: {token_path}\nRun gsetup or copy your OAuth token.")
    except json.JSONDecodeError as e:
        raise SystemExit(f"Invalid token file {token_path}: {e}")

    if not creds.valid:
        try:
            creds.refresh(Request())
        except Exception as e:
            raise SystemExit(f"Failed to refresh OAuth token: {e}")

    return creds


def sanitize_filename(filepath):
    """Sanitize a filename for output: ASCII-safe with NFKD normalization, consistent across all extractors."""
    base = os.path.basename(filepath).rsplit(".", 1)[0]
    # NFKD decomposes accented chars (é→e+́) then encode strips combining marks
    ascii_base = unicodedata.normalize('NFKD', base).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-zA-Z0-9._-]', '_', ascii_base)


def fix_ligatures(text):
    """Replace common Unicode ligatures with ASCII equivalents."""
    return (text.replace("\ufb01", "fi").replace("\ufb02", "fl")
                .replace("\ufb00", "ff").replace("\ufb03", "ffi")
                .replace("\ufb04", "ffl"))


def extract_table_rows(table_block):
    """Extract rows from a DocAI tableBlock into list of lists of strings."""
    rows = []
    for hr in table_block.get("headerRows", []):
        rows.append([
            " ".join(b2.get("textBlock", {}).get("text", "")
                     for b2 in c.get("blocks", [])).strip()
            for c in hr.get("cells", [])
        ])
    for br in table_block.get("bodyRows", []):
        rows.append([
            " ".join(b2.get("textBlock", {}).get("text", "")
                     for b2 in c.get("blocks", [])).strip()
            for c in br.get("cells", [])
        ])
    return rows


def parse_layout_blocks(blocks_raw, page_offset=0):
    """Parse documentLayout blocks into structured dicts.
    
    TextBlock types include: paragraph, subtitle, heading-1..5, header, footer.
    Headers/footers are tagged for downstream filtering.
    """
    blocks = []
    for b in blocks_raw:
        entry = {"block_id": b.get("blockId", ""), "_page_offset": page_offset}
        if "textBlock" in b:
            tb = b["textBlock"]
            entry.update({
                "type": "text",
                "text": tb.get("text", ""),
                "semantic_type": tb.get("type", "paragraph"),
            })
        elif "tableBlock" in b:
            rows = extract_table_rows(b["tableBlock"])
            entry.update({
                "type": "table",
                "table_rows": rows,
                "table_row_count": len(rows),
            })
        blocks.append(entry)
    return blocks


def parse_chunks(chunks_raw, page_offset=0):
    """Parse chunkedDocument chunks into structured dicts.
    
    Each chunk may contain:
    - content: the main text (may include __START_OF_ANNOTATION__ image descriptions)
    - pageHeaders/pageFooters: header/footer text for the chunk's pages
    - sourceBlockIds: references to layout blocks that produced this chunk
    
    is_image_annotation is set True when content is an image annotation
    (logo, stamp, signature, diagram description) rather than real text.
    """
    chunks = []
    for c in chunks_raw:
        page_span = c.get("pageSpan", {})
        if page_span:
            page_span = {
                "pageStart": page_span.get("pageStart", 1) + page_offset,
                "pageEnd": page_span.get("pageEnd", 1) + page_offset,
            }
        content = c.get("content", "")
        is_img = ("__START_OF_ANNOTATION__" in content 
                  or "**Image Description" in content)
        chunks.append({
            "chunk_id": c.get("chunkId"),
            "content": content,
            "page_span": page_span,
            "is_image_annotation": is_img,
            "source_block_ids": c.get("sourceBlockIds", []),
            "page_headers": [h.get("text", "") for h in c.get("pageHeaders", [])],
            "page_footers": [f.get("text", "") for f in c.get("pageFooters", [])],
        })
    return chunks


def build_markdown(all_chunks, filter_headers_footers=True, filter_image_annotations=True):
    """Build markdown from parsed chunks, deduplicating by (chunk_id, content).

    Deduplication uses (chunk_id, content) tuples to preserve legitimately
    repeated text (legal clauses, annex headers) that appear in different chunks.
    
    Filtering options:
    - filter_headers_footers: strip chunks whose semantic_type is header/footer
    - filter_image_annotations: strip chunks where is_image_annotation=True
      (logos, stamps, signatures — usually noise for text extraction)
    
    Image annotations contain verbose descriptions like "The image displays a logo..."
    that pollute the text. For technical diagrams, use a second pass with
    enableImageAnnotation=True and classify each annotation.
    """
    seen = set()
    md_parts = []
    for c in all_chunks:
        content = c["content"].strip()
        key = (c["chunk_id"], content)
        if not content or key in seen:
            continue
        # Skip chunks that are pure header/footer
        if filter_headers_footers and c.get("semantic_type") in ("header", "footer"):
            continue
        # Skip image annotations (logos, stamps, signatures)
        if filter_image_annotations and c.get("is_image_annotation"):
            continue
        seen.add(key)
        md_parts.append(content)
    md = "\n\n---\n\n".join(md_parts)
    return fix_ligatures(md)


def extract_image_annotations(all_chunks):
    """Extract image annotation chunks for second-pass classification.
    
    Returns list of dicts with:
    - chunk_id, content, page_span, source_block_ids
    - annotation_text: just the annotation part (after __START_OF_ANNOTATION__)
    
    Use this to classify annotations into: logo/stamp/signature (discard)
    vs technical diagram/specification (keep and extract).
    """
    annotations = []
    for c in all_chunks:
        if not c.get("is_image_annotation"):
            continue
        content = c["content"]
        # Strip the annotation marker and extract just the description
        marker = "__START_OF_ANNOTATION__"
        idx = content.find(marker)
        annotation_text = content[idx + len(marker):].strip() if idx >= 0 else content
        annotations.append({
            "chunk_id": c["chunk_id"],
            "content": content,
            "annotation_text": annotation_text,
            "page_span": c.get("page_span", {}),
            "source_block_ids": c.get("source_block_ids", []),
        })
    return annotations
