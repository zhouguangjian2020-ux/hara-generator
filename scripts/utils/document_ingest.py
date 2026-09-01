"""输入文档格式识别与受控转换。

`.docx` 直接交给 python-docx；旧 OLE `.doc` 通过可插拔本地转换器转换为临时
DOCX。转换失败时抛出结构化异常，由 stage_parse 生成 Agent 回退请求，而不是
让下游把二进制文件当成可解析文本。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import time


OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


class DocumentConversionError(ValueError):
    def __init__(self, message: str, *, source_info: dict | None = None, attempts: list[dict] | None = None):
        super().__init__(message)
        self.source_info = source_info or {}
        self.attempts = attempts or []


@dataclass
class PreparedDocument:
    source_path: Path
    parse_path: Path
    source_info: dict


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_document_format(path: Path) -> str:
    with path.open("rb") as stream:
        header = stream.read(8)
    if any(header.startswith(signature) for signature in ZIP_SIGNATURES):
        return "docx"
    if header.startswith(OLE_SIGNATURE):
        return "doc_ole"
    return "unknown"


def source_document_info(path: Path) -> dict:
    detected = detect_document_format(path)
    return {
        "original_path": str(path.resolve()),
        "original_name": path.name,
        "original_extension": path.suffix.lower(),
        "detected_format": detected,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "conversion": {
            "required": detected == "doc_ole",
            "status": "not_required" if detected == "docx" else "pending",
            "converter": None,
            "elapsed_ms": 0,
        },
    }


def _word_vbs_text() -> str:
    return r'''Option Explicit
Dim src, dst, word, doc
src = WScript.Arguments(0)
dst = WScript.Arguments(1)
On Error Resume Next
Set word = CreateObject("Word.Application")
If Err.Number <> 0 Then
  WScript.Echo "WORD_CREATE_ERROR " & Err.Number & " " & Err.Description
  WScript.Quit 2
End If
word.Visible = False
word.DisplayAlerts = 0
Err.Clear
Set doc = word.Documents.Open(src, False, True, False, "", "", False, "", "", 0, 0, False, True)
If Err.Number <> 0 Then
  WScript.Echo "WORD_OPEN_ERROR " & Err.Number & " " & Err.Description
  word.Quit
  WScript.Quit 3
End If
Err.Clear
doc.SaveAs2 dst, 16
If Err.Number <> 0 Then
  WScript.Echo "WORD_SAVE_ERROR " & Err.Number & " " & Err.Description
  doc.Close False
  word.Quit
  WScript.Quit 4
End If
doc.Close False
word.Quit
WScript.Echo "OK"
'''


def _run_word_converter(source: Path, target: Path, work_dir: Path) -> dict:
    if os.name != "nt" or not shutil.which("cscript.exe"):
        return {"converter": "word_com", "status": "unavailable", "message": "cscript.exe unavailable"}
    # 复制为纯 ASCII 临时名，避免旧版 Word 转换器对非 ASCII 路径处理不一致。
    ascii_source = work_dir / "source.doc"
    shutil.copy2(source, ascii_source)
    script = work_dir / "convert_doc.vbs"
    script.write_text(_word_vbs_text(), encoding="ascii")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    started = time.perf_counter()
    result = subprocess.run(
        ["cscript.exe", "//Nologo", str(script), str(ascii_source), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        creationflags=creationflags,
        check=False,
    )
    elapsed = round((time.perf_counter() - started) * 1000)
    ok = result.returncode == 0 and target.exists() and target.stat().st_size > 0
    return {
        "converter": "word_com",
        "status": "success" if ok else "failed",
        "exit_code": result.returncode,
        "elapsed_ms": elapsed,
        "stdout": result.stdout.strip()[-1000:],
        "stderr": result.stderr.strip()[-1000:],
    }


def _run_libreoffice_converter(source: Path, target: Path, work_dir: Path) -> dict:
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if not executable:
        return {"converter": "libreoffice", "status": "unavailable", "message": "executable unavailable"}
    started = time.perf_counter()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [executable, "--headless", "--convert-to", "docx", "--outdir", str(work_dir), str(source)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        creationflags=creationflags,
        check=False,
    )
    generated = work_dir / f"{source.stem}.docx"
    if generated.exists() and generated != target:
        shutil.move(str(generated), str(target))
    elapsed = round((time.perf_counter() - started) * 1000)
    ok = result.returncode == 0 and target.exists() and target.stat().st_size > 0
    return {
        "converter": "libreoffice",
        "status": "success" if ok else "failed",
        "exit_code": result.returncode,
        "elapsed_ms": elapsed,
        "stdout": result.stdout.strip()[-1000:],
        "stderr": result.stderr.strip()[-1000:],
    }


@contextmanager
def prepare_document(path: Path):
    source = Path(path).resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    info = source_document_info(source)
    detected = info["detected_format"]
    if detected == "docx":
        yield PreparedDocument(source, source, info)
        return
    if detected != "doc_ole":
        raise DocumentConversionError(
            f"不支持的文档格式: {source}", source_info=info,
            attempts=[{"converter": "none", "status": "unsupported_signature"}],
        )

    attempts = []
    with tempfile.TemporaryDirectory(prefix="hara_doc_convert_") as temp_name:
        work_dir = Path(temp_name)
        target = work_dir / "converted.docx"
        for converter in (_run_word_converter, _run_libreoffice_converter):
            try:
                attempt = converter(source, target, work_dir)
            except Exception as error:  # 转换器异常必须转成可记录诊断
                attempt = {
                    "converter": converter.__name__,
                    "status": "failed",
                    "message": str(error),
                }
            attempts.append(attempt)
            if attempt.get("status") == "success":
                info["conversion"] = {
                    "required": True,
                    "status": "success",
                    "converter": attempt.get("converter"),
                    "elapsed_ms": attempt.get("elapsed_ms", 0),
                    "attempts": attempts,
                }
                yield PreparedDocument(source, target, info)
                return
        info["conversion"] = {
            "required": True,
            "status": "failed",
            "converter": None,
            "elapsed_ms": sum(int(item.get("elapsed_ms", 0) or 0) for item in attempts),
            "attempts": attempts,
        }
        raise DocumentConversionError(
            f"旧 .doc 转换失败: {source}", source_info=info, attempts=attempts,
        )

