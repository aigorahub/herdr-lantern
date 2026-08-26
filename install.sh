#!/bin/sh
# One command after Herdr is installed: install Lantern and open it.
# First-run setup happens in the lantern chat.
set -eu

if ! command -v herdr >/dev/null 2>&1; then
    printf '%s\n' "install: herdr is not on PATH. Install Herdr first: https://herdr.dev" >&2
    exit 1
fi

if herdr plugin list 2>/dev/null | grep -q 'aigora.lantern'; then
    printf '%s\n' "install: aigora.lantern is already listed. Opening it."
else
    herdr plugin install aigorahub/herdr-lantern
fi

herdr plugin action invoke aigora.lantern.open
printf '%s\n' "install: lantern is open. In that chat, set the default spawn (harness, model, setting) so you can later just name a repo."
