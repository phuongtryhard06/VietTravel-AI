import speech_recognition as sr
from langdetect import detect
import os
import logging
from typing import Dict, Tuple

# Initialize logger
logger = logging.getLogger(__name__)

class SpeechService:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        # Define supported languages and their codes
        self.supported_languages = {
            'en': 'en-US',  # English
            'vi': 'vi-VN',  # Vietnamese
            'fr': 'fr-FR',  # French
            'de': 'de-DE',  # German
            'es': 'es-ES',  # Spanish
            'it': 'it-IT',  # Italian
            'ja': 'ja-JP',  # Japanese
            'ko': 'ko-KR',  # Korean
            'zh': 'zh-CN',  # Chinese
            'ru': 'ru-RU'   # Russian
        }
        logger.info("Speech service initialized")
    
    def convert_speech_to_text(self, audio_file_path: str) -> Tuple[bool, Dict]:
        """
        Convert speech to text and detect language
        
        Args:
            audio_file_path (str): Path to the audio file
            
        Returns:
            tuple: (success: bool, result: dict)
        """
        try:
            # Check if file exists
            if not os.path.exists(audio_file_path):
                return False, {"error": "Audio file not found"}
            
            # Load audio file
            with sr.AudioFile(audio_file_path) as source:
                audio_data = self.recognizer.record(source)
            
            # Try to detect language by attempting recognition with different languages
            detected_lang = None
            best_text = None
            
            # First try with Vietnamese as it's the primary language
            try:
                text = self.recognizer.recognize_google(audio_data, language='vi-VN')
                detected_lang = 'vi'
                best_text = text
            except:
                pass
            
            # If Vietnamese fails, try other languages
            if not detected_lang:
                for lang_code, google_lang in self.supported_languages.items():
                    try:
                        text = self.recognizer.recognize_google(audio_data, language=google_lang)
                        # If we get a result, verify it's not just noise
                        if len(text.strip()) > 0:
                            detected_lang = lang_code
                            best_text = text
                            break
                    except:
                        continue
            
            # If no language detected, try one last time with auto language detection
            if not detected_lang:
                try:
                    text = self.recognizer.recognize_google(audio_data)
                    if len(text.strip()) > 0:
                        # Try to detect the language of the text
                        detected_lang = detect(text)
                        best_text = text
                except:
                    pass
            
            # If still no result, return error
            if not detected_lang or not best_text:
                return False, {"error": "Could not detect language or recognize speech"}
            
            return True, {
                "text": best_text,
                "detected_language": detected_lang
            }
            
        except sr.UnknownValueError:
            return False, {"error": "Speech recognition could not understand audio"}
        except sr.RequestError as e:
            return False, {"error": f"Could not request results from speech recognition service: {str(e)}"}
        except Exception as e:
            return False, {"error": f"Error processing audio: {str(e)}"}
    
    def _get_language_code(self, lang: str) -> str:
        """
        Map language code to Google Speech Recognition language code
        
        Args:
            lang (str): Language code from langdetect
            
        Returns:
            str: Google Speech Recognition language code
        """
        return self.supported_languages.get(lang, 'en-US') 