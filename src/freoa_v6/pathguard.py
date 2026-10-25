"""Windows cloud placeholders are ordinary files; name-surrogate links are refused."""
from pathlib import Path


def unsafe_link(path: Path) -> bool:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        return True
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not getattr(info, "st_file_attributes", 0) & 0x400:
        return False
    # Microsoft IO_REPARSE_TAG_CLOUD and CLOUD_1..F only; unknown tags fail closed.
    tag = getattr(info, "st_reparse_tag", 0)
    return (tag & 0xFFFF0FFF) != 0x9000001A
