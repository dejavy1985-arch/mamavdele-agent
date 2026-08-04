const axios = require('axios');
const crypto = require('crypto');
const config = require('./config');

function validMetaSignature(body, signature) {
  if (!signature) return false;
  const parts = signature.split('=');
  const algorithm = parts[0];
  const hash = parts[1];

  const secret = config.META_APP_SECRET || config.FACEBOOK_APP_SECRET;
  const computed = crypto.createHmac('sha256', secret).update(body).digest('hex');
  return computed === hash;
}

function parseWebhook(webhook) {
  const result = {
    directMessages: [],
    instagramComments: [],
    facebookComments: []
  };

  if (webhook.entry) {
    for (const entry of webhook.entry) {
      if (entry.messaging) {
        for (const msg of entry.messaging) {
          if (msg.message && !msg.message.is_echo) {
            result.directMessages.push({
              sourceType: 'Instagram DM',
              sourceId: msg.sender.id,
              text: msg.message.text || '',
              senderId: msg.sender.id
            });
          }
        }
      }
      if (entry.changes) {
        for (const change of entry.changes) {
          if (change.field === 'comments' && change.value) {
            const comment = change.value;
            if (comment.from && comment.from.id !== config.META_IG_USER_ID && comment.from.id !== config.FACEBOOK_PAGE_ID) {
              result.instagramComments.push({
                sourceType: 'Instagram Comment',
                sourceId: comment.id,
                text: comment.text || '',
                senderId: comment.from.id
              });
            }
          }
        }
      }
    }
  }

  return result;
}

async function sendApprovedReply(item) {
  if (item.sourceType === 'Instagram DM') {
    return sendDirectReply(item);
  } else if (item.sourceType === 'Instagram Comment') {
    return sendInstagramReply(item);
  } else if (item.sourceType === 'Facebook Comment') {
    return sendFacebookReply(item);
  }
}

async function sendDirectReply(item) {
  try {
    await axios.post(`https://graph.instagram.com/v26.0/${item.sourceId}/messages`, {
      message_type: 'TEXT',
      recipient_type: 'individual',
      text: item.draftReply.reply
    }, {
      params: { access_token: config.META_ACCESS_TOKEN }
    });
  } catch (err) {
    throw new Error(`Failed to send DM: ${err.message}`);
  }
}

async function sendInstagramReply(item) {
  try {
    await axios.post(`https://graph.instagram.com/v26.0/${item.sourceId}/replies`, {
      text: item.draftReply.reply
    }, {
      params: { access_token: config.META_ACCESS_TOKEN }
    });
  } catch (err) {
    throw new Error(`Failed to send Instagram reply: ${err.message}`);
  }
}

async function sendFacebookReply(item) {
  try {
    await axios.post(`https://graph.facebook.com/v26.0/${item.sourceId}/comments`, {
      message: item.draftReply.reply
    }, {
      params: { access_token: config.FACEBOOK_PAGE_ACCESS_TOKEN }
    });
  } catch (err) {
    throw new Error(`Failed to send Facebook reply: ${err.message}`);
  }
}

module.exports = { validMetaSignature, parseWebhook, sendApprovedReply, sendDirectReply, sendInstagramReply, sendFacebookReply };
