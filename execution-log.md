# Execution log

2026-09-05: Staged isolated worktree from 0d7cf21b705aec09aee015e7e32ba48e6f286a22. Implementation authorized. Merge not authorized.

Draft PR37 opened from staging. Added scoped mailbox and bounded Herdr observer. All21 team tests passed. Live observer required separate subscription and snapshot connections; corrected and verified. Bot review trigger not configured in the repository; CI is not counted as bot review.

Independent review at0d10c41 found four blockers: retired inbox access, response size, nonUTF8 output, and registration crash recovery. Fixed all four and added regression tests. CI also found leading-dash receipts and test SQLite handle cleanup on Windows; receipt format now hex and tests close all connections. Final verification pending.
