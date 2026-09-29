-- PostGIS is enabled even though the current schema stores geometry as GeoJSON
-- (docs/DECISIONS.md ADR-002). Re-introducing geometry columns is then a migration
-- rather than a re-architecture.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
