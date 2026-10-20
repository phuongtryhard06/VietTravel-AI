-- Migration: Create OSM Attractions Staging Table
-- Description: Staging table for raw/standardized OpenStreetMap POI ingestion with unique osm_id
-- Date: 2026-10-20
-- Project: ChatbotTravel_server (Multi-Agent System Upgrade)

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM information_schema.tables 
        WHERE table_schema = 'public' 
        AND table_name = 'osm_attractions_staging'
    ) THEN
        -- Create Staging Table
        CREATE TABLE osm_attractions_staging (
            id SERIAL PRIMARY KEY,
            osm_id VARCHAR(50) NOT NULL,
            osm_source_layer VARCHAR(100) NOT NULL,
            osm_code INTEGER,
            fclass VARCHAR(100) NOT NULL,
            name VARCHAR(255) NOT NULL,
            category VARCHAR(100) NOT NULL,
            latitude DOUBLE PRECISION NOT NULL,
            longitude DOUBLE PRECISION NOT NULL,
            address VARCHAR(500) NOT NULL,
            description TEXT,
            price DOUBLE PRECISION DEFAULT NULL,
            opening_hours VARCHAR(200) DEFAULT NULL,
            phone_number VARCHAR(50) DEFAULT NULL,
            image_url VARCHAR(500) DEFAULT NULL,
            rating DOUBLE PRECISION DEFAULT NULL,
            language VARCHAR(50) DEFAULT 'vietnamese',
            tags JSONB DEFAULT '[]'::jsonb,
            aliases JSONB DEFAULT '[]'::jsonb,
            raw_properties JSONB DEFAULT '{}'::jsonb,
            imported_to_attraction BOOLEAN DEFAULT FALSE,
            attraction_id VARCHAR(50) DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            -- Ensure unique osm_id to prevent duplicate ingestion
            CONSTRAINT uq_osm_staging_osm_id UNIQUE (osm_id)
        );

        -- Performance & Query Indexes
        CREATE INDEX idx_osm_staging_coords ON osm_attractions_staging (latitude, longitude);
        CREATE INDEX idx_osm_staging_category ON osm_attractions_staging (category);
        CREATE INDEX idx_osm_staging_fclass ON osm_attractions_staging (fclass);
        CREATE INDEX idx_osm_staging_layer ON osm_attractions_staging (osm_source_layer);
        CREATE INDEX idx_osm_staging_imported ON osm_attractions_staging (imported_to_attraction);

        RAISE NOTICE 'osm_attractions_staging table created successfully with unique constraint on osm_id';
    ELSE
        RAISE NOTICE 'osm_attractions_staging table already exists';
    END IF;
END $$;

-- Comments for documentation & data dictionary
COMMENT ON TABLE osm_attractions_staging IS 'Staging table for OpenStreetMap POI records before promoting to core Attractions table';
COMMENT ON COLUMN osm_attractions_staging.osm_id IS 'Unique OpenStreetMap feature ID (e.g., osm_104770283)';
COMMENT ON COLUMN osm_attractions_staging.osm_source_layer IS 'Source shapefile layer name (e.g., gis_osm_pois_free_1)';
COMMENT ON COLUMN osm_attractions_staging.osm_code IS 'OSM numerical feature code';
COMMENT ON COLUMN osm_attractions_staging.fclass IS 'Original OSM feature class classification (e.g., restaurant, museum, peak)';
COMMENT ON COLUMN osm_attractions_staging.name IS 'Display name of the POI';
COMMENT ON COLUMN osm_attractions_staging.category IS 'Standardized Vietnamese category (e.g., Nhà hàng, Quán cà phê, Bảo tàng)';
COMMENT ON COLUMN osm_attractions_staging.latitude IS 'WGS 84 latitude coordinate (Y)';
COMMENT ON COLUMN osm_attractions_staging.longitude IS 'WGS 84 longitude coordinate (X)';
COMMENT ON COLUMN osm_attractions_staging.address IS 'Derived address string (never null)';
COMMENT ON COLUMN osm_attractions_staging.description IS 'Factual descriptive text derived from OSM metadata';
COMMENT ON COLUMN osm_attractions_staging.price IS 'Ticket/entry price in VND (explicitly NULL if unknown in OSM)';
COMMENT ON COLUMN osm_attractions_staging.opening_hours IS 'Operating hours (explicitly NULL if unknown in OSM)';
COMMENT ON COLUMN osm_attractions_staging.phone_number IS 'Contact phone number (explicitly NULL if unknown in OSM)';
COMMENT ON COLUMN osm_attractions_staging.image_url IS 'Thumbnail/image URL (explicitly NULL if unknown in OSM)';
COMMENT ON COLUMN osm_attractions_staging.tags IS 'JSON array of searchable keywords and tags';
COMMENT ON COLUMN osm_attractions_staging.raw_properties IS 'Raw attribute dictionary extracted from source DBF';
COMMENT ON COLUMN osm_attractions_staging.imported_to_attraction IS 'Flag indicating whether this POI has been promoted to core Attractions';
COMMENT ON COLUMN osm_attractions_staging.attraction_id IS 'Internal UUID in Attractions table when promoted';
