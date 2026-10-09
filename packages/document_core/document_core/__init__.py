"""document_core -- format-independent models, coordinates and text handling.

This package is the single source of truth for:

* the coordinate contract (:mod:`document_core.coords`),
* the unified document model (:mod:`document_core.models`),
* text cleaning and protected-token handling (:mod:`document_core.cleaning`),
* reading-order and layout heuristics (:mod:`document_core.layout`),
* parse-revision anchor migration (:mod:`document_core.revision`),
* stable identifiers and hashing (:mod:`document_core.ids`).

It deliberately has **no third-party dependencies**, so it can be imported by
adapters, the API service, the worker and the tests alike.
"""

from .cleaning import (  # noqa: F401
    check_placeholders,
    clean_text,
    count_words,
    dehyphenate,
    join_lines,
    looks_like_garbage,
    normalise_unicode,
    protect_spans,
    restore_spans,
    split_sentences,
)
from .coords import (  # noqa: F401
    AffineTransform,
    BBox,
    CoordinateSpace,
    PageGeometry,
    bbox_area,
    bbox_from_polygon,
    clamp,
    expand_bbox,
    iou,
    normalise_bbox,
    polygon_from_bbox,
    union_bbox,
)
from .ids import (  # noqa: F401
    block_id,
    content_hash,
    hash_bytes,
    hash_text,
    jaccard,
    new_id,
    normalise_for_hash,
    normalise_for_match,
    page_id,
    shingles,
    slug,
    span_id,
)
from .layout import (  # noqa: F401
    Column,
    ColumnPlan,
    LayoutParams,
    LayoutResult,
    Paragraph,
    ReadingOrder,
    TextLine,
    analyse,
    assemble_paragraphs,
    build_sections,
    classify_furniture,
    detect_columns,
    segment_bands,
)
from .models import (  # noqa: F401
    SCHEMA_VERSION,
    Asset,
    Block,
    BlockType,
    Confidence,
    ConfidenceLevel,
    Page,
    ParseResult,
    ParseStatus,
    QualityIssue,
    Section,
    Severity,
    SourceSpan,
    TableData,
)
from .revision import (  # noqa: F401
    BlockMatch,
    MatchQuality,
    MigrationReport,
    match_blocks,
    migrate_anchor,
)

__version__ = "0.1.0"
