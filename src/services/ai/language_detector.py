from langdetect import detect
from langdetect.lang_detect_exception import LangDetectException

class LanguageDetector:
    def detect_language(self, text):
        try:
            return detect(text)
        except LangDetectException:
            return 'en'  # Default to English if detection fails 