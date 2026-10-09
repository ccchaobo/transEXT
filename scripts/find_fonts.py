"""Check for a CJK-capable font on this machine."""

from __future__ import annotations

from pathlib import Path

WANTED = [
    "msyh.ttc", "msyhbd.ttc", "simsun.ttc", "simhei.ttf", "simkai.ttf",
    "Deng.ttf", "Dengb.ttf", "NotoSansCJK-Regular.ttc", "SourceHanSansSC-Regular.otf",
    "Arial Unicode.ttf", "malgun.ttf", "meiryo.ttc", "YuGothM.ttc",
]


def main() -> int:
    roots = [Path("C:/Windows/Fonts"), Path.home() / "AppData/Local/Microsoft/Windows/Fonts"]
    found = []
    for root in roots:
        if not root.exists():
            continue
        for name in WANTED:
            path = root / name
            if path.exists():
                found.append(path)
    for path in found:
        print(f"{path.stat().st_size / 1024 / 1024:6.1f} MiB  {path}")
    if not found:
        print("no CJK font found in the standard locations")
    # Also list what is actually there, for a fallback decision.
    fonts_dir = Path("C:/Windows/Fonts")
    if fonts_dir.exists():
        cjk_like = sorted(
            p.name for p in fonts_dir.iterdir()
            if p.suffix.lower() in (".ttf", ".ttc", ".otf") and p.stat().st_size > 3_000_000
        )
        print("\nlarge font files (likely with CJK coverage):")
        for name in cjk_like[:20]:
            print("  ", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
