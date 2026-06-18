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
    
    mock_advice = "تشخیص: پتے پیلے ہونے کی وجہ نائٹروجن کی کمی ہو سکتی ہے۔"
    
    for path in ["/whatsapp", "/webhook/twilio/whatsapp"]:
        print(f"Testing image only flow on path: {path}")
        with patch("routers.twilio_whatsapp.download_twilio_media", new_callable=AsyncMock) as mock_download, \
             patch("routers.twilio_whatsapp.run_crop_analysis", new_callable=AsyncMock) as mock_analyze:
            
            mock_download.return_value = b"fake_image_bytes"
            mock_analyze.return_value = {
                "status": "success",
                "farmer_response": mock_advice
            }
            
            response = client.post(path, data=payload)
            
            # Assertions
            mock_download.assert_called_once_with("https://api.twilio.com/mock-image.jpg")
            
            # Retrieve the called argument and verify the UploadFile
            args, kwargs = mock_analyze.call_args
            assert kwargs.get("text") is None
            assert kwargs.get("image") is not None
            image_file = kwargs.get("image")
            assert image_file.filename == "whatsapp_image.jpg"
            assert image_file.content_type == "image/jpeg"
            
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        body_text = response.text
        assert mock_advice in body_text, f"Expected mocked advice missing in response, got: {body_text}"
        
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
    
    mock_advice = "تشخیص مع کپشن: پتے پیلے ہونے کی وجہ نائٹروجن کی کمی ہے۔"
    
    for path in ["/whatsapp", "/webhook/twilio/whatsapp"]:
        print(f"Testing image + caption flow on path: {path}")
        with patch("routers.twilio_whatsapp.download_twilio_media", new_callable=AsyncMock) as mock_download, \
             patch("routers.twilio_whatsapp.run_crop_analysis", new_callable=AsyncMock) as mock_analyze:
            
            mock_download.return_value = b"fake_image_bytes"
            mock_analyze.return_value = {
                "status": "success",
                "farmer_response": mock_advice
            }
            
            response = client.post(path, data=payload)
            
            # Assertions
            mock_download.assert_called_once_with("https://api.twilio.com/mock-image.jpg")
            
            args, kwargs = mock_analyze.call_args
            assert kwargs.get("text") == "These cotton leaves are turning yellow"
            assert kwargs.get("image") is not None
            image_file = kwargs.get("image")
            assert image_file.filename == "whatsapp_image.jpg"
            assert image_file.content_type == "image/jpeg"
            
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        body_text = response.text
        assert mock_advice in body_text, f"Expected mocked advice missing in response, got: {body_text}"
        
    print("Image + Caption test passed successfully for both paths!")

def test_twilio_webhook_unsupported_media():
    print("\nRunning Twilio WhatsApp Webhook test (Unsupported Media)...")
    payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/mock-doc.pdf",
        "MediaContentType0": "application/pdf",
        "MessageSid": "SM_TEST_PDF_123"
    }
    
    for path in ["/whatsapp", "/webhook/twilio/whatsapp"]:
        print(f"Testing unsupported media flow on path: {path}")
        with patch("routers.twilio_whatsapp.download_twilio_media", new_callable=AsyncMock) as mock_download, \
             patch("routers.twilio_whatsapp.run_crop_analysis", new_callable=AsyncMock) as mock_analyze:
            
            response = client.post(path, data=payload)
            
            # Should NOT download or analyze
            mock_download.assert_not_called()
            mock_analyze.assert_not_called()
            
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        body_text = response.text
        assert "Please send a crop image or a farming-related text question." in body_text
        
    print("Unsupported Media test passed successfully for both paths!")

def test_twilio_webhook_download_failure():
    print("\nRunning Twilio WhatsApp Webhook test (Download Failure Fallback)...")
    payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/mock-image.jpg",
        "MediaContentType0": "image/jpeg",
        "MessageSid": "SM_TEST_IMG_123"
    }
    
    for path in ["/whatsapp", "/webhook/twilio/whatsapp"]:
        print(f"Testing download failure fallback on path: {path}")
        with patch("routers.twilio_whatsapp.download_twilio_media", new_callable=AsyncMock) as mock_download, \
             patch("routers.twilio_whatsapp.run_crop_analysis", new_callable=AsyncMock) as mock_analyze:
            
            mock_download.side_effect = Exception("HTTP 404 Not Found")
            
            response = client.post(path, data=payload)
            
            mock_download.assert_called_once_with("https://api.twilio.com/mock-image.jpg")
            mock_analyze.assert_not_called()
            
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        body_text = response.text
        assert "Sorry, FarmAI could not download the image. Please try sending a clear crop image again." in body_text
        
    print("Download Failure Fallback test passed successfully for both paths!")

if __name__ == "__main__":
    test_twilio_webhook_skeleton()
    test_twilio_webhook_text_flow()
    test_twilio_webhook_image_only_flow()
    test_twilio_webhook_image_caption_flow()
    test_twilio_webhook_unsupported_media()
    test_twilio_webhook_download_failure()
