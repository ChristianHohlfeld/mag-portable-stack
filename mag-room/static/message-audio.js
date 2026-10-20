// Manual, device-local read-aloud for every MAG chat message (bot and human).
// Deliberately no autoplay, server request, audio file, or additional permission.
(() => {
  'use strict';
  const synth = window.speechSynthesis;
  const supported = !!synth && typeof window.SpeechSynthesisUtterance === 'function';
  const playIcon = '<svg class="i sm" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H3v6h3l5 4V5Z"/><path d="M15 9a5 5 0 0 1 0 6m3-9a9 9 0 0 1 0 12"/></svg>';
  const stopIcon = '<svg class="i sm" aria-hidden="true"><use href="#ic-stop"></use></svg>';
  let current = null;
  let nextEphemeral = 0;

  function normalize(text) {
    return String(text || '')
      .replace(/\[\[MAG:(?:WEITER|ENDE)\]\]/g, '')
      .replace(/\r\n?/g, '\n')
      .trim();
  }

  // Safari may stop speaking mid-way through long utterances. Keep each one short,
  // using a word/sentence boundary, but avoid inserting extra speech pauses.
  function split(text, max = 170) {
    const out = [];
    let rest = text;
    while (rest.length > max) {
      let cut = Math.max(rest.lastIndexOf('. ', max), rest.lastIndexOf('! ', max),
                         rest.lastIndexOf('? ', max), rest.lastIndexOf('\n', max));
      if (cut < max / 3) cut = rest.lastIndexOf(' ', max);
      if (cut < max / 3) cut = max;
      else cut += 1;
      out.push(rest.slice(0, cut).trim());
      rest = rest.slice(cut).trimStart();
    }
    if (rest.trim()) out.push(rest.trim());
    return out.filter(Boolean);
  }

  function chosenLocale() {
    const setting = document.getElementById('language')?.value || 'auto';
    if (setting === 'de') return 'de-DE';
    if (setting === 'en') return 'en-US';
    const locale = navigator.language || 'de-DE';
    return locale.startsWith('de') ? 'de-DE' : locale.startsWith('en') ? 'en-US' : locale;
  }

  function setButton(button) {
    const playing = !!current && current.key === button.dataset.audioKey;
    button.setAttribute('aria-pressed', String(playing));
    button.setAttribute('aria-label', playing ? 'Vorlesen stoppen' : 'Nachricht vorlesen');
    button.title = playing ? 'Vorlesen stoppen' : 'Nachricht vorlesen';
    button.innerHTML = playing ? stopIcon : playIcon;
  }
  function setButtons() {
    document.querySelectorAll('.message-speak').forEach(setButton);
  }

  function stop() {
    current = null; // make pending onend/onerror callbacks harmless
    if (supported) synth.cancel();
    setButtons();
  }

  function nextPart(playback) {
    if (current !== playback) return;
    const text = playback.parts[playback.at++];
    if (!text) { current = null; setButtons(); return; }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = playback.lang;
    utterance.rate = 1;
    // Prefer the system's local voice, but leave platform voice choice intact if absent.
    const voices = synth.getVoices?.() || [];
    const match = voices.find(v => v.lang?.toLowerCase() === playback.lang.toLowerCase() && v.localService)
      || voices.find(v => v.lang?.toLowerCase() === playback.lang.toLowerCase())
      || voices.find(v => v.lang?.toLowerCase().split('-')[0] === playback.lang.toLowerCase().split('-')[0]);
    if (match) utterance.voice = match;
    utterance.onend = () => nextPart(playback);
    utterance.onerror = () => {
      if (current === playback) { current = null; setButtons(); }
    };
    try { synth.speak(utterance); }
    catch (_) { if (current === playback) { current = null; setButtons(); } }
  }

  function toggle(key, text) {
    if (!supported) return false;
    const wasPlaying = !!current && current.key === key;
    stop();
    if (wasPlaying) return false;
    const parts = split(normalize(text));
    if (!parts.length) return false;
    current = {key, parts, at: 0, lang: chosenLocale()};
    setButtons();
    // First speech call is synchronous in the user's tap (required by iOS Safari).
    nextPart(current);
    return true;
  }

  function attach(button, text, id) {
    const key = id || 'temporary-message-' + (++nextEphemeral);
    button.classList.add('message-speak');
    button.dataset.audioKey = key;
    button.type = 'button';
    button.disabled = !supported;
    button.onclick = () => toggle(key, text);
    setButton(button);
    if (!supported) button.title = 'Vorlesen auf diesem Gerät nicht verfügbar';
  }

  window.magMessageAudio = {attach, toggle, stop, supported, isPlaying: id => !!current && current.key === id};
  window.addEventListener('pagehide', stop);
})();
