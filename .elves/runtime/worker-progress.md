# Issue cleanup worker progress
Read AGENTS.md, plan revision 2, and the worker packet. Verified branch chore/issue-cleanup at 9190bfb.
Next: S1. Verify fixed shell wrapper arguments against the live direct child before Plan Usage checks its account.

S1 blocked at 9190bfb. The working tree has the wrapper fix in bin/desktop.py and regression tests in tests/desktop.py and tests/plan_usage.py. The focused tests pass here and fail against 4ac9846. Python 3.9 syntax compilation and the full Desktop suite pass. The smoke suite stops in the existing Field Status wrapper reap case. That case supplies a shell at synthetic PID 100 with no verified live direct child and expects target to close. The new wrapper rule correctly preserves the shell. The same Field Status test passes against 4ac9846. The packet places tests/field_status.py outside S1. A fixture update can supply a matching live child while retaining the close assertion.

No slices committed or pushed. Next action depends on lead resolution of the S1 scope conflict.
S1 passed smoke and affected checks after t4kt approved the reap fixture scope extension. Pushed the wrapper and fixture changes. Next: S2 outcome token normalization.
S2 pushed after smoke, Field Status, and Python 3.9 compilation passed. Next: S3 application aliases, omp reap, and readout adapter integration.
S3 pushed after smoke, Desktop, Field Status, and Python 3.9 compilation passed. Next: S4 Windows desktop launch and process ownership.
S4 pushed after local smoke, Desktop, and Python 3.9 compilation passed. Both batch launchers are exercised in the Windows CI smoke job. Next: S5 desktop onboarding and docs.
S5 passed local smoke, Desktop, Teams, Agent Switch, and Python 3.9 compilation. Pushed S1 to S5. Next: report the final head and verification to t4kt.
Reported the five slice commits and evidence to t4kt. Final worker head 64ef484123bc058f81170e8a745e81f4cb760626. Live Windows desktop is unverified.
The Windows Smoke test step passed on S4 d2faf92 in GitHub run 46157207647 with both batch launchers against a stub core.
S5 review fixes pushed. The three platform statements agree. The Windows ANSI regression fails on 64ef484 and passes with explicit UTF-8. Full smoke is running.
Full local smoke passed on 8315da30b8717f9d33f87f6bb698ef74e4dff720. Reported the head and checks to t4kt.
