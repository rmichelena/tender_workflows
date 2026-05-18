#!/usr/bin/env python3
"""
Docling Serve extractor — converts PDF/DOCX/PPTX/XLSX to Markdown.

Default endpoint is the local/bare-metal Docling service documented in:
  scripts/extractors/api guide references/docling-api-guide.md

Usage:
  python3 docling_extract.py <input_file> [output]
  python3 docling_extract.py <input_file> --output-dir <dir>
  python3 docling_extract.py <input_file> out.md --async
  python3 docling_extract.py <input_file> out.json --json --async

If positional output is an existing directory, standard extractor outputs are used:
  {basename}_docling.md and {basename}_docling.json
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from common import fix_ligatures, load_config, sanitize_filename

DEFAULT_BASE_URL = "https://docling.infinitek.pe"
DEFAULT_TIMEOUT = 600
DEFAULT_ASYNC_POLL_INTERVAL = 5
DEFAULT_ASYNC_MAX_WAIT = 900
DEFAULT_SUFFIX = "docling"


class DoclingClient:
    def __init__(
        self,
        base_url: str,
        timeout: int = DEFAULT_TIMEOUT,
        poll_interval: int = DEFAULT_ASYNC_POLL_INTERVAL,
        max_wait: int = DEFAULT_ASYNC_MAX_WAIT,
        verify_tls: bool = True,
        extractor_name: str = DEFAULT_SUFFIX,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_wait = max_wait
        self.verify_tls = verify_tls
        self.extractor_name = extractor_name

    def ssl_context(self):
        if self.base_url.startswith("http://"):
            return None
        if self.verify_tls:
            return ssl.create_default_context()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def request_json(self, req: urllib.request.Request, timeout: int) -> Any:
        try:
            with urllib.request.urlopen(req, context=self.ssl_context(), timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:1000]
            raise RuntimeError(f"HTTP {e.code} from Docling API: {body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Docling API request failed: {e}") from e

    def build_multipart(self, file_path: str, fields: list[tuple[str, str]]) -> tuple[bytes, str]:
        filename = os.path.basename(file_path)
        boundary = f"docling_boundary_{os.getpid()}_{int(time.time())}"

        parts: list[bytes] = []
        for name, value in fields:
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode()
            )

        with open(file_path, "rb") as f:
            file_bytes = f.read()

        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
        )
        parts.append(file_bytes)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        return b"".join(parts), boundary

    def submit(self, file_path: str, fields: list[tuple[str, str]], async_mode: bool) -> Any:
        body, boundary = self.build_multipart(file_path, fields)
        endpoint = "/v1/convert/file/async" if async_mode else "/v1/convert/file"
        req = urllib.request.Request(
            f"{self.base_url}{endpoint}",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        return self.request_json(req, timeout=60 if async_mode else self.timeout)

    def convert_sync(self, file_path: str, fields: list[tuple[str, str]]) -> Any:
        return self.submit(file_path, fields, async_mode=False)

    def convert_async(self, file_path: str, fields: list[tuple[str, str]]) -> Any:
        submit = self.submit(file_path, fields, async_mode=True)
        task_id = submit.get("task_id")
        if not task_id:
            raise RuntimeError(f"No task_id in Docling async response: {submit}")

        print(f"  Task submitted: {task_id}", file=sys.stderr)
        elapsed = 0
        last_status = None
        while elapsed < self.max_wait:
            time.sleep(self.poll_interval)
            elapsed += self.poll_interval

            status_url = f"{self.base_url}/v1/status/poll/{task_id}"
            status_data = self.request_json(urllib.request.Request(status_url), timeout=30)
            status = status_data.get("task_status") or status_data.get("status") or "unknown"
            if status != last_status or elapsed % max(self.poll_interval, 30) == 0:
                print(f"  [{elapsed}s] Status: {status}", file=sys.stderr)
                last_status = status

            if status in ("success", "completed"):
                result_url = f"{self.base_url}/v1/result/{task_id}"
                return self.request_json(urllib.request.Request(result_url), timeout=120)
            if status == "failure":
                raise RuntimeError(f"Docling conversion failed: {status_data}")
            if isinstance(status_data, dict) and status_data.get("detail") == "Task not found.":
                raise RuntimeError("Docling async task not found; service likely restarted/preempted. Re-submit the document, preferably by page ranges.")

        raise TimeoutError(f"Conversion did not complete in {self.max_wait}s")

    def health(self) -> Any:
        return self.request_json(urllib.request.Request(f"{self.base_url}/health"), timeout=min(self.timeout, 180))

    def version(self) -> Any:
        return self.request_json(urllib.request.Request(f"{self.base_url}/version"), timeout=min(self.timeout, 180))


def config_section(section: str) -> configparser.SectionProxy | None:
    try:
        cfg = load_config()
    except FileNotFoundError:
        return None
    return cfg[section] if cfg.has_section(section) else None


def bool_from_config(section: configparser.SectionProxy | None, key: str, default: bool) -> bool:
    if section is None or key not in section:
        return default
    return section.getboolean(key)


def int_from_config(section: configparser.SectionProxy | None, key: str, default: int) -> int:
    if section is None or key not in section:
        return default
    return section.getint(key)


def build_fields(args: argparse.Namespace) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = [
        ("image_export_mode", args.image_export_mode),
        ("include_images", "true" if args.images else "false"),
    ]
    optional = {
        "do_ocr": args.do_ocr,
        "force_ocr": args.force_ocr,
        "ocr_lang": args.ocr_lang,
        "document_timeout": args.document_timeout,
        "table_mode": args.table_mode,
    }
    for key, value in optional.items():
        if value is not None and value != "":
            fields.append((key, str(value).lower() if isinstance(value, bool) else str(value)))

    if args.page_range:
        # Docling Serve expects page_range as a list field, i.e. two repeated
        # multipart fields: page_range=START and page_range=END. Some docs show
        # "1,50" in curl shorthand, but current API validates it as List[int].
        parts = [p.strip() for p in str(args.page_range).replace("-", ",").split(",") if p.strip()]
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise SystemExit("--page-range must be START,END or START-END, e.g. 1,50")
        fields.append(("page_range", parts[0]))
        fields.append(("page_range", parts[1]))
    return fields


def extract_markdown(result: Any) -> tuple[str, str]:
    """Extract markdown content from Docling API response."""
    if not result:
        raise ValueError(f"Unexpected response format: {json.dumps(result)[:500]}")

    if isinstance(result, list):
        if not result:
            raise ValueError(f"Unexpected empty response: {result}")
        doc = result[0]
    elif isinstance(result, dict):
        doc = result
    else:
        raise ValueError(f"Unexpected response format: {json.dumps(result)[:500]}")

    status = doc.get("status") or doc.get("task_status") or "success"
    if status not in ("success", "completed"):
        raise RuntimeError(f"Conversion status: {status}, errors: {doc.get('errors', [])}, message: {doc.get('error_message')}")

    document = doc.get("document") or {}
    md_content = document.get("md_content")
    if not md_content:
        raise ValueError("No document.md_content in Docling response")

    title = document.get("title") or ""
    return fix_ligatures(md_content), title


def resolve_outputs(input_path: str, output: str | None, output_dir: str | None, suffix: str, raw_json: bool) -> tuple[str | None, str | None]:
    """Return (md_or_json_output, sidecar_json_output)."""
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        base = sanitize_filename(input_path)
        return os.path.join(output_dir, f"{base}_{suffix}.{'json' if raw_json else 'md'}"), os.path.join(output_dir, f"{base}_{suffix}.json")

    if output and os.path.isdir(output):
        base = sanitize_filename(input_path)
        return os.path.join(output, f"{base}_{suffix}.{'json' if raw_json else 'md'}"), os.path.join(output, f"{base}_{suffix}.json")

    if output:
        return output, None

    return None, None


def build_parser(
    default_base_url: str = DEFAULT_BASE_URL,
    default_suffix: str = DEFAULT_SUFFIX,
    default_async: bool = False,
    config_name: str = "docling",
) -> argparse.ArgumentParser:
    section = config_section(config_name)
    env_base_url = os.environ.get(f"{config_name.upper()}_BASE_URL") or os.environ.get("DOCLING_BASE_URL")
    parser = argparse.ArgumentParser(description="Convert documents via Docling Serve API")
    parser.add_argument("input", nargs="?", help="Input file (PDF, DOCX, PPTX, XLSX, etc.)")
    parser.add_argument("output", nargs="?", help="Output file, or output directory if it already exists (default: stdout)")
    parser.add_argument("--output-dir", help="Directory for standard extractor outputs")
    parser.add_argument("--base-url", default=env_base_url or (section.get("base_url", fallback=default_base_url) if section else default_base_url), help="Docling Serve base URL")
    parser.add_argument("--suffix", default=default_suffix, help="Output suffix for --output-dir mode")
    parser.add_argument("--async", dest="use_async", action="store_true", default=default_async, help="Use async conversion")
    parser.add_argument("--sync", dest="use_async", action="store_false", help="Force sync conversion")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of extracting markdown")
    parser.add_argument("--images", action="store_true", default=bool_from_config(section, "include_images", False), help="Include/export images in output")
    parser.add_argument("--image-export-mode", default=section.get("image_export_mode", fallback="placeholder") if section else "placeholder", choices=["placeholder", "embedded", "referenced"], help="Docling image_export_mode")
    parser.add_argument("--do-ocr", choices=["true", "false"], default=section.get("do_ocr", fallback=None) if section else None)
    parser.add_argument("--force-ocr", choices=["true", "false"], default=section.get("force_ocr", fallback=None) if section else None)
    parser.add_argument("--ocr-lang", default=section.get("ocr_lang", fallback=None) if section else None, help="OCR languages, e.g. es,en")
    parser.add_argument("--page-range", default=None, help="Page range, e.g. 1,50")
    parser.add_argument("--document-timeout", default=section.get("document_timeout", fallback=None) if section else None, help="Docling internal timeout in seconds")
    parser.add_argument("--table-mode", choices=["accurate", "fast"], default=section.get("table_mode", fallback=None) if section else None)
    parser.add_argument("--timeout", type=int, default=int_from_config(section, "timeout", DEFAULT_TIMEOUT), help="Sync HTTP timeout seconds")
    parser.add_argument("--poll-interval", type=int, default=int_from_config(section, "poll_interval", DEFAULT_ASYNC_POLL_INTERVAL), help="Async poll interval seconds")
    parser.add_argument("--max-wait", type=int, default=int_from_config(section, "max_wait", DEFAULT_ASYNC_MAX_WAIT), help="Async max wait seconds")
    parser.add_argument("--insecure", action="store_true", default=bool_from_config(section, "insecure", False), help="Disable TLS verification")
    parser.add_argument("--health", action="store_true", help="Print /health and exit")
    parser.add_argument("--version", action="store_true", help="Print /version and exit")
    return parser


def run(
    default_base_url: str = DEFAULT_BASE_URL,
    default_suffix: str = DEFAULT_SUFFIX,
    default_async: bool = False,
    config_name: str = "docling",
) -> int:
    parser = build_parser(
        default_base_url=default_base_url,
        default_suffix=default_suffix,
        default_async=default_async,
        config_name=config_name,
    )
    args = parser.parse_args()

    client = DoclingClient(
        base_url=args.base_url,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
        max_wait=args.max_wait,
        verify_tls=not args.insecure,
        extractor_name=args.suffix,
    )

    if args.health:
        print(json.dumps(client.health(), indent=2, ensure_ascii=False))
        return 0
    if args.version:
        print(json.dumps(client.version(), indent=2, ensure_ascii=False))
        return 0

    if not args.input:
        print("Error: input file is required unless using --health or --version", file=sys.stderr)
        return 1

    if not os.path.exists(args.input):
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        return 1

    file_size = os.path.getsize(args.input)
    print(f"Converting: {args.input} ({file_size:,} bytes) via {client.base_url}", file=sys.stderr)
    fields = build_fields(args)
    print(f"  Fields: {fields}", file=sys.stderr)

    t0 = time.time()
    result = client.convert_async(args.input, fields) if args.use_async else client.convert_sync(args.input, fields)
    elapsed = time.time() - t0

    if args.json:
        output = json.dumps(result, indent=2, ensure_ascii=False)
        md_content = ""
    else:
        md_content, title = extract_markdown(result)
        output = f"# {title}\n\n{md_content}" if title else md_content

    out_path, sidecar_path = resolve_outputs(args.input, args.output, args.output_dir, args.suffix, args.json)
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Output written: {out_path} ({len(output):,} chars)", file=sys.stderr)
    else:
        print(output)

    if sidecar_path and not args.json:
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "source": args.input,
                    "extractor": args.suffix,
                    "base_url": client.base_url,
                    "async": args.use_async,
                    "fields": fields,
                    "elapsed_seconds": round(elapsed, 1),
                    "markdown_length": len(md_content),
                    "content": md_content,
                    "raw_result": result,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(f"Sidecar written: {sidecar_path}", file=sys.stderr)

    if not args.json:
        lines = output.count("\n") + 1
        print(f"Stats: {len(output):,} chars, {lines:,} lines, {elapsed:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
