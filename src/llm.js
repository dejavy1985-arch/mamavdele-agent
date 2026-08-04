const axios = require('axios');
const config = require('./config');

function buildSystemPrompt() {
  const knowledge = require('../data/knowledge.json');
  return `Ты ассистент Ларисы (@mamavdele.ai) для AI-видео и AI-агентов. Отвечай кратко на языке пользователя. Продукты: ${JSON.stringify(knowledge.products)}. На оскорбления - твёрдый ответ без извинений. На юридику/скидки - скажи что нужен handoff.`;
}

async function draftReply(message) {
  try {
    const response = await axios.post(`${config.LLM_BASE_URL}chat/completions`, {
      model: config.LLM_MODEL,
      messages: [
        { role: 'system', content: buildSystemPrompt() },
        { role: 'user', content: message }
      ],
      temperature: 0.7,
      max_tokens: 300
    });
    const text = response.data.choices[0].message.content;
    try {
      return JSON.parse(text);
    } catch {
      return { handoff: false, risk: 'normal', reply: text.substring(0, 300) };
    }
  } catch (err) {
    console.error('LLM error:', err.message);
    return { handoff: true, risk: 'normal', reply: '' };
  }
}

module.exports = { buildSystemPrompt, draftReply };
