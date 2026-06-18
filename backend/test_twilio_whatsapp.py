import sys
import io
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

def test_twilio_webhook_image_only_acknowledgement():
    print("\nRunning Twilio WhatsApp Webhook test (Image Only Acknowledgement)...")
    payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/mock-image.jpg",
        "MediaContentType0": "image/jpeg",
        "MessageSid": "SM_TEST_IMG_123"
    }
    
    expected_ack = "FarmAI received your crop image. Analyzing it now..."
    
    for path in ["/whatsapp", "/webhook/twilio/whatsapp"]:
        print(f"Testing image only flow on path: {path}")
        with patch("routers.twilio_whatsapp.download_twilio_media", new_callable=AsyncMock) as mock_download, \
             patch("routers.twilio_whatsapp.run_crop_analysis", new_callable=AsyncMock) as mock_analyze, \
             patch("routers.twilio_whatsapp.send_twilio_whatsapp_message", new_callable=AsyncMock) as mock_send:
            
            mock_download.return_value = b"fake_image_bytes"
            mock_analyze.return_value = {
                "status": "success",
                "farmer_response": "تشخیص: نائٹروجن کی کمی"
            }
            
            response = client.post(path, data=payload)
            
            # Assert immediate response
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            assert expected_ack in response.text
            
            # Verify background task execution
            mock_download.assert_called_once_with("https://api.twilio.com/mock-image.jpg")
            args, kwargs = mock_analyze.call_args
            assert kwargs.get("text") is None
            assert kwargs.get("image") is not None
            image_file = kwargs.get("image")
            assert image_file.filename == "whatsapp_image.jpg"
            assert image_file.content_type == "image/jpeg"
            
            mock_send.assert_called_once_with("whatsapp:+923001234567", "تشخیص: نائٹروجن کی کمی")
        
    print("Image Only flow test passed successfully for both paths!")

def test_twilio_webhook_image_caption_flow():
    print("\nRunning Twilio WhatsApp Webhook test (Image + Caption Background Flow)...")
    payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "These cotton leaves are turning yellow",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/mock-image.jpg",
        "MediaContentType0": "image/jpeg",
        "MessageSid": "SM_TEST_IMG_CAPTION_123"
    }
    
    expected_ack = "FarmAI received your crop image. Analyzing it now..."
    
    for path in ["/whatsapp", "/webhook/twilio/whatsapp"]:
        print(f"Testing image + caption flow on path: {path}")
        with patch("routers.twilio_whatsapp.download_twilio_media", new_callable=AsyncMock) as mock_download, \
             patch("routers.twilio_whatsapp.run_crop_analysis", new_callable=AsyncMock) as mock_analyze, \
             patch("routers.twilio_whatsapp.send_twilio_whatsapp_message", new_callable=AsyncMock) as mock_send:
            
            mock_download.return_value = b"fake_image_bytes"
            mock_analyze.return_value = {
                "status": "success",
                "farmer_response": "نائٹروجن کی کمی"
            }
            
            response = client.post(path, data=payload)
            
            # Assert immediate response
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            assert expected_ack in response.text
            
            # Verify background task parameters
            mock_download.assert_called_once_with("https://api.twilio.com/mock-image.jpg")
            args, kwargs = mock_analyze.call_args
            assert kwargs.get("text") == "These cotton leaves are turning yellow"
            assert kwargs.get("image") is not None
            
            mock_send.assert_called_once_with("whatsapp:+923001234567", "نائٹروجن کی کمی")
        
    print("Image + Caption flow test passed successfully for both paths!")

def test_twilio_webhook_download_failure():
    print("\nRunning Twilio WhatsApp Webhook test (Download Failure Background Flow)...")
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
             patch("routers.twilio_whatsapp.run_crop_analysis", new_callable=AsyncMock) as mock_analyze, \
             patch("routers.twilio_whatsapp.send_twilio_whatsapp_message", new_callable=AsyncMock) as mock_send:
            
            mock_download.side_effect = Exception("HTTP 404 Not Found")
            
            response = client.post(path, data=payload)
            
            # The immediate response is still HTTP 200 acknowledgement
            assert response.status_code == 200
            assert "FarmAI received your crop image. Analyzing it now..." in response.text
            
            # Download failed, so analysis should NOT run
            mock_analyze.assert_not_called()
            
            # Outbound warning sent instead
            mock_send.assert_called_once_with(
                "whatsapp:+923001234567",
                "Sorry, FarmAI could not download the image. Please try sending a clear crop image again."
            )
        
    print("Download Failure Fallback test passed successfully for both paths!")

def test_twilio_webhook_analysis_failure():
    print("\nRunning Twilio WhatsApp Webhook test (Analysis Failure Background Flow)...")
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
        print(f"Testing analysis failure fallback on path: {path}")
        with patch("routers.twilio_whatsapp.download_twilio_media", new_callable=AsyncMock) as mock_download, \
             patch("routers.twilio_whatsapp.run_crop_analysis", new_callable=AsyncMock) as mock_analyze, \
             patch("routers.twilio_whatsapp.send_twilio_whatsapp_message", new_callable=AsyncMock) as mock_send:
            
            mock_download.return_value = b"fake_image_bytes"
            mock_analyze.side_effect = Exception("Pipeline crashed")
            
            response = client.post(path, data=payload)
            
            assert response.status_code == 200
            assert "FarmAI received your crop image. Analyzing it now..." in response.text
            
            mock_download.assert_called_once_with("https://api.twilio.com/mock-image.jpg")
            mock_analyze.assert_called_once()
            
            # Outbound error warning sent
            mock_send.assert_called_once_with(
                "whatsapp:+923001234567",
                "Sorry, FarmAI could not process the crop image right now. Please try again with a clear image."
            )
        
    print("Analysis Failure Fallback test passed successfully for both paths!")

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
             patch("routers.twilio_whatsapp.run_crop_analysis", new_callable=AsyncMock) as mock_analyze, \
             patch("routers.twilio_whatsapp.send_twilio_whatsapp_message", new_callable=AsyncMock) as mock_send:
            
            response = client.post(path, data=payload)
            
            # Should NOT start background task
            mock_download.assert_not_called()
            mock_analyze.assert_not_called()
            mock_send.assert_not_called()
            
            # Polite prompt is returned immediately
            assert response.status_code == 200
            assert "Please send a clear crop image or a farming-related text question." in response.text
        
    print("Unsupported Media test passed successfully for both paths!")

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
        with patch("routers.twilio_whatsapp.logger.info", side_effect=RuntimeError("Simulated crash")):
            response = client.post(path, data=payload)
            
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        body_text = response.text
        assert expected_fallback in body_text, f"Expected fallback message missing, got: {body_text}"
        
    print("Unexpected Exception test passed successfully for both paths!")

if __name__ == "__main__":
    test_twilio_webhook_skeleton()
    test_twilio_webhook_text_flow()
    test_twilio_webhook_image_only_acknowledgement()
    test_twilio_webhook_image_caption_flow()
    test_twilio_webhook_download_failure()
    test_twilio_webhook_analysis_failure()
    test_twilio_webhook_unsupported_media()
    test_twilio_webhook_unexpected_exception()
