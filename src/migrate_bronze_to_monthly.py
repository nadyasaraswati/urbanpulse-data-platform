"""Pindahkan file Bronze lama ke folder bulan.

  data/bronze/<kind>/YYYY-MM-DD.jsonl  ->  data/bronze/<kind>/YYYY-MM/YYYY-MM-DD.jsonl

Default dry-run (hanya menampilkan rencana). Tambahkan --apply untuk memindahkan.
File tujuan yang sudah ada TIDAK ditimpa (dilewati dan dilaporkan).
Jangan dijalankan saat ingestion berjalan; script ini mengambil lock yang sama.
"""
import re
import sys
import shutil
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ingestion.common import BRONZE, LockBusy, source_lock

DAY_FILE = re.compile(r"^(\d{4}-\d{2})-\d{2}\.jsonl$")


def plan(kind):
    moves = []
    folder = BRONZE / kind
    if folder.is_dir():
        for f in sorted(folder.iterdir()):
            m = f.is_file() and DAY_FILE.match(f.name)
            if m:
                moves.append((f, folder / m.group(1) / f.name))
    return moves


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="benar-benar memindahkan (default: dry-run)")
    a = ap.parse_args(argv)

    moved = skipped = 0
    try:
        for kind in ("weather", "traffic"):
            with source_lock(kind, timeout=0):
                for src, dst in plan(kind):
                    rel = f"{src.relative_to(BRONZE)} -> {dst.relative_to(BRONZE)}"
                    if dst.exists():
                        print(f"SKIP (tujuan sudah ada): {rel}")
                        skipped += 1
                    elif a.apply:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src), str(dst))
                        print(f"MOVED: {rel}")
                        moved += 1
                    else:
                        print(f"PLAN : {rel}")
                        moved += 1
    except LockBusy as e:
        print(f"Dibatalkan: {e}")
        return 1
    verb = "dipindahkan" if a.apply else "akan dipindahkan (dry-run)"
    print(f"\nSelesai: {moved} file {verb}, {skipped} dilewati.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
