"""Exercise the evening prompt with a native Windows Python handoff writer."""

import os
import re
import sys
import tempfile
from pathlib import Path


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("this fixture requires native Windows Python")
    prompt, expected = sys.argv[1:3]
    path_match = re.search(r"handoff to this exact path: ([^\r\n]+)", prompt)
    id_match = re.search(r"Its first line must be exactly: handoff-id: ([^\r\n]+)", prompt)
    if not path_match or not id_match:
        raise RuntimeError("evening prompt omitted the handoff path or ID")
    path = path_match.group(1)
    if not re.match(r"^[A-Za-z]:\\", path):
        raise RuntimeError(f"handoff path is not native Windows form: {path}")
    if os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(expected)):
        raise RuntimeError(f"handoff path resolved incorrectly: {path} != {expected}")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                     prefix=".evening-handoff.", delete=False) as stream:
        stream.write(f"handoff-id: {id_match.group(1)}\n")
        stream.write("active: preserved w2\nclosed-temporary: w3\nnext: reconcile morning\n")
        temp_name = stream.name
    os.replace(temp_name, target)


if __name__ == "__main__":
    main()
