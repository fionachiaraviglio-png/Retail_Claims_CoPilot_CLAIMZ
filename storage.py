"""File upload storage — saves uploads to disk under uploads/{claim_id}/{category}/."""
from pathlib import Path
from fastapi import UploadFile

UPLOADS_DIR = Path("uploads")


def get_dir(claim_id: str, category: str = "") -> Path:
    d = UPLOADS_DIR / claim_id
    if category:
        d = d / category
    d.mkdir(parents=True, exist_ok=True)
    return d


async def save_upload(claim_id: str, file: UploadFile, category: str) -> tuple[Path, bytes]:
    d = get_dir(claim_id, category)
    safe_name = Path(file.filename or "file").name
    dest = d / safe_name
    data = await file.read()
    dest.write_bytes(data)
    return dest, data


def load_bytes(path: Path) -> bytes:
    return path.read_bytes() if path.exists() else b""


def list_files(claim_id: str, category: str = "") -> list[Path]:
    d = UPLOADS_DIR / claim_id
    if category:
        d = d / category
    return list(d.iterdir()) if d.exists() else []
