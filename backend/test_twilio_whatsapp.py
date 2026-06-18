import sys
import time
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from fastapi import Request
from main import app

# Import conversation_states and background functions to test/mock them directly
from routers.twilio_whatsapp import (
    conversation_states,
    analyze_image_in_background,
    generate_and_send_tts_summary,
    send_twilio_whatsapp_message
)

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
    
    # Ensure any residual states are cleared
    conversation_states.clear()
    
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

def test_twilio_webhook_text_flow_and_state_transitions():
    print("\nRunning Twilio WhatsApp Webhook test (Text Flow and State Transitions)...")
    payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "my cotton leaves are turning yellow",
        "NumMedia": "0",
        "MessageSid": "SM_TEST_TEXT_123"
    }
    
    mock_advice = "پیلے پتے نائٹروجن کی کمی کو ظاہر کرتے ہیں۔"
    mock_summary = "نائٹروجن کی کمی دور کرنے کے لیے کھاد کا استعمال کریں۔"
    
    # 1. Test Text-Only Flow Saves State and Prompts
    conversation_states.clear()
    with patch("routers.twilio_whatsapp.run_crop_analysis", new_callable=AsyncMock) as mock_analyze:
        mock_analyze.return_value = {
            "status": "success",
            "farmer_response": mock_advice,
            "tts_summary": mock_summary
        }
        
        response = client.post("/webhook/twilio/whatsapp", data=payload)
        
    assert response.status_code == 200
    assert mock_advice in response.text
    assert "Do you want an audio summary of this advice? Reply yes or no." in response.text
    
    # State verification
    user_state = conversation_states.get("whatsapp:+923001234567")
    assert user_state is not None
    assert user_state["last_response_text"] == mock_advice
    assert user_state["tts_summary"] == mock_summary
    assert user_state["pending_tts_confirmation"] is True
    
    # 2. Test Unclear Reply asks again
    unclear_payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "maybe",
        "NumMedia": "0",
        "MessageSid": "SM_TEST_UNCLEAR"
    }
    
    response = client.post("/webhook/twilio/whatsapp", data=unclear_payload)
    assert response.status_code == 200
    assert "Please reply yes or no for the audio summary." in response.text
    assert conversation_states["whatsapp:+923001234567"]["pending_tts_confirmation"] is True
    
    # 3. Test Replying "yes" triggers TTS background flow
    yes_payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "yes",
        "NumMedia": "0",
        "MessageSid": "SM_TEST_YES"
    }
    
    with patch("routers.twilio_whatsapp.generate_and_send_tts_summary", new_callable=AsyncMock) as mock_bg_tts:
        response = client.post("/webhook/twilio/whatsapp", data=yes_payload)
        
    assert response.status_code == 200
    assert "Okay, generating your audio summary now..." in response.text
    assert conversation_states["whatsapp:+923001234567"]["pending_tts_confirmation"] is False
    
    # 4. Test Replying "no" clears conversation state
    # Reset state
    conversation_states["whatsapp:+923001234567"] = {
        "last_response_text": mock_advice,
        "tts_summary": mock_summary,
        "pending_tts_confirmation": True,
        "updated_at": time.time()
    }
    
    no_payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "no",
        "NumMedia": "0",
        "MessageSid": "SM_TEST_NO"
    }
    
    response = client.post("/webhook/twilio/whatsapp", data=no_payload)
    assert response.status_code == 200
    assert "Okay, no audio summary will be sent." in response.text
    assert "whatsapp:+923001234567" not in conversation_states
    
    print("Text Flow and State Transitions tests passed successfully!")

def test_twilio_webhook_image_flow_and_background_task():
    print("\nRunning Twilio WhatsApp Webhook test (Image Flow and Background Task)...")
    payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "cotton leaf",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/mock-image.jpg",
        "MediaContentType0": "image/jpeg",
        "MessageSid": "SM_TEST_IMG"
    }
    
    # 1. Test Webhook immediately acknowledges image
    conversation_states.clear()
    response = client.post("/webhook/twilio/whatsapp", data=payload)
    assert response.status_code == 200
    assert "FarmAI received your crop image. Analyzing it now..." in response.text
    
    # 2. Test analyze_image_in_background directly
    mock_request = MagicMock(spec=Request)
    
    mock_advice = "تصویر میں کپاس کے پتے پر کیڑے کا حملہ دیکھا جا سکتا ہے۔"
    mock_summary = "کپاس پر کیڑے کے حملے کے لیے سپرے کریں۔"
    
    with patch("routers.twilio_whatsapp.download_twilio_media", new_callable=AsyncMock) as mock_download, \
         patch("routers.twilio_whatsapp.run_crop_analysis", new_callable=AsyncMock) as mock_analyze, \
         patch("routers.twilio_whatsapp.send_twilio_whatsapp_message", new_callable=AsyncMock) as mock_send:
             
        mock_download.return_value = b"fake_image_bytes"
        mock_analyze.return_value = {
            "status": "success",
            "farmer_response": mock_advice,
            "tts_summary": mock_summary
        }
        mock_send.return_value = True
        
        import asyncio
        asyncio.run(analyze_image_in_background(
            from_number="whatsapp:+923001234567",
            media_url="https://api.twilio.com/mock-image.jpg",
            content_type="image/jpeg",
            body_text="cotton leaf",
            request=mock_request
        ))
        
        # Verify calls
        mock_download.assert_called_once_with("https://api.twilio.com/mock-image.jpg")
        mock_analyze.assert_called_once()
        mock_send.assert_called_once_with(
            to_number="whatsapp:+923001234567",
            body=mock_advice + "\n\nDo you want an audio summary of this diagnosis? Reply yes or no."
        )
        
    # State check
    user_state = conversation_states.get("whatsapp:+923001234567")
    assert user_state is not None
    assert user_state["last_response_text"] == mock_advice
    assert user_state["tts_summary"] == mock_summary
    assert user_state["pending_tts_confirmation"] is True
    
    print("Image Flow and Background Task tests passed successfully!")

def test_generate_and_send_tts_summary_background_task():
    print("\nRunning background TTS generation and sending task tests...")
    mock_request = MagicMock(spec=Request)
    mock_request.base_url = "http://testserver/"
    
    # 1. Test Successful TTS Generation
    with patch("routers.twilio_whatsapp.generate_tts_audio") as mock_generate_tts, \
         patch("routers.twilio_whatsapp.send_twilio_whatsapp_message", new_callable=AsyncMock) as mock_send:
             
        mock_generate_tts.return_value = {
            "success": True,
            "filename": "tts_test_file.wav"
        }
        mock_send.return_value = True
        
        import asyncio
        asyncio.run(generate_and_send_tts_summary(
            to_number="whatsapp:+923001234567",
            text_to_speak="ٹیسٹ آڈیو خلاصہ",
            request=mock_request
        ))
        
        mock_generate_tts.assert_called_once_with("ٹیسٹ آڈیو خلاصہ")
        mock_send.assert_called_once_with(
            to_number="whatsapp:+923001234567",
            media_url="https://testserver/static/audio/tts_test_file.wav"
        )
        
    # 2. Test TTS Generation Failure Fallback
    with patch("routers.twilio_whatsapp.generate_tts_audio") as mock_generate_tts, \
         patch("routers.twilio_whatsapp.send_twilio_whatsapp_message", new_callable=AsyncMock) as mock_send:
             
        mock_generate_tts.return_value = {
            "success": False,
            "message": "API Quota Exceeded"
        }
        mock_send.return_value = True
        
        import asyncio
        asyncio.run(generate_and_send_tts_summary(
            to_number="whatsapp:+923001234567",
            text_to_speak="ٹیسٹ آڈیو خلاصہ",
            request=mock_request
        ))
        
        mock_send.assert_called_once_with(
            to_number="whatsapp:+923001234567",
            body="Sorry, FarmAI could not generate the audio summary right now."
        )
        
    print("Background TTS task tests passed successfully!")

def test_tts_standalone_endpoint():
    print("\nRunning standalone /tts endpoint test...")
    # Standalone tts tests should still operate normally
    payload = {
        "text": "میری فصل پر پیلا نشان ہے",
        "language_hint": "urdu"
    }
    
    with patch("routers.tts.generate_tts_audio") as mock_tts:
        mock_tts.return_value = {
            "success": True,
            "filename": "tts_standalone_test.wav"
        }
        
        response = client.post("/tts", json=payload)
        
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "static/audio/tts_standalone_test.wav" in response.json()["audio_url"]
    
    print("Standalone /tts test passed successfully!")

if __name__ == "__main__":
    test_twilio_webhook_skeleton()
    test_twilio_webhook_text_flow_and_state_transitions()
    test_twilio_webhook_image_flow_and_background_task()
    test_generate_and_send_tts_summary_background_task()
    test_tts_standalone_endpoint()
