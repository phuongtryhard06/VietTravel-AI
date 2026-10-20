from flask_restx import Resource, fields, Namespace
from src.services.auth_service import (
    register_user,
    verify_otp,
    login_user,
    forgot_password,
    reset_password,
    update_user_name,
    resend_register_otp,
    resend_forgot_password_otp
)
from src.services.email_service import send_otp_email
from src.models.base import db

auth_ns = Namespace('auth', description='Authentication operations')

# Define models for Swagger documentation
register_model = auth_ns.model('Register', {
    'email': fields.String(required=True, description='User email'),
    'password': fields.String(required=True, description='User password'),
    'full_name': fields.String(required=True, description='User full name'),
    'language_preference': fields.String(description='User language preference', default='en')
})

verify_otp_model = auth_ns.model('VerifyOTP', {
    'email': fields.String(required=True, description='User email'),
    'otp_code': fields.String(required=True, description='OTP code')
})

login_model = auth_ns.model('Login', {
    'email': fields.String(required=True, description='User email'),
    'password': fields.String(required=True, description='User password')
})

forgot_password_model = auth_ns.model('ForgotPassword', {
    'email': fields.String(required=True, description='User email')
})

reset_password_model = auth_ns.model('ResetPassword', {
    'email': fields.String(required=True, description='User email'),
    'otp_code': fields.String(required=True, description='OTP code'),
    'password': fields.String(required=True, description='New password')
})

user_model = auth_ns.model('User', {
    'id': fields.Integer(description='User ID'),
    'email': fields.String(description='User email'),
    'full_name': fields.String(description='User full name'),
    'language_preference': fields.String(description='User language preference'),
    'is_verified': fields.Boolean(description='Email verification status')
})

login_response = auth_ns.model('LoginResponse', {
    'token': fields.String(description='JWT token'),
    'user': fields.Nested(user_model)
})

update_name_model = auth_ns.model('UpdateName', {
    'user_id': fields.Integer(required=True, description='User ID'),
    'full_name': fields.String(required=True, description='New full name')
})

@auth_ns.route('/register')
class Register(Resource):
    @auth_ns.expect(register_model)
    @auth_ns.response(201, 'Registration initiated. Please check your email for OTP verification.')
    @auth_ns.response(400, 'Missing required fields or email already registered')
    def post(self):
        data = auth_ns.payload
        
        if not all(k in data for k in ('email', 'password', 'full_name')):
            return {'message': 'Missing required fields'}, 400
        
        success, message = register_user(
            data['email'],
            data['password'],
            data['full_name'],
            data.get('language_preference', 'en')
        )
        
        if not success:
            return {'message': message}, 400
        
        db.session.commit()
        return {'message': message}, 201

@auth_ns.route('/verify-otp')
class VerifyOTP(Resource):
    @auth_ns.expect(verify_otp_model)
    @auth_ns.response(200, 'Email verified successfully')
    @auth_ns.response(400, 'Invalid or expired OTP')
    def post(self):
        data = auth_ns.payload
        
        if not all(k in data for k in ('email', 'otp_code')):
            return {'message': 'Missing email or OTP code'}, 400
        
        success, message = verify_otp(data['email'], data['otp_code'], 'register')
        
        if not success:
            return {'message': message}, 400
        
        db.session.commit()
        return {'message': message}

@auth_ns.route('/login')
class Login(Resource):
    @auth_ns.expect(login_model)
    @auth_ns.response(200, 'Login successful', login_response)
    @auth_ns.response(400, 'Missing email or password')
    @auth_ns.response(401, 'Invalid email or password or email not verified')
    def post(self):
        data = auth_ns.payload
        
        if not all(k in data for k in ('email', 'password')):
            return {'message': 'Missing email or password'}, 400
        
        success, result = login_user(data['email'], data['password'])
        
        if not success:
            return {'message': result}, 401
        
        return result

@auth_ns.route('/forgot-password')
class ForgotPassword(Resource):
    @auth_ns.expect(forgot_password_model)
    @auth_ns.response(200, 'Password reset OTP sent')
    @auth_ns.response(404, 'Email not found')
    def post(self):
        data = auth_ns.payload
        
        if 'email' not in data:
            return {'message': 'Email is required'}, 400
        
        success, message = forgot_password(data['email'])
        
        if not success:
            return {'message': message}, 404
        
        db.session.commit()
        return {'message': message}

@auth_ns.route('/verify-reset-otp')
class VerifyResetOTP(Resource):
    @auth_ns.expect(verify_otp_model)
    @auth_ns.response(200, 'OTP verified successfully')
    @auth_ns.response(400, 'Invalid or expired OTP')
    def post(self):
        data = auth_ns.payload
        
        if not all(k in data for k in ('email', 'otp_code')):
            return {'message': 'Missing email or OTP code'}, 400
        
        success, message = verify_otp(data['email'], data['otp_code'], 'reset_password')
        
        if not success:
            return {'message': message}, 400
        
        db.session.commit()
        return {'message': message}

@auth_ns.route('/reset-password')
class ResetPassword(Resource):
    @auth_ns.expect(reset_password_model)
    @auth_ns.response(200, 'Password has been reset successfully')
    @auth_ns.response(400, 'Invalid request or OTP')
    def post(self):
        data = auth_ns.payload
        
        if not all(k in data for k in ('email', 'otp_code', 'password')):
            return {'message': 'Missing required fields'}, 400
        
        success, message = reset_password(data['email'], data['otp_code'], data['password'])
        
        if not success:
            return {'message': message}, 400
        
        db.session.commit()
        return {'message': message}

@auth_ns.route('/update-username')
class UpdateName(Resource):
    @auth_ns.expect(update_name_model)
    @auth_ns.response(200, 'User name updated successfully')
    @auth_ns.response(404, 'User not found')
    def put(self):
        data = auth_ns.payload
        if not all(k in data for k in ('user_id', 'full_name')):
            return {'message': 'Missing required fields'}, 400
        success, message = update_user_name(data['user_id'], data['full_name'])
        if not success:
            return {'message': message}, 404
        return {'message': message}, 200

@auth_ns.route('/resend-register-otp')
class ResendRegisterOTP(Resource):
    @auth_ns.expect(forgot_password_model)
    @auth_ns.response(200, 'OTP resent successfully')
    @auth_ns.response(404, 'User not found or already verified')
    def post(self):
        data = auth_ns.payload
        if 'email' not in data:
            return {'message': 'Email is required'}, 400
        success, message = resend_register_otp(data['email'])
        if not success:
            return {'message': message}, 404
        return {'message': message}, 200

@auth_ns.route('/resend-forgot-password-otp')
class ResendForgotPasswordOTP(Resource):
    @auth_ns.expect(forgot_password_model)
    @auth_ns.response(200, 'OTP resent successfully')
    @auth_ns.response(404, 'User not found')
    def post(self):
        data = auth_ns.payload
        if 'email' not in data:
            return {'message': 'Email is required'}, 400
        success, message = resend_forgot_password_otp(data['email'])
        if not success:
            return {'message': message}, 404
        return {'message': message}, 200 