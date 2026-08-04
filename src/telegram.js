const axios = require('axios');
const config = require('./config');
const actions = require('./actions');

const BASE_URL = `https://api.telegram.org/bot${config.TELEGRAM_BOT_TOKEN}`;

async function sendApprovalCard(item) {
  try {
    const message = `📝 *Новый комментарий*\n\n${item.sourceType}\n\n_${item.text.substring(0, 200)}_\n\n💬 *Предложенный ответ:*\n_${item.draftReply.reply.substring(0, 200)}_`;
    await axios.post(`${BASE_URL}/sendMessage`, {
      chat_id: config.TELEGRAM_CHAT_ID,
      text: message,
      parse_mode: 'Markdown',
      reply_markup: {
        inline_keyboard: [
          [
            { text: '✅ Одобрить', callback_data: `approve_${item.id}` },
            { text: '❌ Отклонить', callback_data: `reject_${item.id}` }
          ],
          [
            { text: '✏️ Редактировать', callback_data: `edit_${item.id}` }
          ]
        ]
      }
    });
    console.log(`✅ Approval card sent for ${item.id}`);
  } catch (err) {
    console.error('Telegram send error:', err.message);
  }
}

async function handleCallback(callbackQuery) {
  const data = callbackQuery.data;
  const itemId = data.split('_')[1];
  const action = data.split('_')[0];

  try {
    if (action === 'approve') {
      await actions.approveItem(itemId);
      await answerCallback(callbackQuery.id, '✅ Одобрено');
    } else if (action === 'reject') {
      await actions.rejectItem(itemId);
      await answerCallback(callbackQuery.id, '❌ Отклонено');
    } else if (action === 'edit') {
      await answerCallback(callbackQuery.id, '✏️ Редактирование');
    }
  } catch (err) {
    console.error('Callback error:', err.message);
    await answerCallback(callbackQuery.id, '⚠️ Ошибка');
  }
}

async function answerCallback(callbackQueryId, text) {
  try {
    await axios.post(`${BASE_URL}/answerCallbackQuery`, {
      callback_query_id: callbackQueryId,
      text: text,
      show_alert: false
    });
  } catch (err) {
    console.error('Answer callback error:', err.message);
  }
}

async function startPolling() {
  let offset = 0;
  console.log('🤖 Telegram polling started');

  setInterval(async () => {
    try {
      const response = await axios.get(`${BASE_URL}/getUpdates`, {
        params: { offset, allowed_updates: ['message', 'callback_query'] }
      });

      for (const update of response.data.result) {
        offset = update.update_id + 1;

        if (update.callback_query) {
          await handleCallback(update.callback_query);
        }
      }
    } catch (err) {
      console.error('Polling error:', err.message);
    }
  }, 1000);
}

module.exports = { sendApprovalCard, startPolling, handleCallback };
