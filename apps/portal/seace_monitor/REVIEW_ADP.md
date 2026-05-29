# ADP Portal Input Source — Code Review

**Branch:** `adp-input` | **Commit:** `44b162f` | **Files:** 11 (+1223 lines)  
**Reviewed:** 2026-05-29

---

## Summary

The ADP Portal implementation is a well-structured, clean addition that follows the SEACE architecture patterns closely. The parser correctly handles the portal HTML structure, the scanner/watcher/downloade separation mirrors the existing design, and the worker integration is clean. The code is production-ready with a few medium-priority improvements recommended before merge.

**Verdict:** ✅ Approve with 2 medium issues to address. No critical blockers.

---

## Critical Issues

None found.

---

## Medium Issues

### M1. `AdpClient` never closed in `AdpScanner` — resource leak

**File:** `adp_scanner.py:77`  
**Severity:** Medium

The `AdpScanner.__init__` creates an `AdpClient` (`self.client = AdpClient(...)`) but never calls `close()`. The worker creates a new `AdpScanner` on each scan cycle, so the `requests.Session` and its underlying TCP connections accumulate until garbage collection.

Compare with `refresh_adp_watchlist` in `adp_watchlist.py:71` which correctly calls `client.close()`.

**Fix:** Add a `close()` method or use `self.client` as a context-dependent resource:

```python
# adp_scanner.py — in run_once(), after the loop:
def run_once(self) -> int:
    try:
        entity = _ensure_adp_entity(self.session)
        new_count = 0
        for work_id in ALL_WORK_IDS:
            ...
        return new_count
    finally:
        self.client.close()
```

Or better, create the client inside `run_once()` to avoid keeping it alive between cycles:

```python
def run_once(self) -> int:
    client = AdpClient(http_proxy=self.config.http_proxy)
    try:
        ...
    finally:
        client.close()
```

### M2. Watchlist loses `archivo` paths when document fingerprint changes

**File:** `adp_watchlist.py:139-149`  
**Severity:** Medium

When the ADP watchlist detects a document change (even a minor one like a vigencia date update), it reconstructs `new_docs` from the parser output which has `archivo=""` for all documents. `download_adp_documents()` recovers some paths by checking disk, but only for files that happen to exist at the computed filename.

The SEACE watchlist handles this case with `_merge_parsed_docs_with_storage()` which explicitly preserves `archivo` and `nombre` from the previous `documentos_json`. The ADP watchlist doesn't have equivalent logic.

**Impact:** If a user manually renames a file on disk or if `sanitize_download_filename` produces a different name on re-run, the `archivo` field in `documentos_json` will point to the wrong file or be empty after a watchlist refresh.

**Fix:** Add a merge step before downloading, similar to SEACE:

```python
# adp_watchlist.py — in _refresh_process, before download_adp_documents:
stored_by_namefile = {}
if proc.documentos_json:
    try:
        for d in json.loads(proc.documentos_json):
            nf = d.get("name_file", "")
            if nf and d.get("archivo"):
                stored_by_namefile[nf] = d.get("archivo")
    except json.JSONDecodeError:
        pass

for doc in new_docs:
    existing = stored_by_namefile.get(doc.get("name_file", ""))
    if existing:
        doc["archivo"] = existing
```

---

## Low Issues

### L1. `content_hash` excludes `description` from fingerprint

**File:** `adp_parser.py:52-53`, `adp_parser.py:174-179`  
**Severity:** Low

```python
def _fingerprint_payload(process: AdpProcess) -> str:
    parts: list[str] = [process.code, str(process.work_id)]
    for doc in sorted(process.documents, key=lambda d: d.name_file):
        parts.append(...)
    return "|".join(parts)
```

The hash includes `code`, `work_id`, and documents but excludes `description`. If the portal updates only the description text (e.g., fixing a typo in the excerpt), the scanner won't detect the change and the process won't be updated.

In SEACE, `content_hash` comes from `FichaSeace.content_hash()` which includes the full ficha payload. For ADP, the description is the most meaningful text content.

**Recommendation:** Add `process.description` to the payload if description changes should be tracked.

### L2. `_extract_anio` re-imports `re` and `datetime` inside a static method

**File:** `adp_scanner.py:187-195`  
**Severity:** Low (style)

```python
@staticmethod
def _extract_anio(code: str) -> int:
    import re
    m = re.search(r"(20\d{2})", code)
    ...
    from datetime import datetime
    return datetime.now().year
```

Both `re` and `datetime` are already imported at module level. Re-importing inside the method adds unnecessary overhead on every call. If the static method needs to be movable, move it to module level.

### L3. No ADP-specific auto-reject rule filtering

**File:** `adp_scanner.py:167-172`  
**Severity:** Low

Auto-reject rules apply to ALL processes regardless of `source`. ADP processes with `source="adp_portal"` will be evaluated against rules designed for SEACE (`objeto:servicio`, etc.). Since ADP processes also have `objeto` set (to the description), a broad SEACE rule could incorrectly auto-reject an ADP process.

Currently mitigated by: the synthetic entity name `"Aeropuertos del Perú"` allows targeting rules with `entidad:nombre`. But a rule without entity filter could match both sources.

**Recommendation:** Document this behavior and consider adding `source:adp_portal` field support to auto-reject rules.

### L4. Watchlist: no `watch_changelog_json` support

**File:** `adp_watchlist.py:165-168`  
**Severity:** Low

The ADP watchlist sets `watch_documentos_prev_json` (one level of rollback) but doesn't use `watch_changelog_json` for cumulative changelog entries like SEACE does (`append_watchlist_changelog`). This means there's no history of changes beyond the most recent one.

### L5. Tests don't cover watchlist or download edge cases

**Files:** `test_adp_parser.py`, `test_adp_scanner.py`  
**Severity:** Low

The existing tests are good for parser and basic scanner functionality. Missing coverage:
- `download_adp_documents` with existing/partial files on disk
- `refresh_adp_watchlist` with changed/unchanged documents
- `parse_adp_html` with malformed HTML (missing `a` tags, broken `href`)
- `_parse_download_link` with missing `name_file` in query string
- Worker loop integration (ADP + SEACE concurrent schedule)

### L6. No web UI integration for ADP processes

**Files:** `apps/portal/seace_monitor/web/` (no changes)  
**Severity:** Low (intentional — separate task)

The ingest registry registers ADP (`ingest/__init__.py`, `ingest/adp.py`), and processes are stored in the database, but the web UI templates don't filter or display ADP processes differently. ADP processes will appear in the default views mixed with SEACE processes, using the same entity (ADP-PORTAL). This is functional but may need UI differentiation in a follow-up.

---

## Positive Observations

1. **Clean separation of concerns:** Parser, client, scanner, downloader, watchlist — each has a single responsibility matching the SEACE pattern.

2. **Immutable dataclasses:** `AdpProcess` and `AdpDocument` use `frozen=True` where appropriate. Good for hash stability.

3. **Robust fingerprinting:** `content_hash()` and `_docs_fingerprint()` provide deterministic change detection. Document order is normalized via sorting.

4. **Retry with backoff:** `AdpClient.download_document()` has 3 retries with exponential backoff (1s, 2s, 4s). Consistent with existing downloader patterns.

5. **Safe file writes:** `.part` temporary file + `replace()` atomic rename prevents partial/corrupt files on disk.

6. **Proper proxy support:** All HTTP calls use `requests_proxies(http_proxy)` matching the existing infrastructure.

7. **Worker integration is clean:** Separate poll intervals for ADP vs SEACE, correct wake-up calculation using `min()` of all next-fire times.

8. **Sanitized filenames:** `sanitize_download_filename()` from `document_storage.py` prevents path traversal and limits filename length.

9. **Good test coverage for core parser:** Tests cover standard parsing, empty HTML, missing documents, vigencia parsing edge cases, content hash determinism, and real HTML samples.

10. **Config defaults are sensible:** 6h poll interval, enabled by default, separate from SEACE config — allows independent tuning.

11. **Savepoint isolation:** Both scanner and watchlist use `session.begin_nested()` savepoints so a single process failure doesn't roll back the entire scan.

---

## Architecture Fit

The implementation follows the documented architecture in `docs/INPUT_SOURCES.md` and `docs/ARCHITECTURE.md`:

| Concept | SEACE | ADP | Compatible? |
|---------|-------|-----|-------------|
| `source` | `"seace"` | `"adp_portal"` | ✅ |
| `source_ref` | `nid_proceso` | process `code` | ✅ |
| `Entity` | Public entity (RUC) | Synthetic `ADP-PORTAL` | ✅ |
| Scanner | `MultiEntityScanner` | `AdpScanner` | ✅ |
| Watchlist | `refresh_watchlist_processes` | `refresh_adp_watchlist` | ✅ (simplified) |
| Downloader | Alfresco API | Direct HTTP GET | ✅ |
| Ingest adapter | `SEACE_ADAPTER` | `ADP_ADAPTER` | ✅ |
| Worker | Single loop | Parallel in same loop | ✅ |

The ADP `documents_json` format is compatible with downstream consumers: it uses the same keys (`archivo`, `title`, `uuid`) plus ADP-specific ones (`name_file`, `new_name`, `vigencia_desde`, `vigencia_hasta`, `download_url`). The `manifest.json` writing is delegated to `write_manifest()` from `document_storage.py`.

---

## Performance Assessment

- **1 MB HTML parsing:** BeautifulSoup parsing of ~1 MB HTML × 4 categories = ~4 MB per scan. Acceptable with `html.parser` (default) — no lxml dependency needed.
- **689 documents:** Document iteration is O(n) with `select()` calls per process. No nested loops that would cause O(n²).
- **Watchlist:** Fetches all 4 categories regardless of how many watched processes exist. At current scale (4 × 1 MB HTTP requests) this is fine. If the watchlist grows large (hundreds of processes), consider caching or fetching only needed categories.
- **Worker sleep:** Correctly uses `min()` of both scan and watch intervals, so the worker won't oversleep.

---

## Specific Code Suggestions

### S1. `adp_parser.py:85` — `urljoin` base URL has trailing slash assumption

```python
url = urljoin(ADP_BASE_URL + "/", href)
```

`ADP_BASE_URL` is `"https://www.adp.com.pe"` (no trailing slash). Adding `"/"` forces `urljoin` to treat it as a directory base. This works correctly for the current portal but is fragile. Consider:

```python
url = urljoin(ADP_BASE_URL, href)  # urljoin handles this correctly for absolute paths
```

Actually, `urljoin("https://www.adp.com.pe", "/Web/getFile...")` correctly produces `https://www.adp.com.pe/Web/getFile...`. The `+ "/" ` is unnecessary.

### S2. `adp_parser.py:155` — Missing `AdpProcess` attribute access after construction

The `parse_adp_html` function accesses `process.code`, `process.description`, `process.work_id`, etc. — all properly defined. No issue here, just confirming.

### S3. `adp_watchlist.py:62` — `all_parsed` type hint uses `object`

```python
all_parsed: dict[str, tuple[int, object]] = {}
```

`object` is too broad. Consider using `AdpProcess`:

```python
all_parsed: dict[str, tuple[int, AdpProcess]] = {}
```

(Requires importing `AdpProcess` or using string annotation.)

### S4. `adp_scanner.py:181` — `_upsert_process` returns `bool` but never calls `session.commit()`

The method calls `session.flush()` but not `commit()`. The caller (`run_once`) does `savepoint.commit()` for the savepoint. The top-level commit happens in `worker.py` after the whole cycle. This is consistent with SEACE's pattern and is correct — `flush()` is sufficient within a savepoint.

---

## Test Coverage Summary

| Component | Tests | Gaps |
|-----------|-------|------|
| `parse_vigencia()` | 5 tests | ✅ Complete |
| `parse_adp_html()` | 9 tests | Missing: malformed HTML, missing `a` tags |
| `content_hash()` | 4 tests | ✅ Complete |
| `_adp_doc_to_dict()` | 1 test | ✅ Minimal but adequate |
| `_adp_process_to_cronograma()` | 2 tests | ✅ |
| `_extract_anio()` | 3 tests | ✅ |
| `_ensure_adp_entity()` | 2 tests | ✅ (mocked) |
| `AdpClient.fetch_category_html()` | 1 test | Missing: HTTP error, timeout |
| Ingest registry | 1 test | ✅ |
| Watchlist logic | 0 tests | Not tested |
| Downloader | 0 tests | Not tested |
| Worker integration | 0 tests | Not tested |

---

## Security Review

| Concern | Status |
|---------|--------|
| URL injection in `download_url` | ✅ `urljoin` with fixed base prevents open redirect |
| Path traversal in filenames | ✅ `sanitize_download_filename` strips directory components |
| Download file overwrite | ✅ `.part` + `replace()` atomic; no race condition |
| SQL injection | ✅ SQLAlchemy ORM with parameterized queries |
| HTML injection in stored data | ✅ Data stored as JSON text, not rendered unsanitized |
| Proxy credentials exposure | ✅ Proxy URL from config, consistent with rest of app |
| Token/session exposure | ✅ No tokens in ADP client (public portal) |

---

## Conclusion

The ADP Portal implementation is solid. The two medium issues (resource leak in scanner, archivo path loss in watchlist) should be fixed before merge but are not blockers. The architecture fit is excellent — the code follows existing patterns and is properly isolated from SEACE logic. Ship it with the fixes above.
