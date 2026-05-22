"""Preparación de documentos descargados antes del análisis fast-path."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

_LIBREOFFICE_LOCK = threading.Lock()

ARCHIVE_SUFFIXES = {".zip", ".rar"}
UPLOAD_SUFFIXES = {".pdf", ".docx", ".doc", ".xlsx", ".xls"}
CONVERTIBLE_SUFFIXES = {".docx", ".doc", ".xlsx", ".xls"}
ANALYZABLE_SUFFIXES = UPLOAD_SUFFIXES
GEMINI_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def validate_gemini_upload_size(path: Path) -> None:
    size = path.stat().st_size
    if size <= GEMINI_MAX_UPLOAD_BYTES:
        return
    size_mb = size / (1024 * 1024)
    limit_mb = GEMINI_MAX_UPLOAD_BYTES / (1024 * 1024)
    raise RuntimeError(
        f"{path.name} pesa {size_mb:.1f} MB; Gemini acepta hasta ~{limit_mb:.0f} MB por archivo. "
        "Elige un archivo más pequeño (p. ej. la versión DOCX editable en lugar del PDF firmado)."
    )


def normalize_doc_name(name: str) -> str:
    text = name.lower()
    text = re.sub(r"[+\-_]+", " ", text)
    text = re.sub(r"[^a-z0-9áéíóúñü ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def score_bases_candidate(path: Path) -> int:
    normalized = normalize_doc_name(path.stem)
    if "bases" not in normalized:
        return -1
    score = 20
    if any(token in normalized for token in ("integrada", "integradas", "integrado", "integrados")):
        score += 200
    if path.suffix.lower() == ".pdf":
        score += 10
    elif path.suffix.lower() in {".docx", ".doc"}:
        score += 5
    if any(token in normalized for token in ("anexo", "formato", "modelo", "carta")):
        score -= 5
    return score


def extract_archives(documents_dir: Path) -> Path:
    """Descomprime ZIP/RAR en documents_dir/_extracted/."""
    extract_root = documents_dir / "_extracted"
    extract_root.mkdir(parents=True, exist_ok=True)

    for archive in sorted(documents_dir.iterdir()):
        if not archive.is_file():
            continue
        suffix = archive.suffix.lower()
        if suffix not in ARCHIVE_SUFFIXES:
            continue
        dest = extract_root / archive.stem
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)

        if suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(dest)
            logger.info("Extraído ZIP %s → %s", archive.name, dest)
            continue

        _extract_rar(archive, dest)
        logger.info("Extraído RAR %s → %s", archive.name, dest)

    return extract_root


def _extract_rar(archive: Path, dest: Path) -> None:
    for cmd in (
        ["7z", "x", f"-o{dest}", str(archive), "-y"],
        ["unar", "-o", str(dest), str(archive)],
        ["unrar", "x", "-o+", str(archive), str(dest)],
    ):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except FileNotFoundError:
            continue
        if proc.returncode == 0:
            return
        logger.warning("Fallo %s: %s", cmd[0], (proc.stderr or proc.stdout)[-500:])

    raise RuntimeError(
        f"No se pudo descomprimir {archive.name}. Instala p7zip-full (7z) en el contenedor."
    )


def iter_candidate_files(*roots: Path) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.name == "manifest.json":
                continue
            if path.suffix.lower() not in UPLOAD_SUFFIXES | ARCHIVE_SUFFIXES:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(path)
    return files


def resolve_selected_documents(
    documents_dir: Path, selected_rel_paths: list[str]
) -> list[Path]:
    """Resuelve rutas relativas a documentos/ con validación anti path traversal."""
    resolved: list[Path] = []
    base = documents_dir.resolve()
    for rel in selected_rel_paths:
        rel = rel.strip().replace("\\", "/").lstrip("/")
        if not rel or rel == "manifest.json":
            continue
        candidate = (documents_dir / rel).resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise RuntimeError(f"Ruta de documento inválida: {rel}") from exc
        if not candidate.is_file():
            raise RuntimeError(f"Archivo no encontrado: {rel}")
        suffix = candidate.suffix.lower()
        if suffix in ARCHIVE_SUFFIXES:
            raise RuntimeError(f"No se puede analizar un archivo comprimido: {rel}")
        if suffix not in ANALYZABLE_SUFFIXES:
            raise RuntimeError(f"Formato no soportado para análisis: {rel}")
        resolved.append(candidate)
    if not resolved:
        raise RuntimeError("Selecciona al menos un archivo analizable.")
    return resolved


def select_bases_document(documents_dir: Path) -> Path:
    extract_root = extract_archives(documents_dir)
    candidates = iter_candidate_files(documents_dir, extract_root)

    scored: list[tuple[int, Path]] = []
    for path in candidates:
        if path.suffix.lower() in ARCHIVE_SUFFIXES:
            continue
        score = score_bases_candidate(path)
        if score >= 0:
            scored.append((score, path))

    if not scored:
        names = ", ".join(p.name for p in candidates[:12]) or "(vacío)"
        raise RuntimeError(
            f"No se encontró documento de bases entre los archivos descargados. Vistos: {names}"
        )

    scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
    best_score, best_path = scored[0]
    logger.info(
        "Bases seleccionadas: %s (score=%s, candidatos=%s)",
        best_path.name,
        best_score,
        len(scored),
    )
    return best_path


def _convert_to_pdf(source: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    with _LIBREOFFICE_LOCK:
        proc = subprocess.run(
            [
                "soffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"LibreOffice no convirtió {source.name}: {(proc.stderr or proc.stdout)[-1000:]}"
        )
    pdf = out_dir / f"{source.stem}.pdf"
    if not pdf.exists():
        pdfs = list(out_dir.glob("*.pdf"))
        if not pdfs:
            raise RuntimeError(f"Conversión a PDF no produjo salida para {source.name}")
        pdf = pdfs[0]
    return pdf


def ensure_uploadable_document(source: Path, workspace: Path) -> Path:
    """PDF listo para subir a Gemini; convierte DOCX/XLSX→PDF si hace falta."""
    workspace.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        return source

    if suffix in CONVERTIBLE_SUFFIXES:
        out_dir = workspace / "converted"
        return _convert_to_pdf(source, out_dir)

    raise RuntimeError(f"Formato no soportado para análisis fast-path: {source.name}")


def merge_pdfs(sources: list[Path], output: Path) -> Path:
    """Concatena PDFs en orden."""
    try:
        from pypdf import PdfWriter
    except ImportError as exc:
        raise RuntimeError("Instala pypdf para combinar varios PDFs") from exc

    writer = PdfWriter()
    for path in sources:
        writer.append(str(path))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as fh:
        writer.write(fh)
    logger.info("PDFs combinados (%s) → %s", len(sources), output.name)
    return output


def prepare_documents_for_llm(sources: list[Path], workspace: Path) -> list[Path]:
    """Convierte cada fuente a PDF listo para Gemini."""
    workspace.mkdir(parents=True, exist_ok=True)
    pdfs: list[Path] = []
    for index, source in enumerate(sources):
        sub = workspace / f"doc_{index:02d}"
        pdfs.append(ensure_uploadable_document(source, sub))
    return pdfs
