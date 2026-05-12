"""
Shared utilities for tender_procurement extractors.

Centralizes config loading, OAuth credential management, filename sanitization,
ligature fixing, table extraction, deduplication, and DocAI response parsing.

Usage in extractors:
    from common import load_config, get_creds, sanitize_filename, fix_ligatures, ...
"""

import os, re, json, configparser

# Config file search order: extractors.conf, ../extractors.conf, ../../extractors.conf
_CONF_CACHE = None

CONF_SEARCH_PATHS = [
    os.path.join(os.path.dirname(__file__), "extractors.conf"),
    os.path.join(os.path.dirname(__file__), "..", "extractors.conf"),
]


def load_config():
    """Load extractors.conf. Results are cached. Returns configparser.ConfigParser."""
    global _CONF_CACHE
    if _CONF_CACHE is not None:
        return _CONF_CACHE

    conf_path = None
    for p in CONF_SEARCH_PATHS:
        if os.path.exists(p):
            conf_path = p
            break

    if conf_path is None:
        raise FileNotFoundError(
            f"extractors.conf not found. Searched: {CONF_SEARCH_PATHS}\n"
            "Copy extractors.conf.example to extractors.conf and customize."
        )

    cfg = configparser.ConfigParser()
    cfg.read(conf_path)
    _CONF_CACHE = cfg
    return cfg


def get_docai_config():
    """Return DocAI config as a dict with sensible defaults."""
    cfg = load_config()
    return {
        "project_id": cfg.get("docai", "project_id"),
        "location": cfg.get("docai", "location", fallback="us"),
        "processor_id": cfg.get("docai", "processor_id"),
        "gcs_bucket": cfg.get("docai", "gcs_bucket", fallback=""),
        "token_path": _resolve_token_path(cfg.get("docai", "token_path")),
        "online_page_limit": cfg.getint("docai", "online_page_limit", fallback=15),
        "chunk_size": cfg.getint("docai", "chunk_size", fallback=500),
        "poll_interval": cfg.getint("docai", "poll_interval", fallback=20),
        "max_wait": cfg.getint("docai", "max_wait", fallback=3600),
    }


def _resolve_token_path(raw_path):
    """Resolve token path: absolute as-is, relative to conf file directory."""
    if os.path.isabs(raw_path):
        return raw_path
    # Relative to conf file location
    cfg_dir = os.path.dirname(os.path.abspath(CONF_SEARCH_PATHS[0]))
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

    if creds.expired:
        try:
            creds.refresh(Request(timeout=30))
        except Exception as e:
            raise SystemExit(f"Failed to refresh OAuth token: {e}")

    return creds


def sanitize_filename(filepath):
    """Sanitize a filename for output: ASCII-safe, consistent across all extractors."""
    base = os.path.basename(filepath).rsplit(".", 1)[0]
    return re.sub(r'[^a-zA-Z0-9._-]', '_', base)


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
    """Parse documentLayout blocks into structured dicts."""
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
    """Parse chunkedDocument chunks into structured dicts."""
    chunks = []
    for c in chunks_raw:
        page_span = c.get("pageSpan", {})
        if page_span:
            page_span = {
                "pageStart": page_span.get("pageStart", 1) + page_offset,
                "pageEnd": page_span.get("pageEnd", 1) + page_offset,
            }
        chunks.append({
            "chunk_id": c.get("chunkId"),
            "content": c.get("content", ""),
            "page_span": page_span,
        })
    return chunks


def build_markdown(all_chunks):
    """Build markdown from parsed chunks, deduplicating by (chunk_id, content).

    Deduplication uses (chunk_id, content) tuples to preserve legitimately
    repeated text (legal clauses, annex headers) that appear in different chunks.
    """
    seen = set()
    md_parts = []
    for c in all_chunks:
        content = c["content"].strip()
        key = (c["chunk_id"], content)
        if content and key not in seen:
            seen.add(key)
            md_parts.append(content)
    md = "\n\n---\n\n".join(md_parts)
    return fix_ligatures(md)
