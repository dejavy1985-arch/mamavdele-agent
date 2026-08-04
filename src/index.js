const http = require('http');
const config = require('./config');
const meta = require('./meta');
const llm = require('./llm');
const telegram = require('./telegram');
const store = require('./store');

const PORT = config.WEBHOOK_PORT || 3000;

const server = http.createServer(async (req, res) => {
  if (req.url === '/webhook') {
    if (req.method === 'GET') {
      handleWebhookVerification(req, res);
    } else if (req.method === 'POST') {
      handleWebhookPost(req, res);
    }
  } else {
    res.writeHead(404);
    res.end('Not found');
  }
});

function handleWebhookVerification(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const mode = url.searchParams.get('hub.mode');
  const token = url.searchParams.get('hub.verify_token');
  const challenge = url.searchParams.get('hub.challenge');

  if (mode === 'subscribe' && token === config.WEBHOOK_VERIFY_TOKEN) {
    res.writeHead(200);
    res.end(challenge);
    console.log('✅ Webhook verified');
  } else {
    res.writeHead(403);
    res.end('Forbidden');
  }
}

async function handleWebhookPost(req, res) {
  let body = '';
  req.on('data', chunk => { body += chunk; });
  req.on('end', async () => {
    try {
      const webhook = JSON.parse(body);

      if (!meta.validMetaSignature(body, req.headers['x-hub-signature-256'])) {
        res.writeHead(403);
        res.end('Invalid signature');
        return;
      }

      res.writeHead(200);
      res.end('OK');

      const parsed = meta.parseWebhook(webhook);
      const allMessages = [
        ...parsed.directMessages,
        ...parsed.instagramComments,
        ...parsed.facebookComments
      ];

      for (const msg of allMessages) {
        try {
          const draftReply = await llm.draftReply(msg.text);
          const item = store.createItem({
            sourceType: msg.sourceType,
            sourceId: msg.sourceId,
            text: msg.text,
            draftReply: draftReply
          });
          await telegram.sendApprovalCard(item);
        } catch (err) {
          console.error(`Error processing message: ${err.message}`);
        }
      }
    } catch (err) {
      console.error(`Webhook error: ${err.message}`);
      res.writeHead(500);
      res.end('Error');
    }
  });
}

server.listen(PORT, () => {
  console.log(`🚀 Agent running on port ${PORT}`);
  console.log(`📡 Webhook URL: https://dejavy8t.beget.tech/webhook`);
  telegram.startPolling();
});

process.on('SIGTERM', () => {
  console.log('Shutting down gracefully...');
  server.close();
  process.exit(0);
});
