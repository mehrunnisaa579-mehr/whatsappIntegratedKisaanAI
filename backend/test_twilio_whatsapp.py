import sys
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_twilio_webhook():
    print("Running Twilio WhatsApp Webhook test...")
    payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "hello farm ai",
        "NumMedia": "0",
        "MessageSid": "SM_TEST_123"
    }
    
    # Send request as form-data
    response = client.post("/webhook/twilio/whatsapp", data=payload)
    
    # Assertions
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    content_type = response.headers.get("content-type", "")
    assert "xml" in content_type.lower() or "text" in content_type.lower(), f"Expected XML or text content-type, got {content_type}"
    
    body_text = response.text
    assert "<Response>" in body_text, f"Expected <Response> element missing, got: {body_text}"
    assert "</Response>" in body_text, f"Expected </Response> element missing, got: {body_text}"
    assert "<Message>" in body_text, f"Expected <Message> element missing, got: {body_text}"
    assert "</Message>" in body_text, f"Expected </Message> element missing, got: {body_text}"
    assert "FarmAI received your message" in body_text, f"Expected message content missing, got: {body_text}"
    assert "Twilio webhook is working" in body_text, f"Expected webhook confirmation missing, got: {body_text}"
    
    print("Test passed successfully!")
    print(f"Response Body: {body_text}")

if __name__ == "__main__":
    test_twilio_webhook()
