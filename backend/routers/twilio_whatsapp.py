from fastapi import APIRouter, Form, Response, UploadFile, Request, BackgroundTasks
import logging
import xml.sax.saxutils as saxutils
import httpx
import os
import io
import time
import re
from starlette.datastructures import Headers
from routers.analyze import run_crop_analysis
from services.tts_service import generate_tts_audio
from services.gemini_service import generate_safe_tts_summary

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory conversation state cache.
# Map of from_number -> {last_response_text, tts_summary, pending_tts_confirmation, updated_at}
conversation_states = {}

async def download_twilio_media(media_url: str) -> bytes:
    """
    Downloads media from Twilio using HTTP Basic Authentication.
    """
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    
    auth = None
    if account_sid and auth_token:
        auth = httpx.BasicAuth(account_sid, auth_token)
        
    async with httpx.AsyncClient() as client:
        logger.info("Downloading Twilio media from: %s", media_url)
        response = await client.get(media_url, auth=auth, follow_redirects=True, timeout=15.0)
        response.raise_for_status()
        return response.content

async def send_twilio_whatsapp_message(to_number: str, body: str = None, media_url: str = None) -> bool:
    """
    Sends an outbound WhatsApp message or media (audio) to a farmer via Twilio.
    """
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_WHATSAPP_FROM")
    
    if not account_sid or not auth_token or not from_number:
        logger.error("Twilio credentials or TWILIO_WHATSAPP_FROM not set in environment.")
        return False
        
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = {
        "From": from_number,
        "To": to_number,
    }
    if body:
        data["Body"] = body
    if media_url:
        data["MediaUrl"] = media_url
        
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                auth=(account_sid, auth_token),
                data=data,
                timeout=15.0
            )
            response.raise_for_status()
            logger.info("Sent Twilio WhatsApp message/media to %s: %s", to_number, response.json().get("sid"))
            return True
    except Exception as e:
        logger.error("Failed to send Twilio WhatsApp message/media to %s: %s", to_number, str(e))
        return False

def trim_to_max_chars(text: str, max_chars: int = 1500) -> str:
    """
    Trims text to be under max_chars.
    Attempts to truncate cleanly at a sentence boundary (., ۔, \n) if possible.
    """
    if not text or len(text) <= max_chars:
        return text
    
    # Try to find the last sentence boundary in the truncated portion
    target_len = max_chars - 4 # room for "..." or space
    truncated = text[:target_len]
    
    last_idx = max(truncated.rfind('۔'), truncated.rfind('.'), truncated.rfind('\n'))
    # Only use sentence boundary if it's not too short (at least 60% of target_len)
    if last_idx > target_len * 0.6:
        return truncated[:last_idx + 1].strip()
    return truncated.strip() + "..."

def get_base_url(request: Request) -> str:
    public_base_url = os.environ.get("PUBLIC_BASE_URL")
    if public_base_url:
        return public_base_url.rstrip('/')
    base_url = str(request.base_url).rstrip('/')
    if not any(lh in base_url for lh in ("localhost", "127.0.0.1", "0.0.0.0")):
        if base_url.startswith("http://"):
            base_url = "https://" + base_url[7:]
    return base_url

def sanitize_text_for_tts(text: str, language_hint: str = None) -> str:
    """
    Cleans headings, removes markdown, bullets, emojis, XML/TwiML, URLs,
    excessive punctuation, colons, and weird symbols.
    Limits text length depending on language:
      - English: max 550 chars
      - Roman Urdu: max 280 chars
      - Urdu script: max 280 chars
    """
    if not text:
        return ""
    
    # 1. Remove XML/TwiML tags if any
    text = re.sub(r'<[^>]*>', '', text)
    
    # 2. Remove URLs
    text = re.sub(r'https?://\S+|www\.\S+', '', text)
    
    # 3. Replace colons (:) and dashes with spaces/commas for natural pause
    text = text.replace(":", ", ").replace("—", " ").replace("-", " ")
    
    # 4. Remove emojis and weird symbols (allow letters, numbers, spaces, standard English and Urdu punctuation)
    text = re.sub(r'[^\w\s\.,\?!\(\)۔\u0600-\u06FF]', '', text)
    
    # 5. Remove markdown symbols
    text = text.replace("**", "").replace("*", "").replace("__", "").replace("_", "")
    
    # 6. Remove list bullets and numbers at the start of lines or within text
    text = re.sub(r'^\s*[-*+•]\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+[\s\.)\-]*', '', text, flags=re.MULTILINE)
    
    # 7. Normalize spaces and newlines
    text = re.sub(r'\s+', ' ', text).strip()
    
    # 8. Determine language-specific limit
    lang = language_hint
    if not lang:
        from utils.helpers import detect_language
        lang = detect_language(text)
    
    lang_lower = str(lang).lower().strip()
    if lang_lower in ("ur", "urdu"):
        max_limit = 280
    elif lang_lower == "roman_urdu":
        max_limit = 280
    else:
        max_limit = 550
        
    # Limit length
    if len(text) > max_limit:
        truncated = text[:max_limit]
        # Attempt to cut at a sentence ending (either English period or Urdu full stop)
        last_period = max(truncated.rfind('.'), truncated.rfind('۔'))
        if last_period > max_limit // 2:
            text = truncated[:last_period + 1]
        else:
            text = truncated + "..."
            
    return text.strip()

async def generate_and_send_tts_summary(to_number: str, text_to_speak: str, base_url: str, language_hint: str = None):
    """
    Background task to generate TTS audio and send it as a WhatsApp media message.
    """
    current_stage = "init"
    masked_number = to_number[:12] + "..." if to_number else "None"
    try:
        logger.info("Background task received base_url: %s", base_url)
        logger.info("Generating safe TTS summary...")
        
        # Safe debug logs: log length only
        summary_len = len(text_to_speak) if text_to_speak else 0
        logger.info("Safe TTS summary generated. Length: %d", summary_len)
        
        # Generate audio using existing tts_service
        current_stage = "generate_tts_audio"
        logger.info("Calling generate_tts_audio for WhatsApp summary...")
        tts_result = generate_tts_audio(text_to_speak, language_hint=language_hint)
        
        if not tts_result.get("success"):
            logger.error("Failed to generate TTS audio in background: %s", tts_result.get("message"))
            raise RuntimeError(f"TTS generation failed: {tts_result.get('message')}")
            
        filename = tts_result["filename"]
        logger.info("WhatsApp TTS WAV generated successfully: %s", filename)
        
        # Check static WAV file exists
        from pathlib import Path
        from services.tts_service import STATIC_AUDIO_DIR
        wav_path = Path(STATIC_AUDIO_DIR) / filename
        if not wav_path.exists():
            raise FileNotFoundError(f"WAV file does not exist: {wav_path}")
            
        # Convert WAV to OGG/Opus
        current_stage = "convert_wav_to_ogg"
        logger.info("Starting WAV to OGG conversion...")
        
        from services.tts_service import convert_wav_to_ogg_opus
        ogg_filename = convert_wav_to_ogg_opus(filename)
        logger.info("WhatsApp TTS audio converted to OGG successfully: %s", ogg_filename)
        
        # Check static OGG file exists and has size > 0
        ogg_path = Path(STATIC_AUDIO_DIR) / ogg_filename
        if not ogg_path.exists():
            raise FileNotFoundError(f"OGG file does not exist: {ogg_path}")
        ogg_size = ogg_path.stat().st_size
        logger.info("Verified OGG file size: %d bytes", ogg_size)
        if ogg_size == 0:
            raise RuntimeError("Generated OGG file is empty")
            
        audio_url = f"{base_url}/static/audio/{ogg_filename}"
        logger.info("Generated WhatsApp TTS audio URL: %s", audio_url)
        
        # Public URL self-check before Twilio send
        current_stage = "public_url_self_check"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(audio_url, timeout=5.0)
                logger.info(
                    "Self-check response: status_code=%d, content-type=%s, content-length=%s",
                    response.status_code,
                    response.headers.get("content-type", ""),
                    response.headers.get("content-length", "unknown")
                )
        except Exception as check_err:
            logger.error("Self-check request failed: %s", str(check_err))
            
        # Send media message via Twilio outbound helper
        current_stage = "send_twilio_message"
        logger.info("Sending OGG media URL to Twilio WhatsApp...")
        success = await send_twilio_whatsapp_message(
            to_number=to_number,
            media_url=audio_url
        )
        if not success:
            raise RuntimeError("Twilio send returned failure status")
            
        logger.info("Twilio media send completed.")
        
    except Exception as e:
        logger.error("WhatsApp TTS background task failed at stage: %s, error: %s", current_stage, str(e))
        # Send error text message instead of fallback to WAV
        await send_twilio_whatsapp_message(
            to_number=to_number,
            body="Audio summary could not be generated right now. Please try again."
        )

async def analyze_image_in_background(
    from_number: str,
    media_url: str,
    content_type: str,
    body_text: str,
    request: Request
):
    """
    Background task to download crop image, run diagnosis pipeline, and prompt for TTS summary.
    """
    try:
        logger.info("Background task: analyzing crop image for %s", from_number)
        
        allowed_types = ["image/jpeg", "image/png", "image/webp"]
        c_type = content_type.lower() if content_type else ""
        
        if not c_type or c_type not in allowed_types:
            logger.warning("Unsupported media content type: %s", c_type)
            await send_twilio_whatsapp_message(
                to_number=from_number,
                body="Please send a crop image or a farming-related text question."
            )
            return
            
        if not media_url:
            logger.error("media_url is missing for background image task.")
            await send_twilio_whatsapp_message(
                to_number=from_number,
                body="Please send a clear crop image so FarmAI can help diagnose the issue."
            )
            return
            
        # Download image from Twilio
        try:
            image_bytes = await download_twilio_media(media_url)
        except Exception as download_err:
            logger.error("Failed to download Twilio media: %s", str(download_err))
            await send_twilio_whatsapp_message(
                to_number=from_number,
                body="Sorry, FarmAI could not download the image. Please try sending a clear crop image again."
            )
            return
            
        # Wrap bytes in UploadFile
        image_file = UploadFile(
            file=io.BytesIO(image_bytes),
            filename="whatsapp_image.jpg",
            headers=Headers({"content-type": c_type})
        )
        
        # Run crop analysis
        analysis_text = body_text if body_text else None
        analysis_result = await run_crop_analysis(
            text=analysis_text,
            image=image_file
        )
        
        # Extract response
        farmer_response = None
        tts_summary = None
        if isinstance(analysis_result, dict):
            tts_summary = analysis_result.get("tts_summary")
            for key in ["farmer_response", "response", "detailed_response", "message"]:
                val = analysis_result.get(key)
                if val and str(val).strip():
                    farmer_response = str(val).strip()
                    break
                    
        if not farmer_response:
            farmer_response = "Sorry, FarmAI could not process the crop image right now. Please try again with a clear image."
            
        # Detect language and generate summary if missing
        from utils.helpers import detect_language
        lang_hint = detect_language(farmer_response)
        
        if not tts_summary:
            tts_summary = generate_safe_tts_summary(farmer_response, lang_hint)
            
        # Trim response before appending the prompt
        prompt = "\n\nDo you want an audio summary of this diagnosis? Reply yes or no."
        farmer_response = trim_to_max_chars(farmer_response, 1500 - len(prompt))
            
        # Save to state-machine
        conversation_states[from_number] = {
            "last_response_text": farmer_response,
            "tts_summary": tts_summary,
            "language_hint": lang_hint,
            "pending_tts_confirmation": True,
            "updated_at": time.time()
        }
        
        # Send diagnosis and prompt
        full_text = farmer_response + prompt
        await send_twilio_whatsapp_message(
            to_number=from_number,
            body=full_text
        )
        
    except Exception as e:
        logger.error("Error in analyze_image_in_background background task: %s", str(e))
        await send_twilio_whatsapp_message(
            to_number=from_number,
            body="Sorry, FarmAI could not process the crop image right now. Please try again with a clear image."
        )

@router.post("/whatsapp")
@router.post("/webhook/twilio/whatsapp")
async def twilio_whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    From: str = Form(None),
    To: str = Form(None),
    Body: str = Form(None),
    NumMedia: int = Form(0),
    MediaUrl0: str = Form(None),
    MediaContentType0: str = Form(None),
    MessageSid: str = Form(None)
):
    try:
        body_text = Body.strip() if Body else ""
        body_length = len(body_text)
        has_media = NumMedia > 0
        
        # Safe logging: log request received, media presence, and Body length only
        logger.info(
            "Twilio webhook request received: From=%s, To=%s, MessageSid=%s, NumMedia=%d, HasMedia=%s, BodyLength=%d",
            From, To, MessageSid, NumMedia, has_media, body_length
        )
        
        # 1. Retrieve user's conversation state
        user_state = conversation_states.get(From)
        
        # 2. Check for conversation state expiry (TTL 5 minutes / 300 seconds)
        if user_state:
            if time.time() - user_state.get("updated_at", 0) > 300:
                logger.info("Conversation state for %s has expired.", From)
                conversation_states.pop(From, None)
                user_state = None
                
        # Prep yes/no values
        clean_reply = body_text.lower().strip().rstrip('.')
        yes_values = {"yes", "y", "1", "haan", "han", "audio"}
        no_values = {"no", "n", "0", "nah", "nahi"}
        
        is_yes_word = clean_reply in yes_values
        is_no_word = clean_reply in no_values

        # 3. Handle yes/no confirmation responses if pending
        if user_state and user_state.get("pending_tts_confirmation"):
            if is_yes_word:
                logger.info("WhatsApp YES detected for audio summary.")
                logger.info("Pending audio summary state found.")
                
                # Clear pending confirmation flag
                user_state["pending_tts_confirmation"] = False
                user_state["updated_at"] = time.time()
                
                # Fast TwiML acknowledgement response
                farmer_response = "Okay, generating your audio summary now..."
                
                # Retrieve language hint
                lang_hint = user_state.get("language_hint")
                if not lang_hint:
                    from utils.helpers import detect_language
                    lang_hint = detect_language(user_state.get("last_response_text"))
                
                text_to_speak = user_state.get("tts_summary")
                if not text_to_speak:
                    text_to_speak = generate_safe_tts_summary(user_state.get("last_response_text"), lang_hint)
                
                # Sanitize the summary text for TTS engine
                text_to_speak = sanitize_text_for_tts(text_to_speak, language_hint=lang_hint)
                
                # Compute base URL dynamically
                base_url = get_base_url(request)
                
                # Spawn TTS generation and send in background
                logger.info("Starting WhatsApp TTS background task.")
                background_tasks.add_task(
                    generate_and_send_tts_summary,
                    to_number=From,
                    text_to_speak=text_to_speak,
                    base_url=base_url,
                    language_hint=lang_hint
                )
            elif is_no_word:
                # Remove conversation state completely
                conversation_states.pop(From, None)
                farmer_response = "Okay, no audio summary will be sent."
            else:
                # Unclear reply, prompt again
                user_state["updated_at"] = time.time()
                farmer_response = "Please reply yes or no for the audio summary."
                
            # Return response
            farmer_response = trim_to_max_chars(farmer_response, 1500)
            escaped_response = saxutils.escape(farmer_response)
            twiml_response = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<Response>\n'
                f'    <Message>{escaped_response}</Message>\n'
                '</Response>'
            )
            return Response(content=twiml_response, media_type="application/xml")
            
        elif is_yes_word or is_no_word:
            # Handle yes/no words when there is no pending confirmation state
            if is_yes_word:
                farmer_response = "No pending audio summary found. Please ask a farming question first."
            else:
                conversation_states.pop(From, None)
                farmer_response = "Okay, no audio summary will be sent."
                
            farmer_response = trim_to_max_chars(farmer_response, 1500)
            escaped_response = saxutils.escape(farmer_response)
            twiml_response = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<Response>\n'
                f'    <Message>{escaped_response}</Message>\n'
                '</Response>'
            )
            return Response(content=twiml_response, media_type="application/xml")

        # 4. Process normal incoming messages if no confirmation is pending
        if NumMedia == 0:
            # Text-only flow
            if not body_text:
                farmer_response = "خوش آمدید! براہ کرم اپنی فصل کا نام یا کوئی زرعی سوال لکھ کر بھیجیں تاکہ فارم اے آئی آپ کی رہنمائی کر سکے۔"
            else:
                try:
                    # Run crop analysis
                    analysis_result = await run_crop_analysis(text=body_text)
                    farmer_response = analysis_result.get("farmer_response")
                    tts_summary = analysis_result.get("tts_summary")
                    
                    if not farmer_response:
                        farmer_response = (
                            "آپ کا پیغام موصول ہو گیا ہے۔ "
                            "بہتر مشورے کے لیے فصل کی صاف تصویر یا مزید تفصیل بھیجیں۔"
                        )
                    else:
                        # Infer language and generate summary if missing
                        from utils.helpers import detect_language
                        lang_hint = detect_language(farmer_response)
                        
                        if not tts_summary:
                            tts_summary = generate_safe_tts_summary(farmer_response, lang_hint)
                            
                        # Trim response before saving state and appending prompt
                        prompt = "\n\nDo you want an audio summary of this advice? Reply yes or no."
                        farmer_response = trim_to_max_chars(farmer_response, 1500 - len(prompt))
                            
                        # Save state
                        conversation_states[From] = {
                            "last_response_text": farmer_response,
                            "tts_summary": tts_summary,
                            "language_hint": lang_hint,
                            "pending_tts_confirmation": True,
                            "updated_at": time.time()
                        }
                        
                        # Append the audio summary prompt
                        farmer_response += prompt
                        
                except Exception as e:
                    logger.error("Error processing text message: %s", str(e))
                    farmer_response = "Sorry, FarmAI could not process this message right now. Please try again."
        else:
            # Image or media flow
            logger.info("Spawning background image analysis task for %s", From)
            background_tasks.add_task(
                analyze_image_in_background,
                from_number=From,
                media_url=MediaUrl0,
                content_type=MediaContentType0,
                body_text=body_text,
                request=request
            )
            
            # Immediately return fast acknowledgement
            farmer_response = "FarmAI received your crop image. Analyzing it now..."

        # Escape XML characters to prevent TwiML formatting issues
        farmer_response = trim_to_max_chars(farmer_response, 1500)
        escaped_response = saxutils.escape(farmer_response)
        
        twiml_response = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Response>\n'
            f'    <Message>{escaped_response}</Message>\n'
            '</Response>'
        )
        return Response(content=twiml_response, media_type="application/xml")

    except Exception as webhook_err:
        logger.error("Unexpected error in twilio_whatsapp_webhook: %s", str(webhook_err))
        fallback_msg = "Sorry, FarmAI could not process this request right now. Please try again."
        fallback_msg = trim_to_max_chars(fallback_msg, 1500)
        escaped_fallback = saxutils.escape(fallback_msg)
        twiml_response = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Response>\n'
            f'    <Message>{escaped_fallback}</Message>\n'
            '</Response>'
        )
        return Response(content=twiml_response, media_type="application/xml")



