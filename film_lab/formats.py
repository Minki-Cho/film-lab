"""Shared input formats for the engine, desktop picker and command line."""
from pathlib import Path

RAW_EXTENSIONS = frozenset({".arw"})
RASTER_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"})
SUPPORTED_EXTENSIONS = RAW_EXTENSIONS | RASTER_EXTENSIONS
IMAGE_FILE_FILTER = (
    "지원 사진 (*.ARW *.arw *.JPG *.jpg *.JPEG *.jpeg *.PNG *.png "
    "*.TIF *.tif *.TIFF *.tiff *.WEBP *.webp *.BMP *.bmp);;"
    "Sony RAW (*.ARW *.arw);;JPEG (*.JPG *.jpg *.JPEG *.jpeg);;"
    "PNG (*.PNG *.png);;TIFF (*.TIF *.tif *.TIFF *.tiff);;"
    "WebP (*.WEBP *.webp);;BMP (*.BMP *.bmp)"
)


def is_supported_image(path: str | Path) -> bool:
    """Check only the extension; the engine separately validates file contents."""
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS
