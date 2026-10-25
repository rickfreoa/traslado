from __future__ import annotations

import csv
import io
import os
import re
from pathlib import Path
import shutil
import tempfile

from .model import Block, Options
from .runtime import managed_tesseract, managed_tessdata_for
from .safety import run_bounded


def find_tesseract(explicit: str | None = None) -> str | None:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ValueError("Informe o caminho absoluto do Tesseract")
        return str(path)
    managed = managed_tesseract()
    if managed:
        return managed
    # which() on Windows may prepend CWD even with an explicit PATH.
    current = Path.cwd().resolve()
    for entry in os.get_exec_path():
        directory = Path(entry.strip('"'))
        if not directory.is_absolute() or directory.resolve() == current:
            continue
        found = shutil.which(str(directory / "tesseract"))
        if found:
            return str(Path(found).resolve())
    for variable in ("LOCALAPPDATA", "ProgramFiles"):
        root = os.environ.get(variable)
        if root:
            root = Path(root).expanduser()
            if not root.is_absolute():
                continue
            for relative in ("Programs/Tesseract-OCR/tesseract.exe", "Tesseract-OCR/tesseract.exe"):
                candidate = root / relative
                if candidate.is_file():
                    return str(candidate.resolve())
    return None


def recognize(image: Path, workspace: Path, options: Options) -> tuple[Block, list[str]]:
    exe = find_tesseract(options.tesseract)
    if not exe:
        raise RuntimeError("OCR necessário: instale Tesseract e os idiomas solicitados; consulte doctor")
    with tempfile.TemporaryDirectory(prefix="ocr-", dir=workspace) as raw:
        temp = Path(raw)
        base = temp / "result"
        data = managed_tessdata_for(exe, options.tessdata)
        languages = _validate_languages(exe, data, options, temp)
        argv = [exe, str(image), str(base), "-l", options.language, "--psm", "3"]
        if data:
            argv.extend(["--tessdata-dir", data])
        # Direct flags also work with a traineddata-only directory (no configs/).
        argv.extend(["-c", "tessedit_create_txt=1", "-c", "tessedit_create_tsv=1"])
        log = temp / "stderr.log"
        code = run_bounded(argv, temp, options.ocr_timeout, log)
        diagnostics = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        if code != 0 or re.search(r"failed loading language|error opening data file|couldn't load any languages|lstm requested, but not present", diagnostics, re.I):
            raise RuntimeError("Tesseract falhou ao carregar/executar os idiomas solicitados (" + options.language + "); confira tessdata e doctor. " + diagnostics[-1500:])
        text_file, tsv_file = base.with_suffix(".txt"), base.with_suffix(".tsv")
        if not text_file.is_file() or not tsv_file.is_file():
            raise RuntimeError("OCR não produziu o contrato esperado")
        if text_file.stat().st_size + tsv_file.stat().st_size > options.max_output_bytes:
            raise ValueError("OCR excede o limite de saída")
        text = text_file.read_text(encoding="utf-8").strip()
        confidences = []
        for row in csv.DictReader(io.StringIO(tsv_file.read_text(encoding="utf-8")), delimiter="\t"):
            try:
                value = float(row["conf"])
                if value >= 0 and row.get("text", "").strip():
                    confidences.append(value / 100)
            except (KeyError, ValueError):
                continue
        confidence = sum(confidences) / len(confidences) if confidences else None
        warnings = ["ocr_empty"] if not text else []
        if confidence is not None and confidence < .8:
            warnings.append("ocr_low_confidence")
        return Block("text", text, engine="tesseract", confidence=confidence,
                     details={"language": options.language, "requested_languages": options.language.split("+"),
                              "available_languages": languages, "language_validation": "listed_and_no_load_error",
                              "confidence_kind": "mean_word_score_not_accuracy"}), warnings


def _validate_languages(exe: str, data: str | None, options: Options, temp: Path) -> list[str]:
    """Use the same executable and tessdata as OCR; never silently drop languages."""
    argv = [exe]
    if data:
        argv += ["--tessdata-dir", data]
    log = temp / "languages.log"
    code = run_bounded(argv + ["--list-langs"], temp, options.ocr_timeout, log)
    if code != 0 or not log.is_file():
        raise RuntimeError("Não foi possível validar os idiomas do Tesseract; confira doctor")
    available = sorted({line.strip() for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
                        if re.fullmatch(r"[a-zA-Z_]+", line.strip())})
    missing = sorted(set(options.language.split("+")) - set(available))
    if missing:
        raise RuntimeError("Idiomas OCR ausentes: " + ", ".join(missing) + "; instale os modelos em tessdata ou selecione idiomas disponíveis: " + ", ".join(available))
    return available
