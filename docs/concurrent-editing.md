# Concurrent editing

Independent sessions keep independent work cycles and temporary materials while
sharing permanent knowledge. Workers intentionally sharing one cycle share its
edit ownership. Cycle IDs are workflow coordinates, not authenticated identities.

## Procedure

1. Search and read relevant records. Reserve the existing cards you will change
   together with `workspace.reserve`: `cycle_id`, `material_ids`, and a one-line
   `reason`, such as "Update Windows installation commands".
2. A reservation response contains each card's owning cycle, reservation ID,
   reason and `expires_at` (Unix seconds, UTC). A competing request returns these
   facts as a conflict before acquiring any part of its requested set. Continue
   independent work; reading and search remain available.
3. Prepare and review a coherent proposal normally. Preparation reserves its
   complete affected set, including automatic incoming-link edits. Missing
   reservations are acquired for compatibility; use the explicit reserve step to
   announce your edit before generating it. Newly allocated IDs are covered too,
   but different new cards on the same subject still need semantic duplicate checks.
4. Apply the exact reviewed proposal under the current approval policy. The
   reservation generation and current card versions must still match. Successful
   application releases its affected cards and leaves the work cycle open.

`workspace.reservations` lists live ownership. The default TTL is 900 seconds;
`ttl_seconds` accepts 1 through 3600. `workspace.reservation_renew` requires the
owning `cycle_id` and exact `reservation_id`, and only extends a still-live lease.
Acquiring an already owned card does not silently renew it. If review waits too
long, release the reservation and later reread/reprepare instead of occupying
cards indefinitely. `workspace.reservation_release` never deletes knowledge or
temporary files. Releasing ownership invalidates proposals bound to that lease.

After expiry, another cycle may reserve the card. An old proposal remains invalid
even if its original cycle acquires a new reservation. Reserve again, read the
current cards and prepare/show a new proposal. Ordinary revision conflicts retain
the same reread/review requirements. Revising or discarding a proposal releases dropped claims not
needed by other pending proposals in that cycle. Finishing/cancelling releases
only the selected cycle's ownership and keeps existing cleanup approval rules.

## Storage, updates and limits

Workspace SQL schema 2 adds an ephemeral reservation table to the module's
Core-managed database. Install/update applies this additive migration through the
normal module lifecycle; ordinary startup never silently upgrades schema. Module
1.6 requires the schema update even when the Markdown format is already current.
Permanent records, Git history, instructions and existing proposals are preserved.
Use a coherent backup before updating; an older binary is not a compatible
in-place downgrade after this SQL migration.

Pre-upgrade proposals retain their immutable review and acquire ownership at
application after normal conflict checks. An interrupted application can retry
only after the original before/after verification; it cannot overwrite a later
edit. A context guard spans admission and file changes, while SQL transactions
are used only briefly for ownership bookkeeping. No SQLite transaction is held
while an agent reasons or waits for approval. There is no background heartbeat.
Expired rows are logically inactive and can be removed by subsequent bookkeeping.

The reservation check lives inside Workspace, including native update/move/delete
paths. Format migration waits until the selected context has no live edit leases.
Read, search and index repair remain available. Markdown files remain editable in
Obsidian or another editor: a reservation does not lock the filesystem, and direct
file writes do not announce intentions. Revision checks detect observed changes;
they do not make arbitrary concurrent external writes transactional.

Git still records accepted permanent versions. Per-session branches and automatic
semantic merging are not part of this feature.
