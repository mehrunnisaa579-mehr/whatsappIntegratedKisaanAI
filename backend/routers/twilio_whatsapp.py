from fastapi import APIRouter, Form, Response
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/webhook/twilio/whatsapp")
async def twilio_whatsapp_webhook(
    From: str = Form(None),
    To: str = Form(None),
    Body: str = Form(None),
    NumMedia: int = Form(0),
    MessageSid: str = Form(None)
):
    body_length = len(Body) if Body else 0
    logger.info("Received Twilio WhatsApp webhook: From=%s, To=%s, MessageSid=%s, NumMedia=%d, BodyLength=%d", From, To, MessageSid, NumMedia, body_length)
    
    twiml_response = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Response>\n'
        '    <Message>FarmAI received your message. Twilio webhook is working.</Message>\n'
        '</Response>'
    )
    return Response(content=twiml_response, media_type="application/xml")
