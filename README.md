# mamavdele-agent

Instagram/Facebook comment approval agent with Telegram bot interface.

## Features

- Receives Instagram Direct messages, FEED/REELS comments, and Facebook Page comments via Meta webhooks
- Drafts AI responses using Gemini LLM with business knowledge base
- Sends approval cards to Telegram for human review
- Supports approve, reject, and edit actions
- Multi-language responses matching commenter's language
- Persistent storage with two-phase commit to prevent duplicate sends

## Deployment on Vercel

This app is ready to deploy on Vercel with one click. Environment variables are configured in your Vercel project settings.

## Environment Variables

Set these in your Vercel project settings:

- `META_ACCESS_TOKEN` - Instagram Graph API access token
- `META_APP_SECRET` - Instagram app secret
- `META_IG_USER_ID` - Your Instagram user ID
- `FACEBOOK_PAGE_ACCESS_TOKEN` - Facebook page access token
- `FACEBOOK_PAGE_ID` - Your Facebook page ID
- `FACEBOOK_APP_SECRET` - Facebook app secret
- `LLM_API_KEY` - Gemini API key
- `LLM_BASE_URL` - https://generativelanguage.googleapis.com/openai/
- `LLM_MODEL` - gemini-3.5-flash
- `TELEGRAM_BOT_TOKEN` - Telegram bot token
- `TELEGRAM_CHAT_ID` - Your Telegram chat ID for approvals
- `WEBHOOK_VERIFY_TOKEN` - Random token for webhook verification
- `DRY_RUN` - false (or true for testing)

## Local Development

```bash
npm install
cp .env.example .env
npm start
```
