#!/bin/sh
# Action entrypoint: open the helper popup. Bind a key to
# aigora.session-helper.open to reach this from anywhere.
set -eu

exec "${HERDR_BIN_PATH:-herdr}" plugin pane open \
    --plugin aigora.session-helper \
    --entrypoint helper
