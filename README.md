# FarmAI WhatsApp / Twilio Backend

This repository is the backend-only version of FarmAI, configured for WhatsApp / Twilio integration.

## Project Details

- **Interface:** The farmer-facing user interface will be WhatsApp (powered by Twilio).
- **Frontend Status:** The original React Native/Expo mobile frontend has been completely removed from this folder.
- **Backend Code:** The existing FarmAI FastAPI/Python backend logic is preserved under the `backend/` directory.
- **Deployments:** This folder is completely disconnected from the old production GitHub repository and has no active Git remotes.

## Preserved Backend Services

- **Gemini AI Integration** (Text & Image Analysis, RAG, custom tools)
- **Speech-to-Text (STT) & Text-to-Speech (TTS)**
- **Weather Services**
- **Knowledge Base** (wheat, cotton, mango, etc.)
- **Static files and routing**

## Deployment Plan

- **Target Platform:** This project will be deployed to a **new, separate web service on Render** (service name placeholder: `farmai-twilio-backend`).
- **Old Deployment Safety:** The original production Render deployment and database are completely untouched and separate.
- **Webhook Configuration:** Once deployed, your Twilio Sandbox / production webhook should point to:
  `https://NEW_RENDER_URL/webhook/twilio/whatsapp`
- **Phased Testing:** 
  1. Text-only WhatsApp message processing will be tested first.
  2. Image, voice/STT, TTS, and location/weather endpoints will be added/connected to WhatsApp in later phases.
