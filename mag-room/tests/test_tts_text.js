#!/usr/bin/env node
// What the worker's text rules must keep: German is written as it is
// spoken before it goes to the model, the model gets its letters the way
// its table knows them, and a long part is cut where a speaker would pause.
'use strict';
const assert = require('assert');
const { spoken, pieces } = require('../web/app/tts-worker.js');
const de = (text) => spoken(text, 'de').normalize('NFC');
assert.strictEqual(de('Ich habe 42 Äpfel und 3,5 Liter Milch'), 'Ich habe zweiundvierzig Äpfel und drei Komma fünf Liter Milch.');
assert.strictEqual(de('Am 3. März 2025 um 14:30 Uhr'), 'Am dritten März zweitausendfünfundzwanzig um vierzehn Uhr dreißig.');
assert.strictEqual(de('3. März, vom 12.11.2026'), 'dritter März, vom zwölften elften zweitausendsechsundzwanzig.');
assert.strictEqual(de('1989 waren es 1.250 € (ca. 15 %)'), 'neunzehnhundertneunundachtzig waren es eintausendzweihundertfünfzig Euro (circa fünfzehn Prozent)');
assert.strictEqual(de('Kerne 28-55, z. B. bei 21 °C und 100 km/h'), 'Kerne achtundzwanzig bis fünfundfünfzig, zum Beispiel bei einundzwanzig Grad Celsius und einhundert Kilometer pro Stunde.');
assert.strictEqual(de('1 Hund, 101 Dalmatiner, 1000000 Sterne, 0 Fehler'), 'ein Hund, einhunderteins Dalmatiner, eine Million Sterne, null Fehler.');
assert.strictEqual(de('Lena & Noah – „schön“ 😀'), 'Lena und Noah - "schön"');
// What is spelled out is spelled out: "3D" is not "dritte D".
assert.strictEqual(de('Foto zu 3D ist fertig, das 3D-Modell auch'), 'Foto zu drei De ist fertig, das drei De Modell auch.');
assert.strictEqual(de('Die KI in der VM nutzt die GPU, als MP3 in 4K'), 'Die Ka I in der Vau Emm nutzt die Ge Pe U, als Emm Pe drei in vier Ka.');
assert.strictEqual(de('WICHTIG: MAG bleibt MAG'), 'WICHTIG: MAG bleibt MAG.');
// The model's table has no "ä": it gets "a" and the two dots.
assert.ok(spoken('Über Öl', 'de').includes('Über Öl'));
assert.ok(!/[äöüÄÖÜ]/.test(spoken('Über Öl und Übung', 'de')));
// English stays English.
assert.strictEqual(spoken('I have 42 apples', 'en'), 'I have 42 apples.');
// Nothing to say is nothing.
assert.strictEqual(spoken('😀', 'de'), '');
// A long part is cut at a sentence, else at a clause, never in a word.
const cut = pieces('a'.repeat(100) + '. ' + 'b'.repeat(250) + ', ' + 'c'.repeat(100), 280);
assert.deepStrictEqual(cut.map((p) => p.length), [101, 251, 100]);
assert.ok(pieces('wort '.repeat(200).trim(), 280).every((p) => p.length <= 280 && !p.startsWith(' ') && /^(wort ?)+$/.test(p)));
console.log('tts text ok');
