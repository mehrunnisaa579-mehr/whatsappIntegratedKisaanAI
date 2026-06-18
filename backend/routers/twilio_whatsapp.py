from fastapi import APIRouter, Form, Response, UploadFile, BackgroundTasks
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
        # Avoid logging full media URLs if they may contain sensitive access tokens
        logger.info("Downloading Twilio media...")
        response = await client.get(media_url, auth=auth, follow_redirects=True, timeout=15.0)
        response.raise_for_status()
        return response.content

async def send_twilio_whatsapp_message(to_number: str, message_body: str) -> None:
    """
    Sends an outbound WhatsApp message using the Twilio REST API.
    """
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_WHATSAPP_FROM")
    
    if not account_sid or not auth_token or not from_number:
        logger.error("Twilio credentials or sender number missing from environment.")
        return
        
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    auth = httpx.BasicAuth(account_sid, auth_token)
    data = {
        "From": from_number,
        "To": to_number,
        "Body": message_body
    }
    
    async with httpx.AsyncClient() as client:
        logger.info("Sending outbound Twilio WhatsApp message to %s", to_number)
        response = await client.post(url, auth=auth, data=data, timeout=15.0)
        response.raise_for_status()
        logger.info("Outbound Twilio WhatsApp message sent successfully. SID=%s", response.json().get("sid"))

async def process_image_and_reply(
    media_url: str,
    media_content_type: str,
    caption_text: str,
    to_number: str,
    message_sid: str
) -> None:
    """
    Background task that downloads the Twilio image, performs crop analysis,
    and sends the diagnosis report back to the farmer via outbound WhatsApp message.
    """
    logger.info("Background task started for MessageSid=%s", message_sid)
    try:
        # 1. Download image
        try:
            image_bytes = await download_twilio_media(media_url)
            logger.info("Image downloaded successfully for MessageSid=%s, Bytes=%d", message_sid, len(image_bytes))
        except Exception as download_err:
            logger.error("Background task failed to download media for MessageSid=%s: %s", message_sid, download_err)
            fallback_msg = "Sorry, FarmAI could not download the image. Please try sending a clear crop image again."
            await send_twilio_whatsapp_message(to_number, fallback_msg)
            return

        # 2. Run crop analysis
        try:
            image_file = UploadFile(
                file=io.BytesIO(image_bytes),
                filename="whatsapp_image.jpg",
                headers=Headers({"content-type": media_content_type})
            )
            analysis_text = caption_text if caption_text else None
            analysis_result = await run_crop_analysis(
                text=analysis_text,
                image=image_file
            )
            logger.info("Crop analysis completed successfully for MessageSid=%s", message_sid)
        except Exception as analysis_err:
            logger.error("Background task failed crop analysis for MessageSid=%s: %s", message_sid, analysis_err)
            fallback_msg = "Sorry, FarmAI could not process the crop image right now. Please try again with a clear image."
            await send_twilio_whatsapp_message(to_number, fallback_msg)
            return

        # 3. Extract farmer response
        farmer_response = None
        if isinstance(analysis_result, dict):
            for key in ["farmer_response", "response", "detailed_response", "message"]:
                val = analysis_result.get(key)
                if val and str(val).strip():
                    farmer_response = str(val).strip()
                    break

        if not farmer_response:
            farmer_response = (
                "FarmAI analyzed your crop image, but could not generate a clear diagnosis. "
                "Please send a clearer image with a short description."
            )

        # 4. Message length optimization: trim/summarize long messages
        if len(farmer_response) > 800:
            farmer_response = farmer_response[:800] + "..."

        # 5. Outbound WhatsApp send
        try:
            await send_twilio_whatsapp_message(to_number, farmer_response)
            logger.info("Outbound diagnosis message sent successfully for MessageSid=%s", message_sid)
        except Exception as send_err:
            logger.error("Failed to send outbound diagnosis message for MessageSid=%s: %s", message_sid, send_err)

    except Exception as background_err:
        logger.error("Unexpected error in process_image_and_reply background task for MessageSid=%s: %s", message_sid, background_err)
        try:
            fallback_msg = "Sorry, FarmAI could not process the crop image right now. Please try again with a clear image."
            await send_twilio_whatsapp_message(to_number, fallback_msg)
        except Exception as fallback_send_err:
            logger.error("Failed to send background task fallback for MessageSid=%s: %s", message_sid, fallback_send_err)

@router.post("/whatsapp")
@router.post("/webhook/twilio/whatsapp")
async def twilio_whatsapp_webhook(
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
            allowed_types = ["image/jpeg", "image/png", "image/webp"]
            content_type = MediaContentType0.lower() if MediaContentType0 else ""
            
            if not content_type or content_type not in allowed_types:
                logger.warning("Unsupported media content type: %s", content_type)
                farmer_response = "Please send a clear crop image or a farming-related text question."
            else:
                if not MediaUrl0:
                    logger.error("NumMedia > 0 but MediaUrl0 is missing.")
                    farmer_response = "Please send a clear crop image so FarmAI can help diagnose the issue."
                else:
                    # Queue the background processing task
                    background_tasks.add_task(
                        process_image_and_reply,
                        media_url=MediaUrl0,
                        media_content_type=content_type,
                        caption_text=body_text,
                        to_number=From,
                        message_sid=MessageSid
                    )
                    logger.info("Queued background image diagnosis task for MessageSid=%s", MessageSid)
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



