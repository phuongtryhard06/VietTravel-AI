-- Migration: Create Notifications table
-- Description: Add Notifications table for itinerary reminder system
-- Date: 2024

-- Check if table already exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM information_schema.tables 
        WHERE table_schema = 'public' 
        AND table_name = 'Notifications'
    ) THEN
        -- Create Notifications table
        CREATE TABLE "Notifications" (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            itinerary_id INTEGER NOT NULL,
            title VARCHAR(255) NOT NULL,
            message TEXT NOT NULL,
            notification_type VARCHAR(50) DEFAULT 'itinerary_reminder',
            is_read BOOLEAN DEFAULT FALSE,
            scheduled_for TIMESTAMP NOT NULL,
            sent_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_deleted BOOLEAN DEFAULT FALSE,
            
            -- Foreign key constraints
            CONSTRAINT fk_notifications_user 
                FOREIGN KEY (user_id) 
                REFERENCES "Users"(user_id) 
                ON DELETE CASCADE,
                
            CONSTRAINT fk_notifications_itinerary 
                FOREIGN KEY (itinerary_id) 
                REFERENCES "Itineraries"(id) 
                ON DELETE CASCADE
        );
        
        -- Create indexes for better performance
        CREATE INDEX idx_notifications_user_id ON "Notifications"(user_id);
        CREATE INDEX idx_notifications_scheduled_for ON "Notifications"(scheduled_for);
        CREATE INDEX idx_notifications_type ON "Notifications"(notification_type);
        CREATE INDEX idx_notifications_deleted ON "Notifications"(is_deleted);
        CREATE INDEX idx_notifications_sent_at ON "Notifications"(sent_at);
        
        RAISE NOTICE 'Notifications table created successfully with indexes';
    ELSE
        RAISE NOTICE 'Notifications table already exists';
    END IF;
END $$;

-- Add comments to table and columns
COMMENT ON TABLE "Notifications" IS 'Stores user notifications for itinerary reminders and other system notifications';
COMMENT ON COLUMN "Notifications".id IS 'Primary key for notification';
COMMENT ON COLUMN "Notifications".user_id IS 'Foreign key to Users table';
COMMENT ON COLUMN "Notifications".itinerary_id IS 'Foreign key to Itineraries table';
COMMENT ON COLUMN "Notifications".title IS 'Notification title';
COMMENT ON COLUMN "Notifications".message IS 'Notification message content';
COMMENT ON COLUMN "Notifications".notification_type IS 'Type of notification (e.g., itinerary_reminder)';
COMMENT ON COLUMN "Notifications".is_read IS 'Whether the notification has been read by user';
COMMENT ON COLUMN "Notifications".scheduled_for IS 'When the notification should be sent';
COMMENT ON COLUMN "Notifications".sent_at IS 'When the notification was actually sent';
COMMENT ON COLUMN "Notifications".created_at IS 'When the notification was created';
COMMENT ON COLUMN "Notifications".is_deleted IS 'Soft delete flag';

-- Create a view for pending notifications (for scheduler queries)
CREATE OR REPLACE VIEW pending_notifications AS
SELECT 
    id,
    user_id,
    itinerary_id,
    title,
    message,
    notification_type,
    scheduled_for,
    created_at
FROM "Notifications"
WHERE 
    scheduled_for <= CURRENT_TIMESTAMP 
    AND sent_at IS NULL 
    AND is_deleted = FALSE;

COMMENT ON VIEW pending_notifications IS 'View for notifications that are due to be sent';

-- Create a function to get user notifications
CREATE OR REPLACE FUNCTION get_user_notifications(p_user_id INTEGER, p_limit INTEGER DEFAULT 50)
RETURNS TABLE (
    id INTEGER,
    user_id INTEGER,
    itinerary_id INTEGER,
    title VARCHAR(255),
    message TEXT,
    notification_type VARCHAR(50),
    is_read BOOLEAN,
    scheduled_for TIMESTAMP,
    sent_at TIMESTAMP,
    created_at TIMESTAMP,
    is_deleted BOOLEAN
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        n.id,
        n.user_id,
        n.itinerary_id,
        n.title,
        n.message,
        n.notification_type,
        n.is_read,
        n.scheduled_for,
        n.sent_at,
        n.created_at,
        n.is_deleted
    FROM "Notifications" n
    WHERE n.user_id = p_user_id AND n.is_deleted = FALSE
    ORDER BY n.created_at DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_user_notifications(INTEGER, INTEGER) IS 'Get notifications for a specific user';

-- Create a function to mark notification as read
CREATE OR REPLACE FUNCTION mark_notification_read(p_notification_id INTEGER, p_user_id INTEGER)
RETURNS BOOLEAN AS $$
DECLARE
    notification_exists BOOLEAN;
BEGIN
    -- Check if notification exists and belongs to user
    SELECT EXISTS(
        SELECT 1 FROM "Notifications" 
        WHERE id = p_notification_id AND user_id = p_user_id
    ) INTO notification_exists;
    
    IF notification_exists THEN
        UPDATE "Notifications" 
        SET is_read = TRUE 
        WHERE id = p_notification_id;
        RETURN TRUE;
    ELSE
        RETURN FALSE;
    END IF;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION mark_notification_read(INTEGER, INTEGER) IS 'Mark a notification as read for a specific user';

-- Create a function to soft delete notification
CREATE OR REPLACE FUNCTION delete_notification(p_notification_id INTEGER, p_user_id INTEGER)
RETURNS BOOLEAN AS $$
DECLARE
    notification_exists BOOLEAN;
BEGIN
    -- Check if notification exists and belongs to user
    SELECT EXISTS(
        SELECT 1 FROM "Notifications" 
        WHERE id = p_notification_id AND user_id = p_user_id
    ) INTO notification_exists;
    
    IF notification_exists THEN
        UPDATE "Notifications" 
        SET is_deleted = TRUE 
        WHERE id = p_notification_id;
        RETURN TRUE;
    ELSE
        RETURN FALSE;
    END IF;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION delete_notification(INTEGER, INTEGER) IS 'Soft delete a notification for a specific user';

-- Grant necessary permissions (adjust as needed)
-- GRANT SELECT, INSERT, UPDATE, DELETE ON "Notifications" TO your_app_user;
-- GRANT USAGE, SELECT ON SEQUENCE "Notifications_id_seq" TO your_app_user;
-- GRANT SELECT ON pending_notifications TO your_app_user;
-- GRANT EXECUTE ON FUNCTION get_user_notifications(INTEGER, INTEGER) TO your_app_user;
-- GRANT EXECUTE ON FUNCTION mark_notification_read(INTEGER, INTEGER) TO your_app_user;
-- GRANT EXECUTE ON FUNCTION delete_notification(INTEGER, INTEGER) TO your_app_user; 