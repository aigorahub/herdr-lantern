# Explicit Gemini seats

Gemini seats are opt-in. They do not change Lantern's own helper harness, the default coding route, or another provider's defaults. Use `--kind gemini` with an exact model ID; bare `gemini`, `default`, and family aliases are rejected. Lantern never substitutes another Gemini model.

`model-route gemini list` inventories all twelve policy entries with packaged-constant presence, lifecycle decisions and unknown access. It does not require a routing acknowledgement.

`model-route gemini <exact-id> [thinking-level]` reads installed package metadata and model constants without launching Gemini, reading settings or credentials, or sending inference. `LANTERN_GEMINI_PACKAGE_ROOT` can name the installed `@google/gemini-cli` package when a managed launcher obscures its location. Otherwise discovery follows the `gemini` executable's symlink and common package layout. Missing packages, unrecognized package layouts, IDs absent from the package, and unreviewed IDs fail closed. Package presence does not establish service availability.

Gemini CLI 0.65.0 can remap an explicit ID and use fallback chains. **Exact backend selection is not guaranteed.** The route and preflight require `LANTERN_GEMINI_ACK_NATIVE_ROUTING=1` as an explicit acknowledgement of that limitation. Do not enable it automatically or when strict backend identity is required. This acknowledgement does not authorize a different data route or override account policy.

Preflight returns `available: null`, `launchable: true`, `entitlement: "unknown"`, `inference: "untested"`, `actual_model_verified: false`, `cli_may_remap: true`, and `substitute: null` after its local checks. Exit zero means the requested setup passed those checks; it does not mean the backend is accessible. A refusal exits nonzero and supplies no substitute. The Herdr wrapper repeats model/effort checks before starting a Gemini seat, including rejecting missing or duplicate model flags. It prints the native remapping limitation before contacting Herdr.

## Thinking bridge contract

Gemini has no native `--effort` flag. Explicit thinking needs a launcher installed by the environment owner, which consumes the private `--thinking-level` option before executing native Gemini. Without a level, Lantern leaves the CLI's thinking defaults alone.

The owner must install a canonical `gemini` launcher on the **target pane's PATH**, configure the native settings bridge below, and explicitly set `LANTERN_GEMINI_EFFORT_BRIDGE=1` in the controller after that setup has been reviewed. The route checks the current launcher's metadata-only `--lantern-capabilities` command for this JSON:

```json
{"native_thinking_bridge":true,"schema_version":1}
```

The capability check uses canonical `gemini`, matching the executable name Herdr 0.9.5 starts. Controller environment variables are not assumed to cross the Herdr socket. Before rollout, the owner must verify the target shell resolves the intended canonical launcher. A raw CLI will reject the private option rather than silently ignoring the effort.

The launcher requires a single explicit `--model`/`-m`, consumes `--thinking-level minimal|low|medium|high`, and exports the exact ID as `LANTERN_GEMINI_MODEL` and the uppercase level as `LANTERN_GEMINI_THINKING_LEVEL`. It clears inherited bridge variables when no private option is supplied. The environment owner adds this entry to native user settings, preserving existing configuration:

```json
{
  "modelConfigs": {
    "customOverrides": [{
      "match": {"model": "${LANTERN_GEMINI_MODEL:-__lantern_no_model__}"},
      "modelConfig": {
        "generateContentConfig": {
          "thinkingConfig": {"thinkingLevel": "${LANTERN_GEMINI_THINKING_LEVEL:-HIGH}"}
        }
      }
    }]
  }
}
```

Lantern does not write those settings, copy credentials, replace a home directory, or set system settings overrides. The inspected CLI recursively expands string values before validation and appends custom overrides after builtin overrides. Exact matching means a remapped model may miss this override; deeper alias overrides can supersede it. The output therefore records **requested** thinking, not runtime verification. Native configuration support and API level support do not prove a completed model turn.

After acknowledging the native routing limitation and provisioning the launcher, explicit onboarding is:

```bash
bin/onboard apply --kind gemini --model gemini-3.8-flash --effort high
bin/model-route gemini gemini-3.8-flash high
bin/model-preflight gemini gemini-3.8-flash high
```

Pass the returned `argv` after Herdr's `--` delimiter. Do not pass `--thinking-level` to an unconfigured native CLI. These examples specify a model; they do not make it a default recommendation.

## Bounded lifecycle policy, reviewed 2026-10-28

The policy covers the following twelve IDs. It is a conservative Cloud-compatible routing policy, not a universal catalog. New model names require a policy review even if they appear in the installed package.

| Exact ID | Routing policy | Documented thinking levels |
|---|---|---|
| `gemini-3.1-pro-preview` | Current preview; access unknown | low, medium, high |
| `gemini-3.8-flash` | Current GA; access unknown | low, medium, high |
| `gemini-3.5-flash` | Current GA; access unknown | minimal, low, medium, high |
| `gemini-3-flash-preview` | Current preview; access unknown | minimal, low, medium, high |
| `gemini-3.5-flash-lite` | Current GA; access unknown | minimal, low, medium, high |
| `gemini-3.1-flash-lite` | Current GA; access unknown | minimal, low, medium, high |
| `gemini-3-pro-preview` | Blocked: original shut down March 9; alias behavior is not portable | Not routed |
| `gemini-2.5-pro` | Blocked: Cloud retirement October 20 | Not routed |
| `gemini-2.5-flash` | Blocked: Cloud retirement October 20 | Not routed |
| `gemini-2.5-flash-lite` | Blocked: Cloud retirement October 20 | Not routed |
| `gemma-4-31b-it` | Blocked: Developer API ID documented; Cloud route unqualified | Not routed |
| `gemma-4-26b-a4b-it` | Blocked: Developer API ID documented; Cloud route unqualified | Not routed |

Sources: [Cloud lifecycle](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions), [Cloud thinking](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/thinking), [3 Pro shutdown](https://ai.google.dev/gemini-api/docs/models/gemini-3-pro-preview), [Gemma Developer API IDs](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api). Cloud retirement is not a claim about Developer API retirement. A separately qualified backend can add an explicit policy in a later change; unknown is never treated as validated.
