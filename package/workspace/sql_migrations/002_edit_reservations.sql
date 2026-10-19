-- Ephemeral edit ownership is separate from permanent records and Git history.
CREATE TABLE workspace_edit_reservations (
 context TEXT NOT NULL,
 material_id TEXT NOT NULL,
 cycle_id TEXT NOT NULL,
 lease_id TEXT NOT NULL,
 reason TEXT NOT NULL,
 expires_at REAL NOT NULL,
 PRIMARY KEY(context, material_id)
);
CREATE INDEX workspace_edit_reservations_cycle ON workspace_edit_reservations(context, cycle_id, lease_id);
