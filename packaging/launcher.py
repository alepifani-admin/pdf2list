"""PyInstaller launcher (keeps package-relative imports)."""

from __future__ import annotations

from pdf2list.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
