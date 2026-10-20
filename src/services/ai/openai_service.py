import openai
import os
import logging
from typing import Optional, Dict
from langdetect import detect, LangDetectException

# Initialize logger
logger = logging.getLogger(__name__)

class OpenAIService:
    def __init__(self):
        openai.api_key = os.getenv('OPENAI_API_KEY')
        logger.info("OpenAI service initialized")
    
    def detect_language(self, text: str) -> str:
        """
        Detect the language of the input text. Only supports Vietnamese, English, Korean,
        Japanese, Chinese, French, and Russian. Defaults to English for other languages.
        
        Args:
            text (str): The text to detect language from
            
        Returns:
            str: Language code (e.g., 'vi' for Vietnamese, 'en' for English)
        """
        SUPPORTED_LANGUAGES = {
            'vi': 'Vietnamese',
            'en': 'English',
            'ko': 'Korean',
            'ja': 'Japanese',
            'zh': 'Chinese',
            'fr': 'French',
            'ru': 'Russian'
        }
        
        try:
            lang = detect(text)
            logger.info(f"Detected language: {lang}")
            
            # If detected language is supported, return it
            if lang in SUPPORTED_LANGUAGES:
                return lang
                
            # For Chinese, we need to handle both 'zh-cn' and 'zh-tw'
            if lang.startswith('zh'):
                return 'zh'
                
            # Default to English for unsupported languages
            logger.info(f"Unsupported language detected: {lang}, defaulting to English")
            return 'en'
            
        except LangDetectException as e:
            logger.error(f"Error detecting language: {str(e)}")
            return 'en'  # Default to English if detection fails
    
    def get_system_prompt(self, language: str) -> str:
        """
        Get the appropriate system prompt based on the detected language
        
        Args:
            language (str): Language code
            
        Returns:
            str: System prompt in the appropriate language
        """
        if language == 'vi':
            return """Bạn là một trợ lý du lịch thân thiện và hữu ích. Vai trò của bạn là:
            1. Duy trì ngữ cảnh cuộc trò chuyện và ghi nhớ thông tin người dùng đã cung cấp
            2. Đưa ra các đề xuất du lịch cụ thể và có thể thực hiện được dựa trên thông tin đã có
            3. Chỉ hỏi thêm thông tin quan trọng còn thiếu khi thực sự cần thiết
            4. Khi có đủ thông tin, cung cấp lịch trình chi tiết bao gồm:
               - Lịch trình theo ngày
               - Các địa điểm và hoạt động cụ thể
               - Chi phí ước tính
               - Phương tiện di chuyển
               - Nơi lưu trú được đề xuất
               - Mẹo và thông tin địa phương
            5. Nếu cần thêm thông tin, chỉ hỏi MỘT câu hỏi cụ thể tại một thời điểm
            6. Luôn xác nhận thông tin người dùng đã cung cấp
            7. Giữ câu trả lời tập trung và thực tế"""
        else:
            return """You are a friendly and helpful travel assistant. Your role is to:
            1. Maintain conversation context and remember information provided by users
            2. Provide specific, actionable travel recommendations based on the information you have
            3. Only ask for missing critical information that is absolutely necessary for making recommendations
            4. When you have enough information, provide a detailed itinerary including:
               - Day-by-day schedule
               - Specific attractions and activities
               - Estimated costs
               - Transportation options
               - Recommended accommodations
               - Local tips and insights
            5. If you need more information, ask only ONE specific question at a time
            6. Always acknowledge the information the user has already provided
            7. Keep responses focused and practical"""
    
    def generate_title(self, message: str, language: str = 'vi') -> str:
        try:
            system_prompt = """You are a helpful assistant that creates concise, descriptive titles for travel-related conversations.
            Create a short title (maximum 100 characters) that captures the main topic of the user's travel query.
            The title should be in the same language as the user's message and focus on the key aspects of their travel request."""
            
            if language == 'vi':
                system_prompt += """
                Examples in Vietnamese:
                - "Tư vấn du lịch Đà Nẵng 3 ngày 2 đêm"
                - "Địa điểm vui chơi phù hợp gia đình tại Hà Nội"
                - "Lịch trình khám phá Sài Gòn cuối tuần"
                Return only the title text, no additional explanation."""
            else:
                system_prompt += """
                Examples in English:
                - "3-day Da Nang travel guide"
                - "Family-friendly attractions in Hanoi"
                - "Weekend exploration of Saigon"
                Return only the title text, no additional explanation."""
            
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                temperature=0.7,
                max_tokens=100
            )
            
            title = response.choices[0].message.content.strip()
            logger.info(f"Generated title: {title}")
            return title
            
        except Exception as e:
            logger.error(f"Error generating title: {str(e)}")
            return "Travel Consultation" if language == 'en' else "Tư vấn du lịch"
    
    def generate_response(self, message):
        try:
            # Detect language
            language = self.detect_language(message)
            logger.info(f"Detected language: {language}")
            
            # Generate title for the conversation
            title = self.generate_title(message, language)
            
            # Get appropriate system prompt
            system_prompt = self.get_system_prompt(language)
            
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                temperature=0.7,
                max_tokens=2000
            )
            
            response_text = response.choices[0].message.content
            logger.info("Received response from OpenAI")
            
            return {
                'text': response_text,
                'title': title
            }
        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            error_message = f"Sorry, I encountered an error: {str(e)}" if language == 'en' else f"Xin lỗi, tôi đã gặp lỗi: {str(e)}"
            return {
                'text': error_message,
                'title': "Travel Consultation" if language == 'en' else "Tư vấn du lịch"
            } 