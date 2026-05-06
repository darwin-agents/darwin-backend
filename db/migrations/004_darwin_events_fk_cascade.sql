-- Migration 004: Add ON DELETE CASCADE to darwin_events.darwin_id FK
--
-- The initial schema had darwin_events.darwin_id referencing darwins(id)
-- without CASCADE. Greenfield installs from 001_initial.sql already have it;
-- this migration patches existing databases.

ALTER TABLE darwin_events
  DROP CONSTRAINT IF EXISTS darwin_events_darwin_id_fkey;

ALTER TABLE darwin_events
  ADD CONSTRAINT darwin_events_darwin_id_fkey
    FOREIGN KEY (darwin_id) REFERENCES darwins(id) ON DELETE CASCADE;

-- parent_event_id self-reference also needs CASCADE so event threads can be pruned.
ALTER TABLE darwin_events
  DROP CONSTRAINT IF EXISTS darwin_events_parent_event_id_fkey;

ALTER TABLE darwin_events
  ADD CONSTRAINT darwin_events_parent_event_id_fkey
    FOREIGN KEY (parent_event_id) REFERENCES darwin_events(id) ON DELETE CASCADE;
