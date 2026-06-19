import sys
sys.stdout.reconfigure(encoding='utf-8')
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
    send_twilio_whatsapp_message,
    sanitize_text_for_tts
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
    assert mock_bg_tts.call_count == 1
    call_args, call_kwargs = mock_bg_tts.call_args
    assert call_kwargs.get("to_number") == "whatsapp:+923001234567"
    assert call_kwargs.get("text_to_speak") == "نائٹروجن کی کمی دور کرنے کے لیے کھاد کا استعمال کریں۔"
    assert call_kwargs.get("language_hint") == "urdu"
    
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
    
    mock_stat_res = MagicMock()
    mock_stat_res.st_size = 1000
    
    # 1. Test Successful TTS Generation and Successful OGG Conversion
    with patch("routers.twilio_whatsapp.generate_tts_audio") as mock_generate_tts, \
         patch("services.tts_service.convert_wav_to_ogg_opus") as mock_convert_ogg, \
         patch("routers.twilio_whatsapp.send_twilio_whatsapp_message", new_callable=AsyncMock) as mock_send, \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.stat", return_value=mock_stat_res):
              
         mock_generate_tts.return_value = {
              "success": True,
              "filename": "tts_test_file.wav"
          }
         mock_convert_ogg.return_value = "tts_test_file.ogg"
         mock_send.return_value = True
         
         import asyncio
         asyncio.run(generate_and_send_tts_summary(
              to_number="whatsapp:+923001234567",
              text_to_speak="ٹیسٹ آڈیو خلاصہ",
              base_url="https://testserver",
              language_hint="urdu"
          ))
         
         mock_generate_tts.assert_called_once_with("ٹیسٹ آڈیو خلاصہ", language_hint="urdu")
         mock_convert_ogg.assert_called_once_with("tts_test_file.wav")
         mock_send.assert_called_once_with(
              to_number="whatsapp:+923001234567",
              media_url="https://testserver/static/audio/tts_test_file.ogg"
          )
           
    # 2. Test Successful TTS Generation but OGG Conversion Fails (No WAV fallback, sends error text)
    with patch("routers.twilio_whatsapp.generate_tts_audio") as mock_generate_tts, \
         patch("services.tts_service.convert_wav_to_ogg_opus") as mock_convert_ogg, \
         patch("routers.twilio_whatsapp.send_twilio_whatsapp_message", new_callable=AsyncMock) as mock_send, \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.stat", return_value=mock_stat_res):
              
         mock_generate_tts.return_value = {
              "success": True,
              "filename": "tts_test_file.wav"
          }
         mock_convert_ogg.side_effect = Exception("ffmpeg failed")
         mock_send.return_value = True
         
         import asyncio
         asyncio.run(generate_and_send_tts_summary(
              to_number="whatsapp:+923001234567",
              text_to_speak="ٹیسٹ آڈیو خلاصہ",
              base_url="https://testserver",
              language_hint="urdu"
          ))
         
         mock_generate_tts.assert_called_once_with("ٹیسٹ آڈیو خلاصہ", language_hint="urdu")
         mock_convert_ogg.assert_called_once_with("tts_test_file.wav")
         mock_send.assert_called_once_with(
              to_number="whatsapp:+923001234567",
              body="Audio summary could not be generated right now. Please try again."
          )

    # 3. Test TTS Generation Failure Fallback
    with patch("routers.twilio_whatsapp.generate_tts_audio") as mock_generate_tts, \
         patch("routers.twilio_whatsapp.send_twilio_whatsapp_message", new_callable=AsyncMock) as mock_send, \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.stat", return_value=mock_stat_res):
              
         mock_generate_tts.return_value = {
              "success": False,
              "message": "API Quota Exceeded"
          }
         mock_send.return_value = True
         
         import asyncio
         asyncio.run(generate_and_send_tts_summary(
              to_number="whatsapp:+923001234567",
              text_to_speak="ٹیسٹ آڈیو خلاصہ",
              base_url="https://testserver"
          ))
         
         mock_send.assert_called_once_with(
              to_number="whatsapp:+923001234567",
              body="Audio summary could not be generated right now. Please try again."
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

def test_sanitize_text_for_tts():
    print("\nRunning sanitize_text_for_tts tests...")
    
    # 1. Emojis, colons, XML, URLs, bullets, markdown removal
    raw_text = "<b>Possible Issue:</b> ⚠ **Cotton curl**.\n- Check leaves.\nhttps://google.com"
    clean_text = sanitize_text_for_tts(raw_text)
    assert "Possible Issue" in clean_text
    assert "Cotton curl" in clean_text
    assert "Check leaves" in clean_text
    assert "⚠" not in clean_text
    assert ":" not in clean_text
    assert "-" not in clean_text
    assert "https" not in clean_text
    assert "<b>" not in clean_text
    
    # 2. Character limit trim
    long_text = "A. " * 500  # 1500 chars
    clean_long = sanitize_text_for_tts(long_text)
    assert len(clean_long) <= 1300
    
    print("sanitize_text_for_tts tests passed successfully!")

def test_tts_service_no_instructions():
    print("\nRunning test_tts_service_no_instructions...")
    from services.tts_service import generate_tts_audio
    
    with patch("google.generativeai.GenerativeModel") as mock_model_class:
        mock_model_instance = mock_model_class.return_value
        mock_model_instance.generate_content.return_value = MagicMock()
        
        # We need to mock open/os.makedirs/pcm_to_wav/etc. so it doesn't write files or fail during parsing
        with patch("services.tts_service.pcm_to_wav") as mock_pcm_to_wav, \
             patch("builtins.open", create=True) as mock_open, \
             patch("os.makedirs") as mock_makedirs:
             
             # Mock the audio response structure
             mock_response = MagicMock()
             candidate = MagicMock()
             part = MagicMock()
             part.inline_data.data = b"fake_pcm"
             candidate.content.parts = [part]
             mock_response.candidates = [candidate]
             mock_model_instance.generate_content.return_value = mock_response
             mock_pcm_to_wav.return_value = b"fake_wav"
             
             res = generate_tts_audio("ٹیسٹ خلاصہ", language_hint="urdu")
             
             assert res["success"] is True
             # Check what was passed to generate_content
             mock_model_instance.generate_content.assert_called_once()
             call_args, call_kwargs = mock_model_instance.generate_content.call_args
             prompt_sent = call_args[0]
             
             print("Prompt sent to Gemini TTS:", prompt_sent)
             assert "Read this text aloud clearly" in prompt_sent
             assert "ٹیسٹ خلاصہ" in prompt_sent
             
             # Check that it doesn't contain forbidden instructions
             for forbidden in ["summarize", "explain", "rewrite", "generate response", "headings analysis", "markdown instructions"]:
                 assert forbidden not in prompt_sent.lower()
                 
    print("test_tts_service_no_instructions passed successfully!")

def test_production_specific_flows():
    print("\nRunning test_production_specific_flows...")
    
    # 1. Test response trimming helper
    from routers.twilio_whatsapp import trim_to_max_chars
    long_text = "Urdu sentence۔ " * 200 # very long text
    trimmed = trim_to_max_chars(long_text, 1500)
    assert len(trimmed) <= 1500
    assert trimmed.endswith("۔") or trimmed.endswith("...")
    
    # 2. Test response trimming in webhook text flow
    conversation_states.clear()
    payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "cotton query",
        "NumMedia": "0",
        "MessageSid": "SM_TEST_PROD_1"
    }
    
    # Mocking extremely long farmer response (> 1600 characters)
    huge_farmer_response = "ضروری معلومات: " + ("کپاس کی فصل کے لیے کھاد کا استعمال بہت اہم ہے۔ " * 40)
    huge_tts_summary = "کپاس کے لیے سپرے کریں۔ " * 30
    
    with patch("routers.twilio_whatsapp.run_crop_analysis", new_callable=AsyncMock) as mock_analyze:
        mock_analyze.return_value = {
            "status": "success",
            "farmer_response": huge_farmer_response,
            "tts_summary": huge_tts_summary
        }
        
        response = client.post("/webhook/twilio/whatsapp", data=payload)
        
    assert response.status_code == 200
    # The returned TwiML message should be <= 1500 chars (plain text length extracted from TwiML)
    import xml.etree.ElementTree as ET
    root = ET.fromstring(response.text)
    msg_body = root.find("Message").text
    print(f"Truncated message length (with prompt): {len(msg_body)}")
    assert len(msg_body) <= 1500
    assert "Do you want an audio summary of this advice?" in msg_body

    # 3. Test language-specific transcript limits in sanitize_text_for_tts
    # English <= 1200 chars
    long_english = "Cotton is a key crop. " * 80 # 1760 chars
    clean_en = sanitize_text_for_tts(long_english, language_hint="english")
    assert len(clean_en) <= 1300
    # Roman Urdu <= 1000 chars
    long_roman = "Kapas ki fasal bahut aham hai. " * 50 # 1500 chars
    clean_roman = sanitize_text_for_tts(long_roman, language_hint="roman_urdu")
    assert len(clean_roman) <= 1100
    # Urdu script <= 1000 chars
    long_urdu = "کپاس کی فصل بہت اہم ہے۔ " * 50 # 1200 chars
    clean_ur = sanitize_text_for_tts(long_urdu, language_hint="urdu")
    assert len(clean_ur) <= 1100

    # 4. Test "yes" without pending state returns polite instructions, not refusal
    conversation_states.clear()
    yes_payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "yes",
        "NumMedia": "0",
        "MessageSid": "SM_TEST_PROD_YES"
    }
    response = client.post("/webhook/twilio/whatsapp", data=yes_payload)
    assert response.status_code == 200
    root = ET.fromstring(response.text)
    assert "No pending audio summary found" in root.find("Message").text

    # 5. Test "no" without pending state returns "Okay, no audio summary" and clears state
    no_payload = {
        "From": "whatsapp:+923001234567",
        "To": "whatsapp:+14155238886",
        "Body": "no",
        "NumMedia": "0",
        "MessageSid": "SM_TEST_PROD_NO"
    }
    response = client.post("/webhook/twilio/whatsapp", data=no_payload)
    assert response.status_code == 200
    root = ET.fromstring(response.text)
    assert "Okay, no audio summary will be sent" in root.find("Message").text

    # 6. Test background task logs audio URL before sending even if Twilio returns 429
    mock_stat_res = MagicMock()
    mock_stat_res.st_size = 1000
    with patch("routers.twilio_whatsapp.generate_tts_audio") as mock_generate_tts, \
         patch("services.tts_service.convert_wav_to_ogg_opus") as mock_convert_ogg, \
         patch("routers.twilio_whatsapp.send_twilio_whatsapp_message", new_callable=AsyncMock) as mock_send, \
         patch("routers.twilio_whatsapp.logger.info") as mock_logger_info, \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.stat", return_value=mock_stat_res):
              
         mock_generate_tts.return_value = {
              "success": True,
              "filename": "tts_test_file.wav"
          }
         mock_convert_ogg.return_value = "tts_test_file.ogg"
         # Mock Twilio returning False (e.g. failure like 429)
         mock_send.return_value = False
         
         import asyncio
         asyncio.run(generate_and_send_tts_summary(
              to_number="whatsapp:+923001234567",
              text_to_speak="ٹیسٹ آڈیو خلاصہ",
              base_url="https://testserver",
              language_hint="urdu"
          ))
         
         # Assert send was called twice (once for audio media_url, once for fallback text)
         assert mock_send.call_count == 2
         
         # Assert that "Generated WhatsApp TTS audio URL" was logged before/during execution
         log_messages = []
         for call in mock_logger_info.call_args_list:
             fmt = call[0][0]
             args = call[0][1:]
             if args:
                 log_messages.append(fmt % args)
             else:
                 log_messages.append(fmt)
                 
         url_logged = any("Generated WhatsApp TTS audio URL" in msg for msg in log_messages)
         assert url_logged is True
         
    print("test_production_specific_flows passed successfully!")

if __name__ == "__main__":
    test_twilio_webhook_skeleton()
    test_twilio_webhook_text_flow_and_state_transitions()
    test_twilio_webhook_image_flow_and_background_task()
    test_generate_and_send_tts_summary_background_task()
    test_tts_standalone_endpoint()
    test_sanitize_text_for_tts()
    test_tts_service_no_instructions()
    test_production_specific_flows()
