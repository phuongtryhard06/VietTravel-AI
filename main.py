import sys
from dotenv import load_dotenv
load_dotenv()

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from src import create_app
from src.services.scheduler_service import start_itinerary_reminder_scheduler

app = create_app()

if __name__ == '__main__':
    # Start itinerary reminder scheduler
    start_itinerary_reminder_scheduler()
    app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False, threaded=True)
