const fs = require('fs');
const path = require('path');

const STORE_PATH = path.join(__dirname, '../data/store.json');

function ensureDir() {
  const dir = path.dirname(STORE_PATH);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function load() {
  ensureDir();
  if (!fs.existsSync(STORE_PATH)) {
    fs.writeFileSync(STORE_PATH, JSON.stringify([]));
    return [];
  }
  try {
    return JSON.parse(fs.readFileSync(STORE_PATH, 'utf8'));
  } catch {
    return [];
  }
}

function save(data) {
  ensureDir();
  fs.writeFileSync(STORE_PATH, JSON.stringify(data, null, 2));
}

function createItem(data) {
  const id = `${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  const item = {
    id,
    status: 'pending',
    sourceType: data.sourceType,
    sourceId: data.sourceId,
    text: data.text,
    draftReply: data.draftReply,
    createdAt: new Date().toISOString(),
    claimedAt: null
  };
  const store = load();
  store.push(item);
  save(store);
  return item;
}

function getItem(id) {
  const store = load();
  return store.find(item => item.id === id);
}

function updateItem(id, updates) {
  const store = load();
  const item = store.find(item => item.id === id);
  if (item) {
    Object.assign(item, updates);
    save(store);
  }
  return item;
}

function claimForSend(id) {
  const item = getItem(id);
  if (!item) return null;
  if (item.claimedAt) return null;
  return updateItem(id, { claimedAt: new Date().toISOString() });
}

function listQueue(status = 'pending') {
  const store = load();
  return store.filter(item => item.status === status);
}

module.exports = { createItem, getItem, updateItem, claimForSend, listQueue, load, save };
