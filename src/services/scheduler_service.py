import threading
import time
from datetime import datetime, timedelta
from src.services.email_service import send_notification_email
from src.models.itinerary import Itinerary
from src.models.user import User
from src.models.base import db

class ItineraryReminderScheduler:
    def __init__(self):
        self.running = False
        self.thread = None
        self.check_interval = 300  # Check every 5 minutes (300 seconds)
        self.processed_count = 0
        self.error_count = 0
        self.last_check_time = None
        
    def start(self):
        """Start the itinerary reminder scheduler"""
        if self.running:
            print("🔔 Itinerary Reminder Scheduler is already running")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.thread.start()
        print("🔔 Itinerary Reminder Scheduler started successfully")
        print(f"   📅 Check interval: {self.check_interval} seconds ({self.check_interval//60} minutes)")
        print(f"   🕐 Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   🎯 Function: Check future itineraries and send reminder emails")
        
    def stop(self):
        """Stop the itinerary reminder scheduler"""
        if not self.running:
            print("🔔 Scheduler is not running")
            return
            
        self.running = False
        if self.thread:
            self.thread.join()
        
        print("🔔 Itinerary Reminder Scheduler stopped")
        print(f"   📊 Total reminders sent: {self.processed_count}")
        print(f"   ❌ Total errors: {self.error_count}")
        print(f"   🕐 Stopped at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
    def _run_scheduler(self):
        """Main scheduler loop"""
        print("🔄 Scheduler loop started")
        
        while self.running:
            try:
                current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"\n🔍 [{current_time}] Checking for upcoming itineraries...")
                
                self._check_and_send_reminders()
                
                # Wait for next check
                print(f"⏳ Waiting {self.check_interval} seconds until next check...")
                time.sleep(self.check_interval)
                
            except Exception as e:
                self.error_count += 1
                print(f"❌ Critical error in scheduler loop: {e}")
                print(f"   🔄 Retrying in {self.check_interval} seconds...")
                time.sleep(self.check_interval)
                
    def _check_and_send_reminders(self):
        """Check for upcoming itineraries and send reminder emails"""
        try:
            # Create Flask app context
            from src import create_app
            app = create_app()
            
            with app.app_context():
                print("   🔧 Flask app context created successfully")
                
                # Get current date and tomorrow's date
                today = datetime.now().date()
                tomorrow = today + timedelta(days=1)
                
                print(f"   📅 Checking itineraries for: {tomorrow.strftime('%Y-%m-%d')}")
                
                # Find itineraries for tomorrow (future itineraries)
                upcoming_itineraries = Itinerary.query.filter(
                    Itinerary.selected_date == tomorrow,
                    Itinerary.is_deleted == False
                ).all()
                
                print(f"   📋 Found {len(upcoming_itineraries)} upcoming itineraries for tomorrow")
                
                if len(upcoming_itineraries) == 0:
                    print("   ✅ No upcoming itineraries to remind")
                    self.last_check_time = datetime.now()
                    return
                
                # Process each upcoming itinerary
                success_count = 0
                failed_count = 0
                
                for itinerary in upcoming_itineraries:
                    try:
                        print(f"   📧 Processing itinerary {itinerary.id}:")
                        print(f"      👤 User ID: {itinerary.user_id}")
                        print(f"      📅 Travel Date: {itinerary.selected_date}")
                        print(f"      📝 Title: {itinerary.title or 'No title'}")
                        
                        # Get user details
                        user = User.query.get(itinerary.user_id)
                        if not user:
                            print(f"      ❌ User {itinerary.user_id} not found")
                            failed_count += 1
                            self.error_count += 1
                            continue
                        
                        print(f"      👤 User: {user.full_name} ({user.email})")
                        
                        # Prepare email content
                        email_title = f"🚀 Reminder: Your trip tomorrow - {itinerary.selected_date.strftime('%B %d, %Y')}"
                        
                        email_message = f"""Hello {user.full_name}!

🌟 Tomorrow is your big day! You have a planned itinerary for {itinerary.selected_date.strftime('%B %d, %Y')}.

📋 Trip Details:
• Date: {itinerary.selected_date.strftime('%B %d, %Y')}
• Title: {itinerary.title or 'Your Adventure'}
• Notes: {itinerary.notes or 'No additional notes'}

🎯 Don't forget to:
• Check your schedule
• Pack your essentials
• Review your itinerary items
• Set your alarm early

Have a wonderful trip! 🌍✈️

Best regards,
Travel Assistant Chatbot Team"""
                        
                        # Send reminder email
                        success = send_notification_email(
                            user.email,
                            email_title,
                            email_message,
                            user.full_name
                        )
                        
                        if success:
                            success_count += 1
                            self.processed_count += 1
                            print(f"      ✅ Reminder email sent successfully to {user.email}")
                            print(f"      📤 Sent at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                        else:
                            failed_count += 1
                            self.error_count += 1
                            print(f"      ❌ Failed to send reminder email to {user.email}")
                            
                    except Exception as e:
                        failed_count += 1
                        self.error_count += 1
                        print(f"      ❌ Error processing itinerary {itinerary.id}: {e}")
                
                # Summary
                print(f"   📊 Reminder Summary:")
                print(f"      ✅ Successfully sent: {success_count} reminders")
                print(f"      ❌ Failed: {failed_count} reminders")
                print(f"      📈 Total reminders sent this session: {self.processed_count}")
                print(f"      🚨 Total errors this session: {self.error_count}")
                
                self.last_check_time = datetime.now()
                
        except Exception as e:
            self.error_count += 1
            print(f"   ❌ Error creating app context or processing reminders: {e}")
            print(f"   🔧 Error details: {type(e).__name__}: {str(e)}")

# Global scheduler instance
itinerary_reminder_scheduler = ItineraryReminderScheduler()

def start_itinerary_reminder_scheduler():
    """Start the itinerary reminder scheduler"""
    itinerary_reminder_scheduler.start()

def stop_itinerary_reminder_scheduler():
    """Stop the itinerary reminder scheduler"""
    itinerary_reminder_scheduler.stop()

def get_scheduler_status():
    """Get current scheduler status"""
    return {
        'running': itinerary_reminder_scheduler.running,
        'processed_count': itinerary_reminder_scheduler.processed_count,
        'error_count': itinerary_reminder_scheduler.error_count,
        'check_interval': itinerary_reminder_scheduler.check_interval,
        'last_check_time': itinerary_reminder_scheduler.last_check_time.isoformat() if itinerary_reminder_scheduler.last_check_time else None,
        'next_check_time': (itinerary_reminder_scheduler.last_check_time + timedelta(seconds=itinerary_reminder_scheduler.check_interval)).isoformat() if itinerary_reminder_scheduler.last_check_time else None
    }

def create_itinerary_with_reminder(user_id: int, selected_date: str, 
                                 itinerary_items: list, title: str = None, 
                                 notes: str = None) -> tuple[bool, dict | str]:
    """
    Create itinerary (reminder emails will be sent automatically by scheduler)
    
    Args:
        user_id (int): ID of the user
        selected_date (str): Selected date in ISO format
        itinerary_items (list): List of itinerary items
        title (str, optional): Itinerary title
        notes (str, optional): Itinerary notes
        
    Returns:
        tuple: (success: bool, result: dict or str)
    """
    try:
        # Import here to avoid circular imports
        from src.services.Itinerary_service import create_itinerary_with_items
        
        # Create the itinerary
        success, result = create_itinerary_with_items(
            user_id=user_id,
            selected_date=selected_date,
            itinerary_items=itinerary_items,
            title=title,
            notes=notes
        )
        
        if success:
            itinerary_id = result['id']
            print(f"✅ Created itinerary {itinerary_id}")
            print(f"   📅 Reminder email will be sent automatically 1 day before travel")
            print(f"   🕐 Next scheduler check: in {itinerary_reminder_scheduler.check_interval//60} minutes")
        
        return success, result
        
    except Exception as e:
        return False, str(e) 