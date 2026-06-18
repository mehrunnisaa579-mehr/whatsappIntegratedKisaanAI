from fastapi import APIRouter, Form, Response, UploadFile, Request, BackgroundTasks
import logging
import xml.sax.saxutils as saxutils
import httpx
import os
import io
import time
from starlette.datastructures import Headers
from routers.analyze import run_crop_analysis
from services.tts_service import generate_tts_audio, STATIC_AUDIO_DIR
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
    outbound_enabled = os.environ.get("TWILIO_OUTBOUND_ENABLED", "true").lower() == "true"
    if not outbound_enabled:
        logger.info("Twilio outbound skipped because TWILIO_OUTBOUND_ENABLED=false")
        return True

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

def generate_tts_summary_file(text_to_speak: str, request: Request = None) -> dict:
    """
    Helper to generate TTS audio and return metadata.
    Returns dict with keys: success, file_path, media_url, format, error_message
    """
    try:
        tts_result = generate_tts_audio(text_to_speak)
        if not tts_result.get("success"):
            return {
                "success": False,
                "file_path": None,
                "media_url": None,
                "format": None,
                "error_message": tts_result.get("message", "TTS generation failed")
            }
            
        filename = tts_result["filename"]
        file_path = os.path.join(STATIC_AUDIO_DIR, filename)
        
        # Build public absolute media URL
        public_base_url = os.environ.get("PUBLIC_BASE_URL")
        if public_base_url:
            base_url = public_base_url.rstrip('/')
        elif request is not None:
            base_url = str(request.base_url).rstrip('/')
            if not any(lh in base_url for lh in ("localhost", "127.0.0.1", "0.0.0.0")):
                if base_url.startswith("http://"):
                    base_url = "https://" + base_url[7:]
        else:
            base_url = "https://localhost"
            
        audio_url = f"{base_url}/static/audio/{filename}"
        
        return {
            "success": True,
            "file_path": file_path,
            "media_url": audio_url,
            "format": "wav",
            "error_message": None
        }
    except Exception as e:
        logger.error("Error in generate_tts_summary_file: %s", str(e))
        return {
            "success": False,
            "file_path": None,
            "media_url": None,
            "format": None,
            "error_message": str(e)
        }

async def generate_and_send_tts_summary(to_number: str, text_to_speak: str, request: Request):
    """
    Background task to generate TTS audio and send it as a WhatsApp media message.
    """
    try:
        logger.info("Background task: generating TTS audio for %s", to_number)
        
        result = generate_tts_summary_file(text_to_speak, request)
        
        if not result["success"]:
            logger.error("Failed to generate TTS audio in background: %s", result["error_message"])
            await send_twilio_whatsapp_message(
                to_number=to_number,
                body="Sorry, FarmAI could not generate the audio summary right now."
            )
            return
            
        audio_url = result["media_url"]
        logger.info("Outbound media URL constructed: %s", audio_url)
        
        # Send media message via Twilio outbound helper
        success = await send_twilio_whatsapp_message(
            to_number=to_number,
            media_url=audio_url
        )
        if not success:
            logger.error("Failed to send outbound TTS audio message to %s", to_number)
            # Try to send fallback text only once, do not retry / loop
            await send_twilio_whatsapp_message(
                to_number=to_number,
                body="Sorry, FarmAI could not generate the audio summary right now."
            )
            
    except Exception as e:
        logger.error("Unexpected error in generate_and_send_tts_summary background task: %s", str(e))
        await send_twilio_whatsapp_message(
            to_number=to_number,
            body="Sorry, FarmAI could not generate the audio summary right now."
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
            
        # Save to state-machine
        conversation_states[from_number] = {
            "last_response_text": farmer_response,
            "tts_summary": tts_summary,
            "pending_tts_confirmation": True,
            "updated_at": time.time()
        }
        
        # Send diagnosis and prompt
        full_text = farmer_response + "\n\nDo you want an audio summary of this diagnosis? Reply yes or no."
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
                
        # 3. Handle yes/no confirmation responses if pending
        if user_state and user_state.get("pending_tts_confirmation"):
            clean_reply = body_text.lower().strip().rstrip('.')
            
            yes_values = {"yes", "y", "1", "haan", "han", "audio"}
            no_values = {"no", "n", "0", "nah", "nahi"}
            
            if clean_reply in yes_values:
                # Clear pending confirmation flag
                user_state["pending_tts_confirmation"] = False
                user_state["updated_at"] = time.time()
                
                # Fast TwiML acknowledgement response
                farmer_response = "Okay, generating your audio summary now..."
                
                # Spawn TTS generation and send in background
                text_to_speak = user_state.get("tts_summary") or user_state.get("last_response_text")
                background_tasks.add_task(
                    generate_and_send_tts_summary,
                    to_number=From,
                    text_to_speak=text_to_speak,
                    request=request
                )
            elif clean_reply in no_values:
                # Remove conversation state completely
                conversation_states.pop(From, None)
                farmer_response = "Okay, no audio summary will be sent."
            else:
                # Unclear reply, prompt again
                user_state["updated_at"] = time.time()
                farmer_response = "Please reply yes or no for the audio summary."
                
            # Return response
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
                            
                        # Save state
                        conversation_states[From] = {
                            "last_response_text": farmer_response,
                            "tts_summary": tts_summary,
                            "pending_tts_confirmation": True,
                            "updated_at": time.time()
                        }
                        
                        # Append the audio summary prompt
                        farmer_response += "\n\nDo you want an audio summary of this advice? Reply yes or no."
                        
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
        escaped_fallback = saxutils.escape(fallback_msg)
        twiml_response = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Response>\n'
            f'    <Message>{escaped_fallback}</Message>\n'
            '</Response>'
        )
        return Response(content=twiml_response, media_type="application/xml")



