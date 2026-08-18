#!/bin/sh
# Action entrypoint: open the lantern. Bind a key to
# aigora.lantern.open to reach this from anywhere.
set -eu

exec "${HERDR_BIN_PATH:-herdr}" plugin pane open \
    --plugin aigora.lantern \
    --entrypoint helper
