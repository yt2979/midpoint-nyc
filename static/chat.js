(function () {
  'use strict';

  const STORAGE_KEY = 'meetfair.conversation.v1';
  const TOOL_LABELS = {
    get_group_routes: 'Compare group routes',
    evaluate_meeting_fairness: 'Check fairness',
    search_nearby_places: 'Search nearby places',
    plan_group_departures: 'Plan when to leave'
  };

  const GUIDES = Object.freeze({
    meetup: {
      text: "I can help you find a fair meeting spot. Please share each person's name and starting address or station.",
      followup: 'I can compare new meeting spots. What would you like to change?',
      intent: 'Help me find a fair meeting spot for my group.',
      placeholder: 'Names and starting addresses or stations…'
    },
    limits: {
      text: "I can find a spot that fits your time limits. Please share each person's starting point and time limit in minutes.",
      followup: 'How many minutes can each person travel at most?',
      intent: "Help me choose a meeting spot that fits each person's time limit. Ask for any missing limits before checking places.",
      placeholder: 'Each person’s starting point and time limit…'
    },
    places: {
      text: 'I can find food and things to do near a fair meeting spot. Where is each person starting from, and what would you like to do?',
      followup: 'Would you like restaurants, cafes, or things to do?',
      intent: 'Help me find nearby food or things to do for our meetup.',
      placeholder: 'Share your starting points and what you would like to do…'
    },
    departures: {
      text: 'I can plan when each person should leave. Where is everyone starting from, and what day and time should you meet? Use New York time.',
      followup: 'What day and time should everyone meet? Please use New York time.',
      intent: 'Help me plan when everyone should leave for our meetup. Ask for the meeting date and time if missing.',
      placeholder: 'Starting points, meeting date, and time in New York…'
    }
  });

  function guideFor(key) {
    return typeof key === 'string' && Object.hasOwn(GUIDES, key) ? GUIDES[key] : null;
  }

  function pendingGuide(messages) {
    const last = messages.at(-1);
    return last?.role === 'assistant' ? guideFor(last.guide) : null;
  }

  function hasServerReply(messages) {
    return messages.some(message => message.role === 'assistant' && !guideFor(message.guide));
  }

  function openGuide(messages, key) {
    const guide = guideFor(key);
    if (!guide) return false;
    const message = { role: 'assistant', text: hasServerReply(messages) ? guide.followup : guide.text, guide: key };
    if (pendingGuide(messages)) messages[messages.length - 1] = message;
    else messages.push(message);
    return true;
  }

  function requestMessage(text, history) {
    const guide = pendingGuide(history);
    const message = guide ? `${guide.intent}\n\n${text}` : text;
    if (message.length > 4000) throw new Error('Your message is too long. Please shorten it.');
    return message;
  }

  function inputLimitFor(history) {
    return 4000 - requestMessage('', history).length;
  }

  function safeHttpUrl(value) {
    if (typeof value !== 'string' || !/^https?:\/\//i.test(value.trim())) return null;
    try {
      const url = new URL(value.trim());
      return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch (_) {
      return null;
    }
  }

  function parseInline(source) {
    const tokens = [];
    const pattern = /\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)|(https?:\/\/[^\s<>()]+)/gi;
    let cursor = 0;
    let match;
    while ((match = pattern.exec(source)) !== null) {
      if (match.index > cursor) tokens.push({ type: 'text', text: source.slice(cursor, match.index) });
      if (match[1]) {
        tokens.push({ type: 'bold', text: match[1] });
      } else if (match[2]) {
        const href = safeHttpUrl(match[3]);
        tokens.push(href ? { type: 'link', text: match[2], href } : { type: 'text', text: match[0] });
      } else {
        const trimmed = match[4].replace(/[.,;:!?]+$/, '');
        const href = safeHttpUrl(trimmed);
        tokens.push(href ? { type: 'link', text: trimmed, href } : { type: 'text', text: match[4] });
        if (trimmed.length < match[4].length) tokens.push({ type: 'text', text: match[4].slice(trimmed.length) });
      }
      cursor = pattern.lastIndex;
    }
    if (cursor < source.length) tokens.push({ type: 'text', text: source.slice(cursor) });
    return tokens;
  }

  function splitTableRow(line) {
    return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => parseInline(cell.trim()));
  }

  function isTableStart(lines, index) {
    return index + 1 < lines.length && lines[index].includes('|') &&
      /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1]);
  }

  function parseMarkdown(value) {
    const lines = String(value ?? '').replace(/\r\n?/g, '\n').split('\n');
    const blocks = [];
    let index = 0;
    while (index < lines.length) {
      if (!lines[index].trim()) { index++; continue; }
      const heading = /^(#{1,3})\s+(.+)$/.exec(lines[index]);
      if (heading) {
        blocks.push({ type: 'heading', level: heading[1].length, content: parseInline(heading[2]) });
        index++;
        continue;
      }
      if (isTableStart(lines, index)) {
        const headers = splitTableRow(lines[index]);
        const rows = [];
        index += 2;
        while (index < lines.length && lines[index].trim() && lines[index].includes('|')) {
          rows.push(splitTableRow(lines[index]));
          index++;
        }
        blocks.push({ type: 'table', headers, rows });
        continue;
      }
      if (/^\s*[-*]\s+/.test(lines[index])) {
        const items = [];
        while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
          items.push(parseInline(lines[index].replace(/^\s*[-*]\s+/, '')));
          index++;
        }
        blocks.push({ type: 'list', items });
        continue;
      }
      const paragraph = [];
      while (index < lines.length && lines[index].trim() &&
        !/^(#{1,3})\s+/.test(lines[index]) &&
        !/^\s*[-*]\s+/.test(lines[index]) &&
        !isTableStart(lines, index)) {
        paragraph.push(lines[index].trim());
        index++;
      }
      if (paragraph.length) blocks.push({ type: 'paragraph', content: parseInline(paragraph.join(' ')) });
    }
    return blocks;
  }

  function restoreConversation(storage) {
    const empty = { sessionId: null, messages: [] };
    try {
      const raw = storage.getItem(STORAGE_KEY);
      if (!raw) return empty;
      const parsed = JSON.parse(raw);
      if (typeof parsed.sessionId !== 'string' || !Array.isArray(parsed.messages)) return empty;
      if (!parsed.messages.every(message => message &&
        ['user', 'assistant'].includes(message.role) && typeof message.text === 'string' &&
        (message.toolCalls === undefined || Array.isArray(message.toolCalls)) &&
        (message.status === undefined || ['pending', 'failed'].includes(message.status)))) return empty;
      return { sessionId: parsed.sessionId, messages: parsed.messages };
    } catch (_) {
      return empty;
    }
  }

  function saveConversation(storage, conversation) {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(conversation));
      return true;
    } catch (_) {
      return false;
    }
  }

  function createSessionId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') return globalThis.crypto.randomUUID();
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  async function readHttpError(response) {
    const fallback = response.status === 422 ? 'Check your message and try again.' : `Server returned ${response.status}.`;
    let detail;
    try { detail = (await response.json())?.detail; } catch (_) { /* Use the status fallback. */ }
    const safe = typeof detail === 'string' && detail.trim().length > 0 && detail.length <= 240 &&
      !/[<>\x00-\x1f]|https?:\/\//i.test(detail) &&
      !/\b(?:api[_\s-]?key|bearer|authorization|password|secret|token|credential)\b|\bsk-[\w-]{8,}|\bAIza[\w-]{20,}/i.test(detail);
    const message = safe ? detail.trim() : fallback;
    const action = response.status === 409 && /(?:chat is full|start a new chat)/i.test(message) ? 'new-chat' :
      response.status === 409 && /(?:still processing|pending reply|wait for its reply)/i.test(message) ? 'wait' : 'retry';
    return { message, action };
  }

  function sessionResetNotice(previousId, newId, established) {
    return established && previousId !== newId ?
      'Your chat context has reset because the server session expired or restarted. Please send everyone’s starting point and time limit again.' : null;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function appendInline(parent, tokens) {
    for (const token of tokens) {
      if (token.type === 'bold') parent.append(el('strong', '', token.text));
      else if (token.type === 'link') {
        const href = safeHttpUrl(token.href);
        if (!href) { parent.append(document.createTextNode(token.text)); continue; }
        const link = el('a', '', token.text);
        link.href = href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        parent.append(link);
      } else parent.append(document.createTextNode(token.text));
    }
  }

  function renderMarkdown(parent, markdown) {
    for (const block of parseMarkdown(markdown)) {
      if (block.type === 'heading') {
        const heading = el(`h${Math.min(block.level + 2, 5)}`);
        appendInline(heading, block.content);
        parent.append(heading);
      } else if (block.type === 'list') {
        const list = el('ul');
        for (const item of block.items) {
          const li = el('li');
          appendInline(li, item);
          list.append(li);
        }
        parent.append(list);
      } else if (block.type === 'table') {
        const wrap = el('div', 'table-scroll');
        const table = el('table');
        const head = el('thead');
        const headRow = el('tr');
        for (const cell of block.headers) {
          const th = el('th');
          appendInline(th, cell);
          headRow.append(th);
        }
        head.append(headRow);
        const body = el('tbody');
        for (const row of block.rows) {
          const tr = el('tr');
          for (const cell of row) {
            const td = el('td');
            appendInline(td, cell);
            tr.append(td);
          }
          body.append(tr);
        }
        table.append(head, body);
        wrap.append(table);
        parent.append(wrap);
      } else {
        const p = el('p');
        appendInline(p, block.content);
        parent.append(p);
      }
    }
  }

  function collectLinks(value, output = new Set()) {
    if (typeof value === 'string') {
      const direct = safeHttpUrl(value);
      if (direct) output.add(direct);
      else for (const match of value.matchAll(/https?:\/\/[^\s<>"']+/g)) {
        const href = safeHttpUrl(match[0].replace(/[.,;!?]+$/, ''));
        if (href) output.add(href);
      }
    } else if (Array.isArray(value)) {
      value.forEach(item => collectLinks(item, output));
    } else if (value && typeof value === 'object') {
      Object.values(value).forEach(item => collectLinks(item, output));
    }
    return output;
  }

  function collectAttributions(value, output = [], seen = new Set()) {
    if (!value || typeof value !== 'object') return output;
    if (Array.isArray(value)) {
      value.forEach(item => collectAttributions(item, output, seen));
    } else {
      if (Array.isArray(value.attributions)) {
        for (const attribution of value.attributions) {
          const provider = attribution && (attribution.provider || attribution.displayName);
          const href = attribution && safeHttpUrl(attribution.providerUri);
          if (typeof provider === 'string' && !seen.has(`${provider}|${href}`)) {
            output.push({ provider, href });
            seen.add(`${provider}|${href}`);
          }
        }
      }
      Object.values(value).forEach(item => collectAttributions(item, output, seen));
    }
    return output;
  }

  function renderToolCall(call, index) {
    const name = typeof call.name === 'string' ? call.name : 'Tool call';
    const details = el('details', 'tool-card');
    const summary = el('summary', 'tool-summary');
    summary.append(el('span', 'tool-number', String(index + 1).padStart(2, '0')));
    summary.append(el('span', 'tool-title', TOOL_LABELS[name] || name));
    if (['get_group_routes', 'search_nearby_places', 'plan_group_departures'].includes(name)) {
      const attribution = el('span', 'maps-attribution-inline', 'Google Maps');
      attribution.setAttribute('translate', 'no');
      summary.append(attribution);
    }
    summary.append(el('span', 'tool-chevron', '+'));
    details.append(summary);
    const body = el('div', 'tool-body');
    body.append(el('p', 'tool-raw-name', `Tool: ${name}`));
    if (['get_group_routes', 'search_nearby_places', 'plan_group_departures'].includes(name)) {
      const attribution = el('p', 'maps-attribution', 'Google Maps');
      attribution.setAttribute('translate', 'no');
      body.append(attribution);
    }
    const attributions = collectAttributions(call.result);
    if (attributions.length) {
      const credits = el('div', 'provider-attributions');
      credits.append(el('span', '', 'Place data from: '));
      attributions.forEach((item, i) => {
        if (i) credits.append(document.createTextNode(', '));
        if (item.href) {
          const link = el('a', '', item.provider);
          link.href = item.href;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          credits.append(link);
        } else credits.append(document.createTextNode(item.provider));
      });
      body.append(credits);
    }
    const links = [...collectLinks(call.result)];
    if (links.length) {
      const linkList = el('div', 'tool-links');
      links.forEach((href, i) => {
        const link = el('a', '', /^https?:\/\/(www\.)?google\.[^/]+\/maps|^https?:\/\/maps\.google\./i.test(href) ? 'Open in Google Maps ↗' : `Open source ${i + 1} ↗`);
        link.href = href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        linkList.append(link);
      });
      body.append(linkList);
    }
    for (const [label, value] of [['Inputs', call.args], ['Result', call.result]]) {
      body.append(el('h4', '', label));
      const pre = el('pre', 'tool-json');
      pre.textContent = JSON.stringify(value === undefined ? null : value, null, 2);
      body.append(pre);
    }
    details.append(body);
    return details;
  }

  function init() {
    const transcript = document.getElementById('transcript');
    if (!transcript) return;
    const welcome = document.getElementById('welcome');
    const form = document.getElementById('chat-form');
    const input = document.getElementById('message-input');
    const sendButton = document.getElementById('send-button');
    const newChatButton = document.getElementById('new-chat');
    const notice = document.getElementById('notice');
    const followups = document.getElementById('followup-actions');
    const conversation = restoreConversation(sessionStorage);
    if (!conversation.sessionId) conversation.sessionId = createSessionId();
    let busy = false;
    saveConversation(sessionStorage, conversation);

    function showNotice(message) {
      notice.textContent = message;
      notice.hidden = !message;
    }

    function setBusy(value) {
      busy = value;
      sendButton.disabled = value;
      newChatButton.disabled = value;
      input.disabled = value;
      document.querySelectorAll('.example-button').forEach(button => { button.disabled = value; });
    }

    function render() {
      transcript.replaceChildren();
      welcome.hidden = conversation.messages.length > 0;
      transcript.hidden = conversation.messages.length === 0;
      followups.hidden = conversation.messages.length === 0;
      const guide = pendingGuide(conversation.messages);
      input.placeholder = guide ? guide.placeholder : conversation.messages.length ? 'Ask a question or share your plans…' : 'Where are your 2–4 friends starting from?';
      input.maxLength = inputLimitFor(conversation.messages);
      conversation.messages.forEach((message, index) => {
        const article = el('article', `message message-${message.role}`);
        article.setAttribute('aria-label', message.role === 'user' ? 'Your message' : 'Assistant reply');
        if (message.role === 'user') article.append(el('div', 'message-label', 'You'));
        if (message.role === 'assistant' && Array.isArray(message.toolCalls) && message.toolCalls.length) {
          const tools = el('div', 'tool-calls');
          tools.append(el('div', 'tool-group-label', 'How this answer was checked'));
          message.toolCalls.forEach((call, toolIndex) => tools.append(renderToolCall(call, toolIndex)));
          article.append(tools);
        }
        const content = el('div', 'message-content');
        renderMarkdown(content, message.text);
        article.append(content);
        if (message.status === 'pending') article.append(el('div', 'message-state', 'Thinking…'));
        if (message.status === 'failed') {
          const retry = el('button', 'retry-button', message.recovery === 'new-chat' ? 'Start New chat' : 'Retry this message');
          retry.type = 'button';
          retry.addEventListener('click', () => message.recovery === 'new-chat' ? newChatButton.click() : sendPending(index));
          article.append(retry);
        }
        transcript.append(article);
      });
      if (conversation.messages.length) transcript.lastElementChild.scrollIntoView({ block: 'end', behavior: 'smooth' });
    }

    async function sendPending(index) {
      if (busy) return;
      const message = conversation.messages[index];
      if (!message || message.role !== 'user' || !['pending', 'failed'].includes(message.status)) return;
      message.status = 'pending';
      delete message.recovery;
      saveConversation(sessionStorage, conversation);
      showNotice('');
      setBusy(true);
      render();
      try {
        const response = await fetch('/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: requestMessage(message.text, conversation.messages.slice(0, index)), session_id: conversation.sessionId })
        });
        if (!response.ok) {
          const problem = await readHttpError(response);
          throw Object.assign(new Error(problem.message), { action: problem.action });
        }
        const data = await response.json();
        if (!data || typeof data.response !== 'string' || !Array.isArray(data.tool_calls) || typeof data.session_id !== 'string') {
          throw new Error('The server returned an incomplete reply');
        }
        const reset = sessionResetNotice(conversation.sessionId, data.session_id,
          hasServerReply(conversation.messages));
        conversation.sessionId = data.session_id;
        delete message.status;
        if (reset) conversation.messages.push({ role: 'assistant', text: reset });
        conversation.messages.push({ role: 'assistant', text: data.response, toolCalls: data.tool_calls });
        saveConversation(sessionStorage, conversation);
        render();
        input.focus();
      } catch (error) {
        message.status = 'failed';
        message.recovery = error.action === 'new-chat' ? 'new-chat' : 'retry';
        saveConversation(sessionStorage, conversation);
        render();
        const guidance = error.action === 'new-chat' ? 'Use “New chat” to continue.' :
          error.action === 'wait' ? 'Wait for the pending reply, then use “Retry this message”.' :
          'Use “Retry this message” to try again.';
        showNotice(`Could not send your message. ${error.message} ${guidance}`);
        const retry = transcript.querySelector('.retry-button');
        if (retry) retry.focus();
      } finally {
        setBusy(false);
      }
    }

    function queueMessage(text) {
      const trimmed = text.trim();
      if (busy || !trimmed) return;
      if (conversation.messages.some(message => message.status === 'failed' || message.status === 'pending')) {
        showNotice('Resolve the unsent message or start a new chat first.');
        return;
      }
      try {
        requestMessage(trimmed, conversation.messages);
      } catch (error) {
        showNotice(error.message);
        return;
      }
      const index = conversation.messages.push({ role: 'user', text: trimmed, status: 'pending' }) - 1;
      input.value = '';
      saveConversation(sessionStorage, conversation);
      sendPending(index);
    }

    form.addEventListener('submit', event => {
      event.preventDefault();
      queueMessage(input.value);
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        form.requestSubmit();
      }
    });
    document.querySelectorAll('.example-button').forEach(button => {
      button.addEventListener('click', () => {
        if (busy) return;
        if (conversation.messages.some(message => message.status === 'failed' || message.status === 'pending')) {
          showNotice('Retry your unsent message or start a new chat first.');
          return;
        }
        if (!openGuide(conversation.messages, button.dataset.guide)) return;
        showNotice('');
        saveConversation(sessionStorage, conversation);
        render();
        input.focus();
      });
    });
    newChatButton.addEventListener('click', async () => {
      if (busy) return;
      const oldId = conversation.sessionId;
      setBusy(true);
      showNotice('');
      conversation.sessionId = createSessionId();
      conversation.messages = [];
      input.value = '';
      saveConversation(sessionStorage, conversation);
      render();
      try {
        const response = await fetch(`/clear?session_id=${encodeURIComponent(oldId)}`, { method: 'POST' });
        if (!response.ok) throw new Error(`Server returned ${response.status}`);
      } catch (_) {
        showNotice('New chat started. The previous server session could not be cleared.');
      } finally {
        setBusy(false);
        input.focus();
      }
    });
    const interrupted = conversation.messages.find(message => message.status === 'pending');
    if (interrupted) {
      interrupted.status = 'failed';
      saveConversation(sessionStorage, conversation);
      showNotice('A message was interrupted. Use “Retry this message” to send it again.');
    }
    render();
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { parseMarkdown, safeHttpUrl, restoreConversation, saveConversation,
      readHttpError, sessionResetNotice, openGuide, requestMessage, hasServerReply, inputLimitFor };
  }
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
  }
})();
