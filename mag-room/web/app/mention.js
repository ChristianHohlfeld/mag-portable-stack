// @mentions. "@Lena" asks Lena alone (the room decides that; see the engine).
// Here: the suggestions while typing, the chip behind a mention in the
// message field, and the chips in the timeline. A chip is tied to the bot,
// not to the word: after a rename an old "@Lena" shows the bot's name of today.
'use strict';
(function () {
  const { MAG } = window, { el, $ } = MAG;
  const EVERYONE = ['alle', 'beide', 'all', 'everyone'];
  let input, mirror, list;
  let open = null; // { start, items, index } while suggestions are shown

  // Every name a bot answers to (its name, earlier names, its id), longest first.
  function names() {
    const out = [];
    for (const bot of MAG.state.bots)
      for (const name of new Set([bot.name, ...(bot.aliases || []), bot.id])) if (name) out.push({ name, bot });
    for (const name of EVERYONE) out.push({ name, bot: null });
    return out.sort((a, b) => b.name.length - a.name.length);
  }
  // [{ start, end, bot (null = everybody), label }] for the mentions in a text.
  function find(text) {
    const known = names();
    if (!text.includes('@') || !known.length) return [];
    const quote = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const pattern = new RegExp('(^|[^\\p{L}\\p{N}_@])@(' + known.map((n) => quote(n.name)).join('|') + ')(?![\\p{L}\\p{N}_]|-[\\p{L}\\p{N}])', 'giu');
    const found = [];
    for (let m; (m = pattern.exec(text));) {
      const hit = known.find((n) => n.name.toLowerCase() === m[2].toLowerCase());
      const start = m.index + m[1].length;
      found.push({ start, end: start + 1 + m[2].length, bot: hit.bot, label: '@' + (hit.bot ? hit.bot.name : m[2]) });
      pattern.lastIndex = found[found.length - 1].end;
    }
    return found;
  }
  const chip = (hit) => el('span', { class: 'at' + (hit.bot ? '' : ' all'), dataset: hit.bot ? { bot: hit.bot.id } : {}, text: hit.label });

  MAG.mentions = {
    find,
    // The field changed without typing (sent, dictated, restored): repaint its chips.
    paint: () => { if (input) paint(); },
    // Turns the mentions in rendered text into chips (not inside code or links).
    decorate(box) {
      if (!box.textContent.includes('@')) return;
      const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
      const nodes = [];
      for (let node; (node = walker.nextNode());) if (node.nodeValue.includes('@') && !node.parentElement.closest('code, pre, a, .at')) nodes.push(node);
      for (const node of nodes) {
        const text = node.nodeValue, hits = find(text);
        if (!hits.length) continue;
        const parts = [];
        let at = 0;
        for (const hit of hits) {
          if (hit.start > at) parts.push(text.slice(at, hit.start));
          parts.push(chip(hit));
          at = hit.end;
        }
        if (at < text.length) parts.push(text.slice(at));
        node.replaceWith(...parts);
      }
    },
  };

  // ---- the message field ----------------------------------------------------
  // A layer behind the (transparent) field repeats its text and paints the
  // background of every mention; the letters themselves stay the field's.
  function paint() {
    const text = input.value, hits = find(text), parts = [];
    let at = 0;
    for (const hit of hits) {
      if (hit.start > at) parts.push(text.slice(at, hit.start));
      parts.push(el('span', { class: 'at' + (hit.bot ? '' : ' all'), dataset: hit.bot ? { bot: hit.bot.id } : {}, text: text.slice(hit.start, hit.end) }));
      at = hit.end;
    }
    parts.push(text.slice(at) + '​'); // a last empty line has a height, too
    mirror.replaceChildren(...parts);
    mirror.scrollTop = input.scrollTop;
  }

  function close() {
    open = null;
    list.hidden = true;
    input.removeAttribute('aria-activedescendant');
  }
  function choose(item) {
    const end = input.selectionStart;
    input.setRangeText('@' + item.name + ' ', open.start, end, 'end');
    close();
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
  }
  function show() {
    list.replaceChildren(...open.items.map((item, i) => el('button', { type: 'button', id: 'mention-' + i, role: 'option', class: i === open.index ? 'on' : '', 'aria-selected': String(i === open.index),
      dataset: item.bot ? { bot: item.bot.id } : {}, onclick: () => choose(item) },
      el('span', { class: 'dot' }), el('b', { text: '@' + item.name }), el('span', { text: item.hint }))));
    list.hidden = false;
    input.setAttribute('aria-activedescendant', 'mention-' + open.index);
  }
  // Suggestions appear while the word at the caret starts with "@".
  function suggest() {
    const caret = input.selectionStart;
    if (caret !== input.selectionEnd) return close();
    const m = /(^|[\s(])@([\p{L}\p{N}_-]*)$/u.exec(input.value.slice(0, caret));
    if (!m) return close();
    const typed = m[2].toLowerCase(), items = [];
    for (const bot of MAG.state.bots)
      if (bot.name.toLowerCase().startsWith(typed)) items.push({ name: bot.name, bot, hint: bot.model?.state === 'stopped' ? 'abgeschaltet' : bot.enabled === false ? 'pausiert' : 'antwortet allein' });
    if (MAG.state.bots.length > 1 && 'alle'.startsWith(typed)) items.push({ name: 'alle', bot: null, hint: 'alle antworten' });
    // The whole name is there already: nothing left to suggest.
    if (!items.length || items.some((item) => item.name.toLowerCase() === typed)) return close();
    open = { start: caret - m[2].length - 1, items, index: Math.min(open?.index || 0, items.length - 1) };
    show();
  }

  document.addEventListener('DOMContentLoaded', () => {
    input = $('#input');
    mirror = $('#mirror');
    list = el('div', { class: 'mention-list', role: 'listbox', 'aria-label': 'Bot ansprechen', hidden: true,
      onpointerdown: (e) => e.preventDefault() }); // a tap on a suggestion must not take the keyboard away
    input.closest('.field').append(list);
    input.addEventListener('input', () => { paint(); suggest(); });
    input.addEventListener('click', suggest);
    input.addEventListener('keyup', (e) => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) suggest(); });
    input.addEventListener('scroll', () => { mirror.scrollTop = input.scrollTop; });
    input.addEventListener('blur', () => setTimeout(close, 150));
    // Before the composer's own keys: Enter picks a suggestion instead of sending.
    input.addEventListener('keydown', (e) => {
      if (!open) return;
      const step = e.key === 'ArrowDown' ? 1 : e.key === 'ArrowUp' ? -1 : 0;
      if (step) open.index = (open.index + step + open.items.length) % open.items.length;
      else if (e.key === 'Enter' || e.key === 'Tab') choose(open.items[open.index]);
      else if (e.key === 'Escape') close();
      else return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (step) show();
    }, true);
    paint();
  });
  // A rename: the suggestions, the field and every chip already on the page follow.
  MAG.on('bots', () => {
    if (!input) return;
    paint();
    for (const node of document.querySelectorAll('.msg .at[data-bot]')) {
      const bot = MAG.bot(node.dataset.bot);
      if (bot && node.textContent !== '@' + bot.name) node.textContent = '@' + bot.name;
    }
  });
})();
