const test = require('node:test');
const assert = require('node:assert/strict');
const { parseMarkdown, safeHttpUrl, restoreConversation, saveConversation,
  readHttpError, sessionResetNotice } = require('./chat.js');

test('accepts only absolute HTTP and HTTPS links', () => {
  assert.equal(safeHttpUrl('https://maps.google.com/?q=NYC'), 'https://maps.google.com/?q=NYC');
  assert.equal(safeHttpUrl('http://example.com'), 'http://example.com/');
  for (const unsafe of ['javascript:alert(1)', 'data:text/html,x', '//example.com', '/relative', 'https://']) {
    assert.equal(safeHttpUrl(unsafe), null);
  }
});

test('parses headings, bullets and tables while retaining text as data', () => {
  const blocks = parseMarkdown('# Choice\n\n- Alice: **25 min**\n- Bob: 30 min\n\n| Place | Time |\n| --- | ---: |\n| Union Square | 30 |\n\n<script>alert(1)</script>');
  assert.deepEqual(blocks.map(block => block.type), ['heading', 'list', 'table', 'paragraph']);
  assert.deepEqual(blocks[1].items[0], [
    { type: 'text', text: 'Alice: ' }, { type: 'bold', text: '25 min' }
  ]);
  assert.equal(blocks[2].rows[0][0][0].text, 'Union Square');
  assert.equal(blocks[3].content[0].text, '<script>alert(1)</script>');
});

test('keeps unsafe markdown links as plain text and recognizes bare Maps links', () => {
  const [paragraph] = parseMarkdown('[unsafe](javascript:alert(1)) and https://maps.google.com/?q=Union+Square');
  assert.equal(paragraph.content.some(token => token.type === 'link' && token.href.startsWith('javascript:')), false);
  assert.equal(paragraph.content.find(token => token.type === 'link').href, 'https://maps.google.com/?q=Union+Square');
});

test('round-trips one tab conversation and discards malformed storage', () => {
  const data = new Map();
  const storage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) };
  const conversation = { sessionId: 'tab-id', messages: [
    { role: 'user', text: 'Hello' },
    { role: 'assistant', text: 'Hi', toolCalls: [{ name: 'get_group_routes', args: {}, result: {} }] }
  ] };
  saveConversation(storage, conversation);
  assert.deepEqual(restoreConversation(storage), conversation);
  data.set('meetfair.conversation.v1', '{broken');
  assert.deepEqual(restoreConversation(storage), { sessionId: null, messages: [] });
  data.set('meetfair.conversation.v1', JSON.stringify({ sessionId: 'bad', messages: [{ role: 'user', text: 1 }] }));
  assert.deepEqual(restoreConversation(storage), { sessionId: null, messages: [] });
});

test('surfaces safe backend detail and gives chat-full a New chat action', async () => {
  const full = await readHttpError({ status: 409, json: async () => ({ detail: 'This chat is full. Start a new chat to continue.' }) });
  assert.equal(full.message, 'This chat is full. Start a new chat to continue.');
  assert.equal(full.action, 'new-chat');
  const pending = await readHttpError({ status: 409, json: async () => ({ detail: 'This chat is still processing a message. Wait for its reply.' }) });
  assert.equal(pending.action, 'wait');
  const capacity = await readHttpError({ status: 503, json: async () => ({ detail: 'Chat capacity reached. Retry later or clear an unused chat.' }) });
  assert.equal(capacity.message, 'Chat capacity reached. Retry later or clear an unused chat.');
  assert.equal(capacity.action, 'retry');
});

test('HTTP errors fall back for unreadable, validation or unsafe details', async () => {
  assert.deepEqual(await readHttpError({ status: 422, json: async () => ({ detail: [{ msg: 'invalid' }] }) }),
    { message: 'Check your message and try again.', action: 'retry' });
  assert.equal((await readHttpError({ status: 500, json: async () => { throw Error('bad JSON'); } })).message, 'Server returned 500.');
  assert.equal((await readHttpError({ status: 500, json: async () => ({ detail: '<script>bad</script>' }) })).message, 'Server returned 500.');
  assert.equal((await readHttpError({ status: 500, json: async () => ({ detail: 'API key: secret-value' }) })).message, 'Server returned 500.');
});

test('detects server session reset only after an established chat', () => {
  assert.match(sessionResetNotice('old', 'new', true), /context has reset/i);
  assert.equal(sessionResetNotice('old', 'new', false), null);
  assert.equal(sessionResetNotice('same', 'same', true), null);
});

const { openGuide, requestMessage, hasServerReply, inputLimitFor } = require('./chat.js');

test('feature entry opens an assistant guide without creating or sending a user query', () => {
  assert.equal(typeof openGuide, 'function');
  const messages = [];
  assert.equal(openGuide(messages, 'meetup'), true);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].role, 'assistant');
  assert.match(messages[0].text, /starting/i);
  assert.equal(messages.some(message => message.role === 'user' || message.status === 'pending'), false);
  assert.equal(hasServerReply(messages), false);
  assert.equal(openGuide(messages, 'places'), true);
  assert.equal(messages.length, 1, 'switching entry before typing should replace the guide');
  assert.equal(openGuide(messages, 'invalid'), false);
  assert.equal(messages.length, 1);
});

test('the first real reply carries the chosen goal and the user\'s own details', () => {
  assert.equal(typeof requestMessage, 'function');
  const messages = [];
  openGuide(messages, 'departures');
  const userText = 'Sam: Penn Station, Manhattan. Jo: Atlantic Terminal, Brooklyn. Friday at 7 PM.';
  const payload = requestMessage(userText, messages);
  assert.ok(payload.endsWith(userText));
  assert.match(payload, /leave/i);
  assert.doesNotMatch(payload, /Alice|Bob|Carol|tomorrow|10 minutes/);
  assert.ok(payload.length <= 4000);
  assert.equal(inputLimitFor(messages) + requestMessage('', messages).length, 4000);
  assert.throws(() => requestMessage('x'.repeat(inputLimitFor(messages) + 1), messages), /too long/i);
});

test('guide follow-ups reuse the real group and only ask for missing information', () => {
  assert.equal(typeof openGuide, 'function');
  const messages = [{ role: 'user', text: 'Sam and Jo at Penn Station.' }, { role: 'assistant', text: 'Union Square works.' }];
  assert.equal(hasServerReply(messages), true);
  openGuide(messages, 'limits');
  assert.doesNotMatch(messages.at(-1).text, /starting|address/i);
  assert.match(messages.at(-1).text, /minutes/i);
  assert.match(requestMessage('Sam: 20 minutes. Jo: 30 minutes.', messages), /time limit/i);
  messages.push({ role: 'user', text: 'Sam: 20 minutes. Jo: 30 minutes.' }, { role: 'assistant', text: 'No place works.' });
  assert.equal(requestMessage('Try another place.', messages), 'Try another place.');
});

test('refresh keeps an unsent guide without claiming that a server chat has started', () => {
  assert.equal(typeof openGuide, 'function');
  const data = new Map();
  const storage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) };
  const conversation = { sessionId: 'client-id', messages: [] };
  openGuide(conversation.messages, 'places');
  saveConversation(storage, conversation);
  const restored = restoreConversation(storage);
  assert.equal(hasServerReply(restored.messages), false);
  assert.match(requestMessage('Sam and Jo start at Penn Station, Manhattan. We want cafes.', restored.messages), /nearby/i);
  assert.equal(sessionResetNotice('client-id', 'server-id', hasServerReply(restored.messages)), null);
});

test('clicking a feature makes no network request; typing real details sends one chat request', async () => {
  const fs = require('node:fs');
  const vm = require('node:vm');
  class Node {
    constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.dataset = {}; this.value = ''; this.textContent = ''; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
    dispatch(name) { for (const handler of this.listeners[name] || []) handler({ preventDefault() {} }); }
    click() { this.dispatch('click'); }
    focus() {}
    scrollIntoView() {}
    querySelector(selector) { return this.children.find(child => child.className === selector.slice(1)) || null; }
    get lastElementChild() { return this.children.at(-1); }
    allText() { return this.textContent + this.children.map(child => child.allText()).join(' '); }
  }
  const ids = Object.fromEntries(['transcript', 'welcome', 'chat-form', 'message-input', 'send-button', 'new-chat', 'notice', 'followup-actions'].map(id => [id, new Node(id)]));
  const entry = new Node('button'); entry.dataset.guide = 'meetup';
  const data = new Map();
  const requests = [];
  const document = {
    readyState: 'complete', getElementById: id => ids[id],
    querySelectorAll: () => [entry], createElement: tag => new Node(tag),
    createTextNode: text => { const node = new Node('#text'); node.textContent = text; return node; }
  };
  vm.runInNewContext(fs.readFileSync(require.resolve('./chat.js'), 'utf8'), {
    document, URL, crypto: { randomUUID: () => 'client-id' }, module: { exports: {} },
    sessionStorage: { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) },
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { ok: true, json: async () => ({ response: 'Please share Jo\'s station.', session_id: 'server-id', tool_calls: [] }) };
    }
  });
  entry.click();
  assert.equal(requests.length, 0, 'entry should not call Gemini or Maps');
  assert.equal(ids.transcript.children.length, 1);
  assert.match(ids.transcript.allText(), /starting/i);
  assert.equal(ids.welcome.hidden, true);
  ids['message-input'].value = 'Sam: Penn Station, Manhattan. Jo: Brooklyn.';
  ids['chat-form'].dispatch('submit');
  await new Promise(setImmediate);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/chat');
  const body = JSON.parse(requests[0].options.body);
  assert.ok(body.message.endsWith('Sam: Penn Station, Manhattan. Jo: Brooklyn.'));
  assert.doesNotMatch(body.message, /Alice|Bob|Carol/);
  assert.equal(ids.transcript.children.length, 3, 'first real reply must not add a false session-reset notice');
  assert.equal(ids['send-button'].disabled, false);
  ids['new-chat'].click();
  await new Promise(setImmediate);
  assert.equal(requests.at(-1).url, '/clear?session_id=server-id');
  assert.equal(ids.transcript.children.length, 0);
  assert.equal(ids.welcome.hidden, false);
  entry.click();
  assert.equal(requests.length, 2);
  assert.equal(ids.transcript.children.length, 1);
});
