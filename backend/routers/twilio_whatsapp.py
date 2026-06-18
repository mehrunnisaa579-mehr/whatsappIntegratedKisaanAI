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
        # Twilio media files are hosted on twilio.com.
        # We also support basic auth for any domain in case of custom routing.
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
    body_text = Body.strip() if Body else ""
    body_length = len(body_text)
    
    logger.info(
        "Twilio WhatsApp webhook hit: From=%s, To=%s, MessageSid=%s, NumMedia=%d, MediaUrl0=%s, MediaContentType0=%s, BodyLength=%d, Body=%s",
        From, To, MessageSid, NumMedia, MediaUrl0, MediaContentType0, body_length, body_text
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
        # Check if the first media is an image MIME type
        allowed_types = ["image/jpeg", "image/png", "image/webp"]
        content_type = MediaContentType0.lower() if MediaContentType0 else ""
        
        if not content_type or content_type not in allowed_types:
            logger.warning("Unsupported media content type: %s", content_type)
            farmer_response = "Please send a crop image or a farming-related text question."
        else:
            if not MediaUrl0:
                logger.error("NumMedia > 0 but MediaUrl0 is missing.")
                farmer_response = "Please send a clear crop image so FarmAI can help diagnose the issue."
            else:
                try:
                    # Download the image from Twilio
                    image_bytes = await download_twilio_media(MediaUrl0)
                except Exception as download_err:
                    logger.error("Failed to download Twilio media from %s: %s", MediaUrl0, download_err)
                    farmer_response = "Sorry, FarmAI could not download the image. Please try sending a clear crop image again."
                else:
                    try:
                        # Construct a Starlette/FastAPI UploadFile adapter
                        image_file = UploadFile(
                            file=io.BytesIO(image_bytes),
                            filename="whatsapp_image.jpg",
                            headers=Headers({"content-type": content_type})
                        )
                        
                        # Pass image and caption context (if any) to crop analysis
                        analysis_text = body_text if body_text else None
                        analysis_result = await run_crop_analysis(
                            text=analysis_text,
                            image=image_file
                        )
                        
                        # Extract response priority: farmer_response, response, detailed_response, message
                        farmer_response = None
                        if isinstance(analysis_result, dict):
                            for key in ["farmer_response", "response", "detailed_response", "message"]:
                                val = analysis_result.get(key)
                                if val and str(val).strip():
                                    farmer_response = str(val).strip()
                                    break
                        
                        if not farmer_response:
                            farmer_response = "Sorry, FarmAI could not process the crop image right now. Please try again with a clear image."
                    except Exception as analysis_err:
                        logger.error("Error running crop analysis for Twilio image: %s", analysis_err)
                        farmer_response = "Sorry, FarmAI could not process the crop image right now. Please try again with a clear image."

    # Escape XML characters to prevent TwiML formatting issues
    escaped_response = saxutils.escape(farmer_response)
    
    twiml_response = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Response>\n'
        f'    <Message>{escaped_response}</Message>\n'
        '</Response>'
    )
    return Response(content=twiml_response, media_type="application/xml")

