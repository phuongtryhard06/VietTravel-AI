from datetime import datetime, timedelta
import jwt
import random
import string
from werkzeug.security import generate_password_hash, check_password_hash
from src.models.user import User
from src.models.otp import OTP
from src.config.config import Config
from src.services.email_service import send_otp_email
from src import db

def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

def register_user(email, password, full_name, language_preference='en'):
    if User.query.filter_by(email=email).first():
        return False, 'Email already registered'
    
    # Generate OTP
    otp_code = generate_otp()
    expires_at = datetime.utcnow() + timedelta(seconds=60)
    
    # Save OTP
    otp = OTP(
        email=email,
        otp_code=otp_code,
        purpose='register',
        expires_at=expires_at
    )
    db.session.add(otp)
    db.session.commit()

    # Save user
    hashed_password = generate_password_hash(password)
    new_user = User(
        email=email,
        password_hash=hashed_password,
        full_name=full_name,
        language_preference=language_preference,
        is_verified=False
    )
    db.session.add(new_user)
    db.session.commit()

    send_otp_email(email, otp_code, 'register')
    
    return True, 'Registration initiated. Please check your email for OTP verification.'

def verify_otp(email, otp_code, purpose):
    otp = OTP.query.filter_by(
        email=email,
        otp_code=otp_code,
        purpose=purpose,
        is_used=False
    ).first()
    
    if not otp or otp.expires_at < datetime.utcnow():
        return False, 'Invalid or expired OTP'
    
    otp.is_used = True
    
    if purpose == 'register':
        user = User.query.filter_by(email=email).first()
        user.is_verified = True

    # Delete all OTP records for this email and purpose
        OTP.query.filter_by(
            email=email,
            purpose='register'
        ).delete()
        
    db.session.commit()
    
    return True, 'OTP verified successfully'

def login_user(email, password):
    user = User.query.filter_by(email=email).first()
    
    if not user or not check_password_hash(user.password_hash, password):
        return False, 'Invalid email or password'
    
    if not user.is_verified:
        return False, 'Please verify your email first'
    
    token = jwt.encode({
        'user_id': user.user_id,
        'exp': datetime.utcnow() + timedelta(days=1)
    }, Config.SECRET_KEY)
    
    return True, {
        'token': token,
        'user': {
            'id': user.user_id,
            'email': user.email,
            'full_name': user.full_name,
            'language_preference': user.language_preference,
            'is_verified': user.is_verified
        }
    }

def forgot_password(email):
    user = User.query.filter_by(email=email).first()
    if not user:
        return False, 'Email not found'
    
    otp_code = generate_otp()
    expires_at = datetime.utcnow() + timedelta(minutes=5)
    
    otp = OTP(
        email=email,
        otp_code=otp_code,
        purpose='reset_password',
        expires_at=expires_at
    )
    db.session.add(otp)
    db.session.commit()

    send_otp_email(email, otp_code, 'reset_password')
    
    return True, 'Password reset OTP sent'

def reset_password(email, otp_code, new_password):
    otp = OTP.query.filter_by(
        email=email,
        otp_code=otp_code,
        purpose='reset_password',
        is_used=True
    ).first()
    
    if not otp:
        return False, 'Please verify OTP first'
    
    user = User.query.filter_by(email=email).first()
    user.password_hash = generate_password_hash(new_password)

    # Delete all OTP records for this email and purpose
    OTP.query.filter_by(
        email=email,
        purpose='reset_password'
    ).delete()
    
    db.session.commit()
    
    return True, 'Password has been reset successfully'

def update_user_name(user_id, new_full_name):
    user = User.query.filter_by(user_id=user_id).first()
    if not user:
        return False, 'User not found'
    user.full_name = new_full_name
    db.session.commit()
    return True, 'User name updated successfully'

def resend_register_otp(email):
    user = User.query.filter_by(email=email).first()
    if not user or user.is_verified:
        return False, 'User not found or already verified'
    # Xóa các OTP cũ chưa dùng cho email này
    OTP.query.filter_by(email=email, purpose='register', is_used=False).delete()
    db.session.commit()
    otp_code = generate_otp()
    expires_at = datetime.utcnow() + timedelta(seconds=60)
    otp = OTP(
        email=email,
        otp_code=otp_code,
        purpose='register',
        expires_at=expires_at
    )
    db.session.add(otp)
    db.session.commit()
    send_otp_email(email, otp_code, 'register')
    return True, 'OTP resent successfully'

def resend_forgot_password_otp(email):
    user = User.query.filter_by(email=email).first()
    if not user:
        return False, 'User not found'
    
    # Xóa các OTP cũ chưa dùng cho email này
    OTP.query.filter_by(email=email, purpose='reset_password', is_used=False).delete()
    db.session.commit()
    
    otp_code = generate_otp()
    expires_at = datetime.utcnow() + timedelta(seconds=60)
    
    otp = OTP(
        email=email,
        otp_code=otp_code,
        purpose='reset_password',
        expires_at=expires_at
    )
    db.session.add(otp)
    db.session.commit()
    
    send_otp_email(email, otp_code, 'reset_password')
    return True, 'OTP resent successfully' 