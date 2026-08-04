const store = require('./store');
const meta = require('./meta');
const config = require('./config');

async function approveItem(itemId) {
  const item = store.claimForSend(itemId);
  if (!item) {
    console.warn(`Item ${itemId} already claimed or not found`);
    return;
  }

  try {
    if (config.DRY_RUN) {
      console.log(`🔄 [DRY RUN] Would send: ${item.draftReply.reply}`);
    } else {
      await meta.sendApprovedReply(item);
      console.log(`✅ Reply sent for ${itemId}`);
    }
    store.updateItem(itemId, { status: 'approved', sentAt: new Date().toISOString() });
  } catch (err) {
    console.error(`❌ Failed to send: ${err.message}`);
    store.updateItem(itemId, { status: 'failed', error: err.message });
  }
}

async function rejectItem(itemId) {
  const item = store.getItem(itemId);
  if (!item) {
    console.warn(`Item ${itemId} not found`);
    return;
  }
  store.updateItem(itemId, { status: 'rejected', rejectedAt: new Date().toISOString() });
  console.log(`❌ Item ${itemId} rejected`);
}

async function editItem(itemId, newReply) {
  const item = store.getItem(itemId);
  if (!item) return;
  item.draftReply.reply = newReply;
  store.updateItem(itemId, { draftReply: item.draftReply, status: 'edited' });
  console.log(`✏️ Item ${itemId} edited`);
}

module.exports = { approveItem, rejectItem, editItem };
