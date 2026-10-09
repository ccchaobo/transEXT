"""Format and engine adapters.

Everything that depends on a third-party parsing, rendering or recognition
library lives here, behind an interface, so the rest of the system can be tested
without those libraries and a library can be replaced without a rewrite.  The
PyMuPDF licence obligation (ADR 0003) is confined to this package for that reason.
"""

__all__ = [
    "docx_convert",
    "fonts",
    "ocr",
    "pdf_extract",
]
