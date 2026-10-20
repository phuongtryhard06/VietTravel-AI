import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Database
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_POSTGRESQL_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # PostgreSQL configuration
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }
    
    # JWT
    SECRET_KEY = os.getenv('SECRET_KEY')
    
    # Email
    MAIL_SERVER = os.getenv('MAIL_SERVER') or os.getenv('SMTP_HOST')
    MAIL_PORT = int(os.getenv('MAIL_PORT') or os.getenv('SMTP_PORT', 587))
    MAIL_USE_TLS = (os.getenv('MAIL_USE_TLS') or os.getenv('SMTP_TLS', 'True')).lower() == 'true'
    MAIL_USERNAME = os.getenv('MAIL_USERNAME') or os.getenv('SMTP_USER')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD') or os.getenv('SMTP_PASSWORD')
    _from_name = os.getenv('SMTP_FROM_NAME') or os.getenv('MAIL_DEFAULT_SENDER_NAME') or 'Vietnam Travel Assistant'
    _from_email = os.getenv('MAIL_DEFAULT_SENDER') or os.getenv('SMTP_FROM_EMAIL') or os.getenv('MAIL_USERNAME')
    MAIL_DEFAULT_SENDER = (_from_name, _from_email) if _from_name and _from_email else _from_email
    
    # Frontend
    FRONTEND_URL = os.getenv('FRONTEND_URL') 

    # Database
    DB_HOST = os.getenv('DB_HOST')
    DB_NAME = os.getenv('DB_NAME')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_PORT = os.getenv('DB_PORT')