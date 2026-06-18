from fastapi import APIRouter, Form, Response, UploadFile
import logging
import xml.sax.saxutils as saxutils
import httpx
import os
import io
from starlette.datastructures import Headers
from routers.analyze import run_crop_analysis

logger = logging.getLogger(__name__)

router = APIRouter()

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

@router.post("/whatsapp")
@router.post("/webhook/twilio/whatsapp")
async def twilio_whatsapp_webhook(
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
        
        # Text-only flow (when NumMedia == 0)
        if NumMedia == 0:
            if not body_text:
                farmer_response = "خوش آمدید! براہ کرم اپنی فصل کا نام یا کوئی زرعی سوال لکھ کر بھیجیں تاکہ فارم اے آئی آپ کی رہنمائی کر سکے۔"
            else:
                try:
                    # Call the existing FarmAI analysis pipeline
                    analysis_result = await run_crop_analysis(text=body_text)
                    farmer_response = analysis_result.get("farmer_response")
                    if not farmer_response:
                        farmer_response = (
                            "آپ کا پیغام موصول ہو گیا ہے۔ "
                            "بہتر مشورے کے لیے فصل کی صاف تصویر یا مزید تفصیل بھیجیں۔"
                        )
                except Exception as e:
                    logger.error("Error processing text message in Twilio WhatsApp webhook: %s", e)
                    farmer_response = "Sorry, FarmAI could not process this message right now. Please try again."
        else:
            # Image or media flow (when NumMedia > 0)
            # FAST acknowledgement mode for debug (does not call download or Gemini)
            logger.info("FAST acknowledgement mode triggered for NumMedia=%d", NumMedia)
            farmer_response = "FarmAI received your crop image. Image diagnosis is being tested."

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
        # Log error safely without printing any secrets
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


