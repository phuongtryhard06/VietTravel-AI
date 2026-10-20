from flask_restx import Resource, fields, Namespace, reqparse
from werkzeug.datastructures import FileStorage
from flask import request
from src.services.chatting_service import create_conversation, get_user_conversations, get_conversation_messages, save_message, end_conversation, save_message_update
from src.services.ai.speech_service import SpeechService
from werkzeug.utils import secure_filename
import os
from src import db

chatting_ns = Namespace('chatting', description='Chatting operations')

# Define models for Swagger documentation
conversation_create_model = chatting_ns.model('ConversationCreate', {
    'user_id': fields.Integer(required=True, description='ID of the user starting the conversation'),
    'source_language': fields.String(description='Source language for the conversation', default='en'),
    'started_at': fields.DateTime(description='Start time of the conversation (default: current UTC time)'),
    'title': fields.String(description='Title of the conversation')
})

conversation_response_model = chatting_ns.model('ConversationResponse', {
    'conversation_id': fields.Integer(description='ID of the created conversation'),
    'user_id': fields.Integer(description='ID of the user'),
    'source_language': fields.String(description='Source language of the conversation'),
    'started_at': fields.DateTime(description='Start time of the conversation'),
    'ended_at': fields.DateTime(description='End time of the conversation (null if ongoing)'),
    'title': fields.String(description='Title of the conversation')
})

message_response_model = chatting_ns.model('MessageResponse', {
    'message_id': fields.Integer(description='ID of the message'),
    'conversation_id': fields.Integer(description='ID of the conversation'),
    'content': fields.String(description='Content of the message'),
    'role': fields.String(description='Role of the message sender (user/assistant)'),
    'created_at': fields.DateTime(description='Time when the message was created')
})

message_create_model = chatting_ns.model('MessageCreate', {
    'conversation_id': fields.Integer(required=True, description='ID of the conversation'),
    'sender': fields.String(required=True, description='Sender of the message (bot or user)'),
    'message_text': fields.String(required=True, description='Content of the message'),
    'translated_text': fields.String(description='Translated text of the message'),
    'message_type': fields.String(description='Type of the message', default='text'),
    'voice_url': fields.String(description='URL of the voice message if any')
})

message_update_create_model = chatting_ns.model('MessageUpdateCreate', {
    'conversation_id': fields.Integer(required=True, description='ID of the conversation'),
    'sender': fields.String(required=True, description='Sender of the message (bot or user)'),
    'message_text': fields.String(required=True, description='Content of the message'),
    'translated_text': fields.String(description='Translated text of the message'),
    'message_type': fields.String(description='Type of the message', default='text'),
    'voice_url': fields.String(description='URL of the voice message if any')
})

voice_message_parser = reqparse.RequestParser()
voice_message_parser.add_argument('conversation_id', type=int, required=True, help='ID of the conversation')
voice_message_parser.add_argument('sender', type=str, required=True, help='Sender of the message (must be user)')
voice_message_parser.add_argument('audio', type=FileStorage, location='files', required=True, help='Audio file (WAV, AIFF, FLAC)')

user_message_model = chatting_ns.model('UserMessage', {
    'message_id': fields.Integer(description='ID of the message'),
    'conversation_id': fields.Integer(description='ID of the conversation'),
    'sender': fields.String(description='Sender of the message'),
    'message_text': fields.String(description='Content of the message'),
    'translated_text': fields.String(description='Translated text of the message'),
    'message_type': fields.String(description='Type of the message'),
    'voice_url': fields.String(description='URL of the voice message'),
    'sent_at': fields.DateTime(description='Time when the message was sent')
})

bot_message_model = chatting_ns.model('BotMessage', {
    'message_id': fields.Integer(description='ID of the message'),
    'conversation_id': fields.Integer(description='ID of the conversation'),
    'sender': fields.String(description='Sender of the message'),
    'message_text': fields.String(description='Content of the message'),
    'message_type': fields.String(description='Type of the message'),
    'sent_at': fields.DateTime(description='Time when the message was sent')
})

message_save_response = chatting_ns.model('MessageSaveResponse', {
    'status': fields.String(description='Status of the response'),
    'message': fields.String(description='Response message'),
    'data': fields.Nested(chatting_ns.model('MessageData', {
        'user_message': fields.Nested(user_message_model),
        'bot_message': fields.Nested(bot_message_model),
    }))
})

message_save_update_response = chatting_ns.model('MessageSaveUpdateResponse', {
    'status': fields.String(description='Status of the response'),
    'message': fields.String(description='Response message'),
    'data': fields.Nested(chatting_ns.model('MessageData', {
        'user_message': fields.Nested(user_message_model),
        'bot_message': fields.Nested(bot_message_model),
        'travel_data': fields.Raw(description='Travel chatbot data if applicable')
    }))
})

success_response = chatting_ns.model('SuccessResponse', {
    'status': fields.String(description='Status of the response'),
    'message': fields.String(description='Response message'),
    'data': fields.Nested(conversation_response_model)
})

conversations_list_response = chatting_ns.model('ConversationsListResponse', {
    'status': fields.String(description='Status of the response'),
    'message': fields.String(description='Response message'),
    'data': fields.List(fields.Nested(conversation_response_model))
})

messages_list_response = chatting_ns.model('MessagesListResponse', {
    'status': fields.String(description='Status of the response'),
    'message': fields.String(description='Response message'),
    'data': fields.List(fields.Nested(message_response_model))
})

# Create parser for query parameters
parser = reqparse.RequestParser()
parser.add_argument('user_id', type=int, required=True, help='ID of the user')

conversation_parser = reqparse.RequestParser()
conversation_parser.add_argument('conversation_id', type=int, required=True, help='ID of the conversation')

# Configure upload folder
UPLOAD_FOLDER = 'uploads/voice_messages'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

@chatting_ns.route('/conversations')
class ConversationResource(Resource):
    @chatting_ns.expect(conversation_create_model)
    @chatting_ns.response(201, 'Conversation created successfully', success_response)
    @chatting_ns.response(400, 'Invalid request data')
    @chatting_ns.response(500, 'Internal server error')
    def post(self):
        """Create a new conversation"""
        data = chatting_ns.payload
        
        if 'user_id' not in data:
            return {'message': 'User ID is required'}, 400
            
        success, result = create_conversation(
            user_id=data['user_id'],
            source_language=data.get('source_language', 'en'),
            started_at=data.get('started_at'),
            title=data.get('title')
        )
        
        if not success:
            return {'message': f'Failed to create conversation: {result}'}, 500
            
        return {
            'status': 'success',
            'message': 'Conversation created successfully',
            'data': result
        }, 201

@chatting_ns.route('/conversations/list')
class ConversationsListResource(Resource):
    @chatting_ns.expect(parser)
    @chatting_ns.response(200, 'Successfully retrieved conversations', conversations_list_response)
    @chatting_ns.response(400, 'Invalid request data')
    @chatting_ns.response(500, 'Internal server error')
    def get(self):
        """Get all conversations for a specific user"""
        args = parser.parse_args()
        user_id = args['user_id']
        
        success, result = get_user_conversations(user_id)
        
        if not success:
            return {'message': f'Failed to get conversations: {result}'}, 500
            
        return {
            'status': 'success',
            'message': 'Successfully retrieved conversations',
            'data': result
        }

@chatting_ns.route('/conversations/messages')
class ConversationMessagesResource(Resource):
    @chatting_ns.expect(conversation_parser)
    @chatting_ns.response(200, 'Successfully retrieved messages', messages_list_response)
    @chatting_ns.response(400, 'Invalid request data')
    @chatting_ns.response(404, 'Conversation not found')
    @chatting_ns.response(500, 'Internal server error')
    def get(self):
        """Get all messages for a specific conversation"""
        args = conversation_parser.parse_args()
        conversation_id = args['conversation_id']
        
        success, result = get_conversation_messages(conversation_id)
        
        if not success:
            if result == "Conversation not found":
                return {'message': result}, 404
            return {'message': f'Failed to get messages: {result}'}, 500
            
        return {
            'status': 'success',
            'message': 'Successfully retrieved messages',
            'data': result
        }

@chatting_ns.route('/messages')
class MessageResource(Resource):
    @chatting_ns.expect(message_create_model)
    @chatting_ns.response(201, 'Message saved successfully', message_save_response)
    @chatting_ns.response(400, 'Invalid request data')
    @chatting_ns.response(404, 'Conversation not found')
    @chatting_ns.response(500, 'Internal server error')
    def post(self):
        """Save a new message"""
        data = chatting_ns.payload
        
        # Validate required fields
        required_fields = ['conversation_id', 'sender', 'message_text']
        if not all(field in data for field in required_fields):
            return {'message': 'Missing required fields'}, 400
            
        success, result = save_message(
            conversation_id=data['conversation_id'],
            sender=data['sender'],
            message_text=data['message_text'],
            translated_text=data.get('translated_text'),
            message_type=data.get('message_type', 'text'),
            voice_url=data.get('voice_url')
        )
        
        if not success:
            if result == "Conversation not found":
                return {'message': result}, 404
            return {'message': f'Failed to save message: {result}'}, 500
            
        return {
            'status': 'success',
            'message': 'Message saved successfully',
            'data': result
        }, 201

@chatting_ns.route('/messages/update')
class MessageResource(Resource):
    @chatting_ns.expect(message_update_create_model)
    @chatting_ns.response(201, 'Message saved successfully', message_save_update_response)
    @chatting_ns.response(400, 'Invalid request data')
    @chatting_ns.response(404, 'Conversation not found')
    @chatting_ns.response(500, 'Internal server error')
    def post(self):
        """Save a new message update"""
        data = chatting_ns.payload
        
        # Validate required fields
        required_fields = ['conversation_id', 'sender', 'message_text']
        if not all(field in data for field in required_fields):
            return {'message': 'Missing required fields'}, 400
            
        success, result = save_message_update(
            conversation_id=data['conversation_id'],
            sender=data['sender'],
            message_text=data['message_text'],
            translated_text=data.get('translated_text'),
            message_type=data.get('message_type', 'text'),
            voice_url=data.get('voice_url')
        )
        
        if not success:
            if result == "Conversation not found":
                return {'message': result}, 404
            return {'message': f'Failed to save message: {result}'}, 500
            
        return {
            'status': 'success',
            'message': 'Message saved successfully',
            'data': result
        }, 201

@chatting_ns.route('/messages/voice')
class VoiceMessageResource(Resource):
    @chatting_ns.expect(voice_message_parser)
    @chatting_ns.response(201, 'Voice message processed successfully', message_save_update_response)
    @chatting_ns.response(400, 'Invalid request data')
    @chatting_ns.response(404, 'Conversation not found')
    @chatting_ns.response(500, 'Internal server error')
    def post(self):
        """Process a voice message
        
        Upload a voice message and get AI response.
        Supported audio formats: WAV, AIFF, FLAC
        
        Example request:
        ```
        curl -X POST "http://localhost:5000/api/chatting/messages/voice" \
             -H "accept: application/json" \
             -H "Content-Type: multipart/form-data" \
             -F "conversation_id=55" \
             -F "sender=user" \
             -F "audio=@/path/to/audio.wav"
        ```
        """
        try:
            # Get data from form data
            args = voice_message_parser.parse_args()
            conversation_id = args.get('conversation_id')
            sender = args.get('sender')
            audio_file = args.get('audio')
            
            # Validate required fields
            if not conversation_id or not sender or not audio_file:
                return {'message': 'Missing required fields'}, 400
            
            # Validate sender
            if sender != 'user':
                return {'message': 'Only user can send voice messages'}, 400
            
            # Save audio file
            filename = secure_filename(audio_file.filename)
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            
            # Ensure the upload directory exists
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            
            # Save the file
            audio_file.save(file_path)
            
            try:
                # Convert speech to text
                speech_service = SpeechService()
                success, result = speech_service.convert_speech_to_text(file_path)
                
                if not success:
                    return {'message': result['error']}, 500
                
                # Save message with transcribed text using save_message_update
                success, message_result = save_message_update(
                    conversation_id=int(conversation_id),
                    sender=sender,
                    message_text=result['text'],
                    message_type='voice',
                    voice_url=file_path
                )
                
                if not success:
                    if message_result == "Conversation not found":
                        return {'message': message_result}, 404
                    return {'message': f'Failed to save message: {message_result}'}, 500
                
                return {
                    'status': 'success',
                    'message': 'Voice message processed successfully',
                    'data': message_result
                }, 201
                
            finally:
                # Clean up audio file
                try:
                    if os.path.exists(file_path):
                        # Close any open file handles
                        audio_file.close()
                        # Wait a bit to ensure file is released
                        import time
                        time.sleep(0.1)
                        # Try to remove the file
                        os.remove(file_path)
                except Exception as e:
                    print(f"Warning: Could not delete temporary file {file_path}: {str(e)}")
                    
        except Exception as e:
            return {'message': f'Error processing voice message: {str(e)}'}, 500

@chatting_ns.route('/conversations/end')
class EndConversationResource(Resource):
    @chatting_ns.expect(conversation_parser)
    @chatting_ns.response(200, 'Conversation ended successfully', success_response)
    @chatting_ns.response(400, 'Invalid request data')
    @chatting_ns.response(404, 'Conversation not found')
    @chatting_ns.response(409, 'Conversation is already ended')
    @chatting_ns.response(500, 'Internal server error')
    def post(self):
        """End a conversation"""
        args = conversation_parser.parse_args()
        conversation_id = args['conversation_id']
        
        success, result = end_conversation(conversation_id)
        
        if not success:
            if result == "Conversation not found":
                return {'message': result}, 404
            if result == "Conversation is already ended":
                return {'message': result}, 409
            return {'message': f'Failed to end conversation: {result}'}, 500
            
        return {
            'status': 'success',
            'message': 'Conversation ended successfully',
            'data': result
        } 