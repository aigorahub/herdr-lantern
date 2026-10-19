<!-- agent-rules:start -->
## Agent rules

Done means the goal is met, not that nothing more can be found. In an authorized cleanup, when in doubt, delete, but keep a test that records a bug, a requirement, or an edge case.

### Tests
- Test only when an escaped bug costs more than keeping the test: money, data loss, security, runtime boundaries (model output, request bodies, files, user input), silent wrong answers. Skip cheap or obvious failures.
- Use the fewest tests that catch real failures. Extend an existing test before adding one. Coverage is never a reason.
- Use the cheapest reliable test at the boundary where the risk is. Add integration or end-to-end tests when wiring or rendering is the risk.
- No tests for what the type system enforces, no tautologies (expected values computed by the code under test), no mocks of the system under test, no snapshot tests by default.
- A test for a bug fix or behavior change must fail on the last revision without the change, because of the defect. Characterization tests may pass on both. If a test needs elaborate setup, simplify it or drop it.
- Each test names the input or state that breaks things and asserts behavior at the boundary, not internals.
- Never require an exact match of generated prose. Keep exact checks for deterministic contracts: schemas, required identifiers, tool arguments, limits. Define semantic tolerances before running, and never loosen them after a failure.
- Never edit, skip, or weaken a test to make it pass. Claim a pass only for a run you saw, and name the commit.

### Comments
- Comment only why: a non-obvious reason, an invariant the types cannot express, an external constraint or workaround, an intentional edge case. Never restate the code. No filler.
- Each comment is self-contained and stays true when nearby code changes.

### Finishing work
- A finding blocks only when it names a trigger, a code path, an impact, and evidence for one of these: data loss, a security hole, a wrong answer, a crash, a broken build, a destructive migration, or a missing acceptance criterion. Changed code can expose a defect in unchanged code. Speculative improvements are advisory and become issues.
- Each batch gets one full review and one delta review from two reviewers. The delta review covers only the delta. Then stop: finish, or give the user the remaining blockers to decide. Never start a third round.
- If the two reviewers disagree, the user decides from the evidence. The final review uses all configured reviewers for one round, under the same rules.
- A docs-only commit needs no new review or full CI rerun.
- Test infrastructure is not product scope. Green CI on the exact starting commit is the baseline. After a change, rerun the affected checks, including after dependency, config, fixture, or environment changes.
- If a requirement is impossible as written, say so and propose an alternative. Never redefine acceptance silently.
- At each batch end, report batches done against batches planned.
<!-- agent-rules:end -->
