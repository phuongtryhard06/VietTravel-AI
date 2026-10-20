# Database Migration: Notifications Table

This migration adds the `Notifications` table to support the automatic itinerary reminder system.

## 📋 Table Structure

The `Notifications` table includes the following columns:

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PRIMARY KEY | Unique identifier |
| `user_id` | INTEGER | Foreign key to Users table |
| `itinerary_id` | INTEGER | Foreign key to Itineraries table |
| `title` | VARCHAR(255) | Notification title |
| `message` | TEXT | Notification message content |
| `notification_type` | VARCHAR(50) | Type of notification (default: 'itinerary_reminder') |
| `is_read` | BOOLEAN | Whether notification has been read |
| `scheduled_for` | TIMESTAMP | When notification should be sent |
| `sent_at` | TIMESTAMP | When notification was actually sent |
| `created_at` | TIMESTAMP | When notification was created |
| `is_deleted` | BOOLEAN | Soft delete flag |

## 🚀 Running the Migration

### Option 1: Using Python Script (Recommended)

```bash
# Make sure you have psycopg2 installed
pip install psycopg2-binary

# Run the migration script
python migrate_notifications.py
```

### Option 2: Using SQL File Directly

```bash
# Connect to your PostgreSQL database
psql -h your_host -U your_user -d your_database -f migrations/create_notifications_table.sql
```

### Option 3: Using pgAdmin or DBeaver

1. Open your database management tool
2. Connect to your PostgreSQL database
3. Open the SQL file: `migrations/create_notifications_table.sql`
4. Execute the script

## 📊 Indexes Created

The migration creates the following indexes for optimal performance:

- `idx_notifications_user_id` - For querying user notifications
- `idx_notifications_scheduled_for` - For scheduler queries
- `idx_notifications_type` - For filtering by notification type
- `idx_notifications_deleted` - For filtering deleted notifications
- `idx_notifications_sent_at` - For scheduler queries

## 🔧 Additional Features

### Views
- `pending_notifications` - View for notifications that are due to be sent

### Functions
- `get_user_notifications(user_id, limit)` - Get notifications for a specific user
- `mark_notification_read(notification_id, user_id)` - Mark notification as read
- `delete_notification(notification_id, user_id)` - Soft delete notification

## ✅ Verification

After running the migration, you can verify the table was created correctly:

```sql
-- Check table structure
\d "Notifications"

-- Check indexes
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'Notifications';

-- Check if sample data was inserted (if using Python script)
SELECT * FROM "Notifications" LIMIT 5;
```

## 🔄 Rollback (if needed)

If you need to rollback the migration:

```sql
-- Drop the view and functions first
DROP VIEW IF EXISTS pending_notifications;
DROP FUNCTION IF EXISTS get_user_notifications(INTEGER, INTEGER);
DROP FUNCTION IF EXISTS mark_notification_read(INTEGER, INTEGER);
DROP FUNCTION IF EXISTS delete_notification(INTEGER, INTEGER);

-- Drop the table
DROP TABLE IF EXISTS "Notifications";
```

## 📝 Next Steps

After running the migration:

1. **Restart your Flask application** - The notification system will be automatically enabled
2. **Test the system** - Run `python test_notification.py` to verify everything works
3. **Monitor logs** - Check application logs for notification scheduler activity

## 🐛 Troubleshooting

### Common Issues

1. **Permission denied**: Make sure your database user has CREATE TABLE permissions
2. **Foreign key constraint fails**: Ensure Users and Itineraries tables exist
3. **Connection error**: Check your database connection settings in `src/config/config.py`

### Error Messages

- `table "Notifications" already exists` - The migration has already been run
- `relation "Users" does not exist` - Run user migration first
- `relation "Itineraries" does not exist` - Run itinerary migration first

## 📞 Support

If you encounter any issues, check:
1. Database connection settings
2. User permissions
3. Application logs
4. PostgreSQL error logs 