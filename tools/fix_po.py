"""Repair the reference lines ``makemessages`` mangles on Windows.

Run it after every ``makemessages`` and before ``compilemessages``::

    uv run python tools/fix_po.py

A long ``#:`` block comes out wrapped onto a second line that begins with a
*space* instead of a second ``#:``::

    #: .\\templates\\recipes\\tag_list.html:3
     .\\templates\\recipes\\tag_list.html:4

``msgfmt`` then refuses the entire file with "keyword unknown" — and the way
that presents is the problem: ``compilemessages`` reports an error most people
scroll past, no ``.mo`` is written, and the app carries on serving the *previous*
catalog. So a session's translations appear to have been compiled and simply are
not there, which looks like a caching problem for as long as you let it.

A plain script rather than a management command, because it has to be able to
run when the catalogs are in a state Django cannot load.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def repair(path):
    """Prefix every wrapped continuation line with ``#:``. Returns how many."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    out, fixed, in_references = [], 0, False
    for line in lines:
        if line.startswith("#:"):
            in_references = True
        elif in_references and line.startswith(" ") and line.strip():
            line = "#:" + line
            fixed += 1
        else:
            in_references = False
        out.append(line)
    if fixed:
        path.write_text("".join(out), encoding="utf-8")
    return fixed


def main(argv):
    targets = [pathlib.Path(name) for name in argv] or sorted(
        (ROOT / "locale").rglob("*.po")
    )
    total = 0
    for path in targets:
        fixed = repair(path)
        total += fixed
        print(f"{path.relative_to(ROOT) if path.is_absolute() else path}: "
              f"{fixed} wrapped reference line{'' if fixed == 1 else 's'} repaired")
    return 0 if total >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
