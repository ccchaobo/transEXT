"""Parse-revision versioning and anchor migration.

Spec 8 requires that:

* a :class:`~document_core.models.Block` id is stable **within** a parse
  revision;
* re-parsing creates a **new** revision;
* annotations are migrated by comparing content and geometry, and a
  low-confidence match is marked ``needs_relocation`` rather than silently
  attached to a look-alike paragraph.

This module implements that migration deterministically so it can be unit
tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .coords import BBox, iou
from .ids import jaccard, shingles
from .models import Block, BlockType

__all__ = [
    "MatchQuality",
    "BlockMatch",
    "migrate_anchor",
    "match_blocks",
    "MigrationReport",
]


class MatchQuality(str, Enum):
    EXACT = "exact"             # same id -> trivially the same block
    CONTENT = "content"         # content fingerprint + geometry agree
    GEOMETRIC = "geometric"     # only geometry agrees
    WEAK = "weak"               # below the relocation threshold
    NONE = "none"

    @property
    def is_reliable(self) -> bool:
        return self in (MatchQuality.EXACT, MatchQuality.CONTENT)


@dataclass
class BlockMatch:
    old_block_id: str
    new_block_id: str | None
    quality: MatchQuality
    score: float
    reason: str
    needs_relocation: bool

    def to_dict(self) -> dict:
        return {
            "old_block_id": self.old_block_id,
            "new_block_id": self.new_block_id,
            "quality": self.quality.value,
            "score": round(self.score, 4),
            "reason": self.reason,
            "needs_relocation": self.needs_relocation,
        }


@dataclass
class MigrationReport:
    matches: list[BlockMatch] = field(default_factory=list)
    unmatched_old: list[str] = field(default_factory=list)
    new_block_count: int = 0
    old_block_count: int = 0

    @property
    def relocated(self) -> list[BlockMatch]:
        return [m for m in self.matches if m.needs_relocation]

    def to_dict(self) -> dict:
        return {
            "old_block_count": self.old_block_count,
            "new_block_count": self.new_block_count,
            "matched": len([m for m in self.matches if m.new_block_id]),
            "reliable": len([m for m in self.matches if m.quality.is_reliable]),
            "needs_relocation": len(self.relocated),
            "unmatched_old": list(self.unmatched_old),
            "matches": [m.to_dict() for m in self.matches],
        }


def _geom_score(a: Block, b: Block) -> float:
    if not a.spans or not b.spans:
        return 0.0
    if a.pages and b.pages and a.pages[0] != b.pages[0]:
        return 0.0
    best = 0.0
    for sa in a.spans:
        for sb in b.spans:
            if sa.page_index != sb.page_index:
                continue
            best = max(best, iou(sa.bbox, sb.bbox))
    return best


def match_blocks(
    old_blocks: list[Block],
    new_blocks: list[Block],
    *,
    content_threshold: float = 0.72,
    weak_threshold: float = 0.45,
) -> MigrationReport:
    """Greedy one-to-one matching from old blocks to new blocks.

    Ordering matters for determinism: candidates are considered by descending
    combined score, ties broken by block id, so repeated runs give identical
    results.
    """
    report = MigrationReport(old_block_count=len(old_blocks), new_block_count=len(new_blocks))
    new_by_id = {b.id: b for b in new_blocks}
    used_new: set[str] = set()

    candidates: list[tuple[float, Block, Block, float, float]] = []
    for ob in old_blocks:
        for nb in new_blocks:
            content = jaccard(shingles(ob.text), shingles(nb.text))
            geom = _geom_score(ob, nb)
            # Content dominates; geometry breaks ties and rescues short blocks
            # (headings, captions) whose text is too short for n-grams.
            combined = 0.75 * content + 0.25 * geom
            if combined <= 0.0:
                continue
            candidates.append((combined, ob, nb, content, geom))
    candidates.sort(key=lambda t: (-t[0], t[1].id, t[2].id))

    matched_old: set[str] = set()
    for combined, ob, nb, content, geom in candidates:
        old_id, new_id = ob.id, nb.id
        if old_id in matched_old or new_id in used_new:
            continue
        if combined < weak_threshold:
            continue
        if old_id == new_id and ob.type == nb.type:
            quality = MatchQuality.EXACT
        elif content >= content_threshold:
            quality = MatchQuality.CONTENT
        elif geom >= 0.6:
            quality = MatchQuality.GEOMETRIC
        else:
            quality = MatchQuality.WEAK
        reliable = quality.is_reliable
        report.matches.append(
            BlockMatch(
                old_block_id=old_id,
                new_block_id=new_id,
                quality=quality,
                score=combined,
                reason=f"content={content:.3f} geometry={geom:.3f}",
                needs_relocation=not reliable,
            )
        )
        matched_old.add(old_id)
        used_new.add(new_id)

    report.unmatched_old = [b.id for b in old_blocks if b.id not in matched_old]
    for old_id in report.unmatched_old:
        report.matches.append(
            BlockMatch(
                old_block_id=old_id,
                new_block_id=None,
                quality=MatchQuality.NONE,
                score=0.0,
                reason="no candidate above threshold",
                needs_relocation=True,
            )
        )
    report.matches.sort(key=lambda m: m.old_block_id)
    return report


def migrate_anchor(
    old_block_id: str,
    old_blocks: list[Block],
    new_blocks: list[Block],
    *,
    content_threshold: float = 0.72,
) -> BlockMatch:
    """Migrate a single anchor, used when re-parsing invalidates block ids."""
    old = next((b for b in old_blocks if b.id == old_block_id), None)
    if old is None:
        return BlockMatch(
            old_block_id=old_block_id,
            new_block_id=None,
            quality=MatchQuality.NONE,
            score=0.0,
            reason="old block not present in previous revision",
            needs_relocation=True,
        )
    direct = next((b for b in new_blocks if b.id == old_block_id and b.type == old.type), None)
    if direct is not None:
        return BlockMatch(
            old_block_id=old_block_id,
            new_block_id=direct.id,
            quality=MatchQuality.EXACT,
            score=1.0,
            reason="same id and type in both revisions",
            needs_relocation=False,
        )
    report = match_blocks([old], new_blocks, content_threshold=content_threshold)
    for match in report.matches:
        if match.old_block_id == old_block_id:
            return match
    return BlockMatch(
        old_block_id=old_block_id,
        new_block_id=None,
        quality=MatchQuality.NONE,
        score=0.0,
        reason="old block not present in previous revision",
        needs_relocation=True,
    )
