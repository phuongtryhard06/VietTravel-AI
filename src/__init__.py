from flask import Flask
from flask_restx import Api
from flask_cors import CORS
from flask_mail import Mail
from src.models.base import db
from src.config.config import Config

# Import all models to ensure they are registered with SQLAlchemy
from src.models.user import User
from src.models.attraction import Attraction
from src.models.message import Message
from src.models.conversation import Conversation
from src.models.otp import OTP
from src.models.itinerary import Itinerary
from src.models.itinerary_item import ItineraryItem
from src.models.notification import Notification

mail = Mail()

def create_app():
    app = Flask(__name__)
    CORS(app)
    
    # Load configuration
    app.config.from_object(Config)
    
    # Configure API
    api = Api(
        title='Travel Assistant API',
        version='1.0',
        description='A travel recommendation system for Vietnamese destinations',
        doc='/docs'
    )
    
    # Initialize extensions
    db.init_app(app)
    mail.init_app(app)
    
    from flask import render_template
    
    @app.route('/')
    def index():
        return render_template('index.html')

    # Register API
    api.init_app(app)
    
    # Add namespaces
    from src.controllers.auth_controller import auth_ns
    from src.controllers.chatting_controller import chatting_ns
    from src.controllers.travel_chatbot_controller import travel_chatbot_ns
    from src.controllers.scape_controller import scape_ns
    from src.controllers.map_controller import map_ns
    from src.controllers.Itinerary_controller import itinerary_ns
    from src.controllers.notification_controller import notification_ns
    
    api.add_namespace(auth_ns, path='/api/auth')
    api.add_namespace(chatting_ns, path='/api/chatting')
    api.add_namespace(travel_chatbot_ns, path='/api/travel-chatbot')
    api.add_namespace(scape_ns, path='/api/scape')
    api.add_namespace(map_ns, path='/api/map')
    api.add_namespace(itinerary_ns, path='/api/itinerary')
    api.add_namespace(notification_ns, path='/api/notification')
    

    # Create database tables
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            app.logger.warning(f"Could not connect to database on startup: {e}")
    
    return app 