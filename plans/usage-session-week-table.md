# Session and weekly usage table

The 0.17.1 table mixed percentages with IDs, plan codes, credits, source
labels, and update age. Version 0.17.2 shows the values needed for the next
work session: Service, Session, Session reset, Week, and Week reset.

The display omits account IDs and model IDs. It does not expand raw plan
codes such as promax into an unverified public plan name. Reset times use
ET. A reset today shows its time. Later resets also show the date.

A narrow pane puts each reset below its percentage. Very small panes stack
the two periods. Color marks low percentages. Plain output and NO_COLOR
preserve the same text. Unknown values show --. Stale percentages and past
reset times keep warnings.

When one login has several limits in a period, show the lowest remaining
percentage and the reset for that same limit. A note states this rule.
Never add percentages. Equal percentages select the latest matching reset.
Separate logins keep separate lines inside the service row. Their IDs stay
out of the display. A failed login read remains visible in a short note.

Collection and account checks stay as released in 0.17.1. The current Google
reader skips quota reports without a verified account identity. The current
Grok reader skips processes with credential or base URL overrides.
Native Antigravity /usage can return four quota windows with no model turn.
Those collection limits need a separate source change. This display change
does not assign quota from one login to another.

Tests cover hidden metadata, missing periods, the limiting reset, separate
logins, warnings, color, plain output, and widths from 20 to 88 columns.

Version 0.17.3 changes this contract at the user's request. The table now shows
only Service, Session left, and Week left. Each service has one row with the
lowest remaining percentage across its pools and local login profiles.
Reset times, plan codes, credits, model names, and IDs stay hidden.
Google reads structured native usage groups with zero model turns and binds
the result to its local login profile. Grok reads the saved OAuth plan despite
an inherited API key. Fugu reads Sakana's usage API with its configured profile
key. All reads check source binding and avoid model turns.
Field Status uses the top two thirds of the right column. Usage uses the bottom third.


Version 0.17.4 adds Week resets beside the percentages. The column uses local
time for the weekly limit that sets the displayed percentage. Equal minimum
percentages with different reset times show Varies. Missing or expired times
show --. Panes under 39 columns keep the percentage table and ask for more width.
No provider read changes. IDs, model names, plan codes, and credits stay hidden.
