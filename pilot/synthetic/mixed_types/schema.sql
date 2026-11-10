CREATE SCHEMA pilot_mixed;
CREATE SCHEMA pilot_mixed_api;
CREATE TABLE pilot_mixed.readings(
  reading_id bigint PRIMARY KEY,
  qty integer NOT NULL,
  active boolean NOT NULL,
  observed_at timestamptz NOT NULL,
  label text NOT NULL
);
CREATE INDEX readings_document_order ON pilot_mixed.readings(reading_id);
