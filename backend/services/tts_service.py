import os
import re
import uuid
import wave
import io
import json
import logging
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai

backend_dir = Path(__file__).resolve().parent.parent
dotenv_path = backend_dir / ".env"
load_dotenv(dotenv_path=dotenv_path)

from services.gemini_service import get_available_gemini_models, classify_gemini_error

logger = logging.getLogger(__name__)

# Constants
STATIC_AUDIO_DIR = backend_dir / "static" / "audio"
DEFAULT_VOICE = "Aoede" # Puck, Charon, Fenrir, Kore, Aoede

def clean_text_for_tts(text: str) -> str:
    """
    Cleans markdown formatting, list indicators, and raw JSON blocks 
    before sending text to the TTS engine.
    """
    if not text:
        return ""
    
    # 1. Clean JSON if accidentally present
    trimmed = text.strip()
    if (trimmed.startswith("{") and trimmed.endswith("}")) or (trimmed.startswith("[") and trimmed.endswith("]")):
        try:
            data = json.loads(trimmed)
            if isinstance(data, dict):
                # Try common keys for message response
                for key in ["farmer_response", "text", "message", "response"]:
                    if key in data and data[key]:
                        return clean_text_for_tts(str(data[key]))
        except Exception:
            pass

    # Remove inline JSON-like strings
    text = re.sub(r'\{[^{}]*\}', '', text)
    
    # 2. Clean markdown bold/italic/code markers
    text = text.replace("**", "").replace("*", "").replace("__", "").replace("_", "")
    text = text.replace("```", "")
    
    # Remove header markers (e.g., "# Heading" -> "Heading")
    text = re.sub(r'#+\s*', '', text)
    
    # Remove bullet/list markers at the beginning of lines
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
    
    # Remove excessive newlines
    text = re.sub(r'\n+', '\n', text)
    return text.strip()


def pcm_to_wav(pcm_data: bytes, sample_rate: int = 24000) -> bytes:
    """Converts raw 16-bit linear PCM bytes into standard WAV bytes."""
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(1)       # Mono
        wav_file.setsampwidth(2)      # 16-bit (2 bytes per sample)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)
    return wav_io.getvalue()


def generate_tts_audio(text: str, language_hint: str = None) -> dict:
    """
    Generates TTS audio from input text using Gemini API.
    Saves file under static/audio/ and returns a status dict.
    """
    # Clean input text
    cleaned_text = clean_text_for_tts(text)
    if not cleaned_text:
        return {
            "success": False,
            "error_type": "empty_input",
            "message": "آواز بنانے کے لیے متن موجود نہیں۔"
        }

    # Trim to reasonable length if excessively long (e.g. limit to 2000 chars to avoid timeout/quota overload)
    if len(cleaned_text) > 2000:
        cleaned_text = cleaned_text[:2000] + "..."

    # Safe debug logs
    logger.info("Final TTS transcript length: %d", len(cleaned_text))
    logger.info("Final TTS transcript snippet: %s", cleaned_text[:120])

    from services.key_manager import run_with_key_rotation

    def _execute_tts(api_key: str) -> dict:
        if not api_key:
            return {
                "success": False,
                "error_type": "missing_api_key",
                "message": "آواز بنانے میں مسئلہ آ رہا ہے، دوبارہ کوشش کریں۔"
            }

        # 1. Model Selection
        env_model = os.getenv("GEMINI_TTS_MODEL", "").strip()
        selected_model = None

        if env_model:
            selected_model = env_model
            logger.info("Using configured GEMINI_TTS_MODEL: %s", selected_model)
        else:
            # Auto-discovery
            available_models = get_available_gemini_models(api_key)
            # Priority list of TTS models
            priority_models = [
                "models/gemini-2.5-flash-preview-tts",
                "models/gemini-3.1-flash-tts-preview",
                "models/gemini-2.5-pro-preview-tts",
                "models/gemini-2.5-flash",
                "models/gemini-2.0-flash",
                "gemini-2.5-flash-preview-tts",
                "gemini-3.1-flash-tts-preview",
                "gemini-2.5-flash",
                "gemini-2.0-flash"
            ]
            
            # Clean model names to find matches
            def clean_name(n: str) -> str:
                return n[7:] if n.startswith("models/") else n
                
            normalized_available = {clean_name(m): m for m in available_models}
            
            for p in priority_models:
                p_clean = clean_name(p)
                if p_clean in normalized_available:
                    selected_model = normalized_available[p_clean]
                    break
                    
            if not selected_model:
                selected_model = "models/gemini-2.5-flash-preview-tts"
                logger.info("Auto-discovery fallback to default model: %s", selected_model)
                
        if not selected_model:
            logger.error("No valid Gemini model available for TTS generation.")
            return {
                "success": False,
                "error_type": "model_not_available",
                "message": "آواز بنانے میں مسئلہ آ رہا ہے، دوبارہ کوشش کریں۔"
            }

        # 2. Build instructions prompt based on language_hint or detected language
        active_lang = language_hint
        if not active_lang:
            # Infer language from text
            from utils.helpers import detect_language
            active_lang = detect_language(cleaned_text)

        full_prompt = f"Read this text aloud clearly in a natural farmer-friendly voice: {cleaned_text}"

        # 3. Call API
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(selected_model)
            
            # Generation config dictionary for audio modalities
            config = {
                "response_modalities": ["AUDIO"],
                "speech_config": {
                    "voice_config": {
                        "prebuilt_voice_config": {
                            "voice_name": DEFAULT_VOICE
                        }
                    }
                }
            }
            
            logger.info("Generating audio using model: %s, lang: %s", selected_model, active_lang)
            response = model.generate_content(
                full_prompt,
                generation_config=config,
                request_options={"timeout": 30.0}
            )
            
            # Extract audio bytes
            pcm_bytes = None
            if response and len(response.candidates) > 0:
                candidate = response.candidates[0]
                if candidate.content and len(candidate.content.parts) > 0:
                    part = candidate.content.parts[0]
                    if hasattr(part, "inline_data") and part.inline_data:
                        pcm_bytes = part.inline_data.data
                        
            if not pcm_bytes:
                logger.error("Model did not return valid inline audio bytes. Response object: %s", response)
                raise ValueError("empty_response: Model did not return valid inline audio bytes.")
                
            # Convert PCM to playable WAV
            wav_bytes = pcm_to_wav(pcm_bytes)
            
            # Ensure static/audio directory exists
            os.makedirs(STATIC_AUDIO_DIR, exist_ok=True)
            
            # Save audio file
            filename = f"tts_{uuid.uuid4().hex}.wav"
            file_path = os.path.join(STATIC_AUDIO_DIR, filename)
            with open(file_path, "wb") as f:
                f.write(wav_bytes)
                
            logger.info("Saved generated TTS audio: %s", file_path)
            return {
                "success": True,
                "filename": filename,
                "tts_status": {
                    "success": True,
                    "model_used": selected_model
                }
            }
            
        except Exception as exc:
            err_type, err_msg = classify_gemini_error(exc)
            logger.exception("Error in TTS service API execution: %s", err_msg)
            raise exc

    # Execute with key rotation using the TTS pool
    rotation_res = run_with_key_rotation("TTS", _execute_tts)
    
    if rotation_res.get("success"):
        res = rotation_res["result"]
        # Add rotation tracking to tts_status
        res["tts_status"]["pool"] = rotation_res.get("pool")
        res["tts_status"]["attempts"] = rotation_res.get("attempts")
        res["tts_status"]["key_index_used"] = rotation_res.get("key_index_used")
        return res
    else:
        # Rotation failed completely (all keys exhausted or pool empty)
        return {
            "success": False,
            "error_type": rotation_res.get("error_type", "tts_failed"),
            "message": "آواز بنانے میں مسئلہ آ رہا ہے، دوبارہ کوشش کریں۔",
            "tts_status": {
                "success": False,
                "pool": rotation_res.get("pool"),
                "error_type": rotation_res.get("error_type", "tts_failed"),
                "attempts": rotation_res.get("attempts", []),
                "key_index_used": rotation_res.get("key_index_used", 0)
            }
        }
