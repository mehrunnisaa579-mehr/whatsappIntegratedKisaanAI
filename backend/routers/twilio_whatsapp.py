from fastapi import APIRouter, Form, Response
import logging
import xml.sax.saxutils as saxutils
from routers.analyze import analyze_crop

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/whatsapp")
@router.post("/webhook/twilio/whatsapp")
async def twilio_whatsapp_webhook(
    From: str = Form(None),
    To: str = Form(None),
    Body: str = Form(None),
    NumMedia: int = Form(0),
    MessageSid: str = Form(None)
):
    body_text = Body.strip() if Body else ""
    body_length = len(body_text)
    
    logger.info("Twilio WhatsApp webhook hit: From=%s, To=%s, MessageSid=%s, NumMedia=%d, BodyLength=%d, Body=%s", From, To, MessageSid, NumMedia, body_length, body_text)
    
    if not body_text and NumMedia == 0:
        farmer_response = "خوش آمدید! براہ کرم اپنی فصل کا نام یا کوئی زرعی سوال لکھ کر بھیجیں تاکہ فارم اے آئی آپ کی رہنمائی کر سکے۔"
    else:
        try:
            # Call the existing FarmAI analysis pipeline
            analysis_result = await analyze_crop(text=body_text)
            farmer_response = analysis_result.get("farmer_response")
            if not farmer_response:
                farmer_response = (
                    "آپ کا پیغام موصول ہو گیا ہے۔ "
                    "بہتر مشورے کے لیے فصل کی صاف تصویر یا مزید تفصیل بھیجیں۔"
                )
        except Exception as e:
            logger.error("Error processing message in Twilio WhatsApp webhook: %s", e)
            farmer_response = "Sorry, FarmAI could not process this message right now. Please try again."

    # Escape XML characters to prevent TwiML formatting issues
    escaped_response = saxutils.escape(farmer_response)
    
    twiml_response = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Response>\n'
        f'    <Message>{escaped_response}</Message>\n'
        '</Response>'
    )
    return Response(content=twiml_response, media_type="application/xml")
