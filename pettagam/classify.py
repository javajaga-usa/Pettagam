"""Rule-based smart-type classification.

Each file gets (category, subtype, confidence, reason). Files below REVIEW_BELOW are planned
into review/<category>/ instead of being guessed. AI enrichment (later milestone) refines these.
"""

import re
from dataclasses import dataclass

REVIEW_BELOW = 0.6

# Extension aliases: same format, one spelling.
ALIASES = {
    "jpeg": "jpg", "jpe": "jpg", "jfif": "jpg", "tif": "tiff", "heif": "heic",
    "yml": "yaml", "htm": "html", "tgz": "tar", "mpeg": "mpg", "markdown": "md",
}

RAW = {"cr2", "cr3", "nef", "arw", "dng", "orf", "rw2", "raf", "srw", "pef"}
IMAGES = {"jpg", "png", "gif", "webp", "heic", "avif", "bmp", "tiff", "svg", "ico", "psd"} | RAW
VIDEOS = {"mp4", "mov", "mkv", "avi", "webm", "m4v", "3gp", "wmv", "flv", "mpg", "mts", "m2ts"}
AUDIO = {"mp3", "wav", "flac", "aac", "m4a", "ogg", "opus", "wma", "aiff", "amr"}
PRESENTATIONS = {"ppt", "pptx", "key", "odp"}
SPREADSHEETS = {"xls", "xlsx", "csv", "numbers", "ods", "tsv"}
DOCUMENTS = {"pdf", "doc", "docx", "odt", "rtf", "txt", "md", "pages", "epub"} | PRESENTATIONS | SPREADSHEETS
ARCHIVES = {"zip", "tar", "gz", "bz2", "xz", "7z", "rar", "zst", "dmg", "iso"}
CODE = {
    "py", "js", "ts", "tsx", "jsx", "java", "kt", "go", "rs", "c", "h", "cpp", "hpp", "cs", "rb",
    "php", "swift", "sh", "bash", "zsh", "ps1", "sql", "json", "yaml", "toml", "xml", "html",
    "css", "scss", "ipynb", "dart", "lua", "pl", "r", "m", "gradle",
}

JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini", ".localized", "icon\r", ".directory"}
JUNK_DIRS = {".spotlight-v100", ".fseventsd", ".trashes", ".temporaryitems", "$recycle.bin",
             ".documentrevisions-v100", "__macosx", "lost+found", ".git", "node_modules", "__pycache__"}

SCREEN_SIZES = {
    (1920, 1080), (2560, 1440), (3840, 2160), (1366, 768), (1440, 900), (2880, 1800), (1280, 800),
    (2560, 1600), (3024, 1964), (3456, 2234), (1536, 864), (1600, 900), (1680, 1050), (5120, 2880),
    (1280, 720), (1920, 1200), (3840, 2400), (2048, 1536), (2732, 2048), (2388, 1668), (2360, 1640),
    (1170, 2532), (1179, 2556), (1284, 2778), (1290, 2796), (1206, 2622), (1320, 2868),
    (1125, 2436), (1242, 2688), (828, 1792), (750, 1334), (1080, 1920), (1080, 2400), (1080, 2340),
    (1440, 3200), (1440, 3088), (1080, 2408), (720, 1600),
}
SCREEN_SIZES |= {(h, w) for (w, h) in SCREEN_SIZES}

CAMERA_NAME = re.compile(r"^(img|vid|dsc|dscn|dscf|mvi|pxl|gopr|gx\d|dji|p\d{7}|sam|photo|mov)[_-]?\d", re.I)
SCREENSHOT_NAME = re.compile(r"screen ?shot|screenshot|screen ?capture|snip|capture\d|scr_?\d", re.I)
SCREEN_REC_NAME = re.compile(r"screen ?record|screencast|screen ?capture|obs[_ ]", re.I)
SCAN_NAME = re.compile(r"\bscan|scanned|camscanner|^scan[_ -]?\d|_scan\b", re.I)
GRAPHIC_NAME = re.compile(r"logo|icon|banner|favicon|sprite|badge|avatar", re.I)
VOICE_NAME = re.compile(r"record|voice|memo|call|dictat|interview|meeting|whatsapp audio|ptt-", re.I)

DOC_KEYWORDS = [
    ("invoices", re.compile(r"invoice|inv[_ -]?\d|bill[_ -]|billing", re.I)),
    ("receipts", re.compile(r"receipt|payment[_ -]confirm|order[_ -]confirm", re.I)),
    ("contracts", re.compile(r"contract|agreement|\bnda\b|lease|terms[_ -]signed|signed", re.I)),
    ("reports", re.compile(r"report|statement|summary|analysis", re.I)),
    ("manuals", re.compile(r"manual|user[_ -]?guide|handbook|instructions|datasheet", re.I)),
]


@dataclass
class Result:
    category: str
    subtype: str | None
    confidence: float
    reason: str


def normalize_ext(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".tar.gz"):
        return "tar"
    dot = lower.rfind(".")
    if dot <= 0 or dot == len(lower) - 1:
        return ""
    ext = lower[dot + 1:]
    if len(ext) > 10 or not ext.isalnum():
        return ""
    return ALIASES.get(ext, ext)


def is_junk(name: str, size: int) -> str | None:
    lower = name.lower()
    if lower in JUNK_NAMES:
        return "system file"
    if name.startswith("._"):
        return "macOS resource fork"
    if lower.startswith("~$"):
        return "Office lock file"
    if size == 0:
        return "empty file"
    return None


def effective_ext(name_ext: str, kind: str | None) -> str:
    """Trust the content when the extension is missing or clearly wrong."""
    if not kind:
        return name_ext
    if kind == "ole":
        return name_ext if name_ext in {"doc", "xls", "ppt", "msg"} else "doc"
    if not name_ext:
        return kind
    zip_family = {"zip", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "jar", "apk", "key", "pages", "numbers"}
    if kind == "zip" and name_ext in zip_family:
        return name_ext
    mp4_family = {"mp4", "m4v", "mov", "m4a", "3gp"}
    if kind in mp4_family and name_ext in mp4_family:
        return name_ext
    if kind == "tiff" and name_ext in RAW:
        return name_ext
    if kind == "mp3" and name_ext not in AUDIO:
        return name_ext  # frame-sync check is weak; keep the name's extension
    return kind


def classify(name: str, ext: str, size: int, width=None, height=None, camera=None,
             has_exif=False, has_tags=False) -> Result:
    stem = name.rsplit(".", 1)[0]
    if ext in IMAGES:
        return _image(stem, ext, size, width, height, camera, has_exif)
    if ext in VIDEOS:
        return _video(stem, ext)
    if ext in AUDIO:
        return _audio(stem, ext, size, has_tags)
    if ext in DOCUMENTS:
        return _document(stem, ext)
    if ext in ARCHIVES:
        return Result("archives", None, 1.0, "archive format")
    if ext in CODE:
        return Result("code", None, 0.9, "source or config format")
    return Result("other", ext or "no-extension", 0.5, "unrecognised type")


def _image(stem, ext, size, w, h, camera, has_exif) -> Result:
    if ext in RAW:
        return Result("images", "raw", 1.0, "camera raw format")
    if ext in {"svg", "ico", "psd"}:
        return Result("images", "graphics", 0.95, f"{ext} is a graphics format")
    if camera:
        return Result("images", "photos", 0.97, f"camera data: {camera}")
    if SCREENSHOT_NAME.search(stem):
        return Result("images", "screenshots", 0.9, "screenshot filename")
    if SCAN_NAME.search(stem):
        return Result("images", "scans", 0.8, "scan filename")
    if w and h and (w, h) in SCREEN_SIZES and not has_exif:
        return Result("images", "screenshots", 0.8, f"screen-sized {w}x{h}, no camera data")
    if GRAPHIC_NAME.search(stem) or (w and h and max(w, h) <= 512 and ext in {"png", "gif", "webp", "bmp"}):
        return Result("images", "graphics", 0.75, "small image or graphic filename")
    if CAMERA_NAME.search(stem):
        return Result("images", "photos", 0.8, "camera-style filename")
    if ext == "heic":
        return Result("images", "photos", 0.8, "HEIC is a phone camera format")
    if has_exif:
        return Result("images", "photos", 0.7, "has EXIF data")
    if ext == "jpg" and size > 500_000:
        return Result("images", "photos", 0.55, "large JPEG without camera data")
    if ext == "png":
        return Result("images", "graphics", 0.55, "PNG without other clues")
    return Result("images", "photos", 0.5, "no strong clues")


def _video(stem, ext) -> Result:
    if SCREEN_REC_NAME.search(stem):
        return Result("videos", "screen-recordings", 0.9, "screen recording filename")
    if CAMERA_NAME.search(stem):
        return Result("videos", "recordings", 0.85, "camera-style filename")
    if ext in {"mts", "m2ts", "3gp"}:
        return Result("videos", "recordings", 0.8, f"{ext} is a camera format")
    return Result("videos", "clips", 0.65, "default for videos")


def _audio(stem, ext, size, has_tags) -> Result:
    if has_tags:
        return Result("audio", "music", 0.9, "has artist or title tags")
    if VOICE_NAME.search(stem) or ext in {"amr", "opus"}:
        return Result("audio", "voice", 0.75, "voice recording filename or format")
    if ext in {"wav", "aiff", "ogg"} and size < 1_000_000:
        return Result("audio", "sounds", 0.65, "short uncompressed audio")
    if ext in {"mp3", "flac", "wma"}:
        return Result("audio", "music", 0.65, "music format")
    if ext == "m4a":
        return Result("audio", "voice", 0.6, "untagged m4a is usually a voice memo")
    return Result("audio", "music", 0.5, "no strong clues")


def _document(stem, ext) -> Result:
    if ext in PRESENTATIONS:
        return Result("documents", "presentations", 1.0, "presentation format")
    if ext in SPREADSHEETS:
        return Result("documents", "spreadsheets", 1.0, "spreadsheet format")
    for sub, rx in DOC_KEYWORDS:
        if rx.search(stem):
            return Result("documents", sub, 0.75, f"'{sub}' keyword in filename")
    if ext == "pdf" and SCAN_NAME.search(stem):
        return Result("documents", "scans", 0.8, "scanned PDF filename")
    return Result("documents", "general", 0.8, "document format")
