"""Record Field Status's busy-machine warning for the opt-in launch guard."""

import os
import sys
import threading
import time


BUDGET = 3


def main() -> None:
    deadline = float(sys.argv[1]) + BUDGET
    remaining = min(deadline - time.time(), float(BUDGET))
    if remaining <= 0:
        return

    # The wrapper may be unable to signal this interpreter on Windows.
    timer = threading.Timer(remaining, os._exit, (0,))
    timer.daemon = True
    timer.start()

    # The wrapper uses -S so site hooks run inside the deadline.
    import site
    site.main()
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    from field_status import machine_warning

    # Counting done terminals would require a child the wrapper could not reliably stop.
    text = machine_warning([], done_limit=0)
    if not text or time.time() >= deadline:
        return
    result = os.path.join(sys.argv[2], "result")
    with open(result + ".tmp", "w", encoding="utf-8", newline="\n") as stream:
        stream.write(f"lantern: {text} Starting the agent anyway.\n")
    os.replace(result + ".tmp", result)


try:
    main()
except BaseException:
    pass
os._exit(0)
