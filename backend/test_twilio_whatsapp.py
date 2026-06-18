import sys
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_twilio_webhook_skeleton():
    print("\nRunning Twilio WhatsApp Webhook test (Skeleton/Instruction)...")
    payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "",
        "NumMedia": "0",
        "MessageSid": "SM_TEST_123"
    }
    
    for path in ["/whatsapp", "/webhook/twilio/whatsapp"]:
        print(f"Testing skeleton on path: {path}")
        response = client.post(path, data=payload)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        content_type = response.headers.get("content-type", "")
        assert "xml" in content_type.lower() or "text" in content_type.lower(), f"Expected XML/text content-type, got {content_type}"
        
        body_text = response.text
        assert "<Response>" in body_text, f"Expected <Response> element missing, got: {body_text}"
        assert "</Response>" in body_text, f"Expected </Response> element missing, got: {body_text}"
        assert "<Message>" in body_text, f"Expected <Message> element missing, got: {body_text}"
        assert "</Message>" in body_text, f"Expected </Message> element missing, got: {body_text}"
        assert "براہ کرم اپنی فصل کا نام یا کوئی زرعی سوال لکھ کر بھیجیں" in body_text, f"Expected polite instruction missing, got: {body_text}"
    
    print("Skeleton/Instruction test passed successfully for both paths!")

def test_twilio_webhook_text_flow():
    print("\nRunning Twilio WhatsApp Webhook test (Text Flow)...")
    payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "my cotton leaves are turning yellow",
        "NumMedia": "0",
        "MessageSid": "SM_TEST_TEXT_123"
    }
    
    mock_advice = "یہ ایک فرضی مشورہ ہے: کپاس کے پتے پیلے ہونے کی صورت میں نمی کا معائنہ کریں۔"
    
    for path in ["/whatsapp", "/webhook/twilio/whatsapp"]:
        print(f"Testing text flow on path: {path}")
        with patch("routers.twilio_whatsapp.run_crop_analysis", new_callable=AsyncMock) as mock_analyze:
            mock_analyze.return_value = {
                "status": "success",
                "farmer_response": mock_advice
            }
            
            response = client.post(path, data=payload)
            mock_analyze.assert_called_once_with(text="my cotton leaves are turning yellow")
            
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        body_text = response.text
        assert mock_advice in body_text, f"Expected mocked advice missing in response, got: {body_text}"
    
    print("Text Flow test passed successfully for both paths!")

def test_twilio_webhook_image_only_flow():
    print("\nRunning Twilio WhatsApp Webhook test (Image Only)...")
    payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/mock-image.jpg",
        "MediaContentType0": "image/jpeg",
        "MessageSid": "SM_TEST_IMG_123"
    }
    
    expected_ack = "FarmAI received your crop image. Image diagnosis is being tested."
    
    for path in ["/whatsapp", "/webhook/twilio/whatsapp"]:
        print(f"Testing image only flow on path: {path}")
        response = client.post(path, data=payload)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        body_text = response.text
        assert expected_ack in body_text, f"Expected fast image acknowledgement missing, got: {body_text}"
        
    print("Image Only test passed successfully for both paths!")

def test_twilio_webhook_image_caption_flow():
    print("\nRunning Twilio WhatsApp Webhook test (Image + Caption)...")
    payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "These cotton leaves are turning yellow",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/mock-image.jpg",
        "MediaContentType0": "image/jpeg",
        "MessageSid": "SM_TEST_IMG_CAPTION_123"
    }
    
    expected_ack = "FarmAI received your crop image. Image diagnosis is being tested."
    
    for path in ["/whatsapp", "/webhook/twilio/whatsapp"]:
        print(f"Testing image + caption flow on path: {path}")
        response = client.post(path, data=payload)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        body_text = response.text
        assert expected_ack in body_text, f"Expected fast image acknowledgement missing, got: {body_text}"
        
    print("Image + Caption test passed successfully for both paths!")

def test_twilio_webhook_unexpected_exception():
    print("\nRunning Twilio WhatsApp Webhook test (Unexpected Exception)...")
    payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "hello",
        "NumMedia": "0",
        "MessageSid": "SM_TEST_CRASH"
    }
    
    expected_fallback = "Sorry, FarmAI could not process this request right now. Please try again."
    
    for path in ["/whatsapp", "/webhook/twilio/whatsapp"]:
        print(f"Testing unexpected exception handling on path: {path}")
        # Patching logger.info to simulate a crash at the top of the webhook
        with patch("routers.twilio_whatsapp.logger.info", side_effect=RuntimeError("Simulated crash")):
            response = client.post(path, data=payload)
            
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        body_text = response.text
        assert expected_fallback in body_text, f"Expected fallback message missing, got: {body_text}"
        
    print("Unexpected Exception test passed successfully for both paths!")

if __name__ == "__main__":
    test_twilio_webhook_skeleton()
    test_twilio_webhook_text_flow()
    test_twilio_webhook_image_only_flow()
    test_twilio_webhook_image_caption_flow()
    test_twilio_webhook_unexpected_exception()
