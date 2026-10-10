# Usage source checks

These checks ran on macOS on October 10, 2026. They used signed in provider access. They did not send input to a peer. They did not start a model turn.

The checks used CodexBar CLI 0.74.0 from its macOS arm64 release. The archive SHA256 matched the published digest: `9aab1b9229f5d1d77de21b99aa887a60a47921bfd9b66e4b941a0e8a9d6e38e8`. The CLI ran from a temporary directory. Its private test config disabled cookies and hooks. It had no saved token accounts. It did not change the user's CodexBar config.

## Results

| Provider | Source | Result |
| --- | --- | --- |
| Codex | Native app server | Initialization, account read, and limit read succeeded. The response had an account ID, plan type, limit IDs, a weekly percentage, a reset epoch, and a separate credits balance. |
| Claude | CodexBar OAuth | Failed with credentials not found. This source cannot read the current login on this machine. |
| Claude | CodexBar CLI | Succeeded. The response included account email, login method, session and weekly windows, and a numeric weekly reset time. The returned source was `claude`. |
| Grok | CodexBar CLI | Failed with Method not found. The installed Grok CLI does not expose the requested billing method. |
| Grok | CodexBar OAuth | Succeeded. The response included account email, organization, login method, a weekly percentage, and a numeric reset time. The returned source was `grok-web`. This was OAuth billing, with cookies off. |
| Grok | CodexBar Web | Unavailable with cookies off. Do not use this source. |
| Antigravity | CodexBar CLI | Succeeded for limits. It did not return an account email or account ID. It supplied separate Gemini and Claude/GPT windows for five hours and one week. |
| Antigravity | Native `agy -p /usage --output-format json` | Succeeded with `num_turns: 0`. It supplied the same four allowance groups. It did not return an account identity. |
| Antigravity | Existing process local GetUserStatus endpoint | Rejected the request with HTTP 401. No verified account identity was obtained. |

The Antigravity print report is a quota report. The [official usage command documentation](https://antigravity.google/docs/cli/commands/usage/) describes the quota refresh. The check does not establish a billing identity for the open tabs.

## Account scope check

Herdr process info supplied the current Codex PID and arguments. A same user macOS `KERN_PROCARGS2` read obtained the process environment. Its HOME matched the process's expected home. The check printed only selector names and a match result. It did not print or store the full environment.

This proves that the proposed macOS environment access works on this machine. It does not prove that all provider overrides have been handled. The implementation must reject unsupported selectors and test process changes during a read.

## Plan changes from these checks

Select the native Codex source, explicit Claude CLI source, and explicit Grok OAuth source. Do not use Auto or browser cookie fallback. CodexBar's returned Grok source label is `grok-web` even for the OAuth strategy. Validate the requested strategy and isolated config as well as the returned label.

Keep Antigravity account identity unresolved until a supported identity source passes. Do not use the installation ID as a billing account ID. Keep all four Antigravity allowance groups in parser fixtures. Do not attach those values to an unverified account.

Use `extraRateWindows` when it supplies the complete Antigravity groups. The primary and secondary fields repeat parts of those groups. Do not display them twice.

The source contract is the [pinned CodexBar CLI documentation](https://github.com/steipete/CodexBar/blob/v0.74.0/docs/cli.md). The [pinned Grok source implementation](https://github.com/steipete/CodexBar/blob/v0.74.0/Sources/CodexBarCore/Providers/Grok/GrokProviderDescriptor.swift) separates CLI, OAuth, and cookie strategies. The [pinned Antigravity documentation](https://github.com/steipete/CodexBar/blob/v0.74.0/docs/antigravity.md) describes quota groups and the CLI report.
