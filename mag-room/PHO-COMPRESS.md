# Native PhO-Compress

The room adapts Christian Heinrich Hohlfeld's [PhoLine](https://github.com/ChristianHohlfeld/PhoLine) lexical Stage I proxy (`src/codec.ts`, `src/lexicon.ts`) directly in C++. Attribution: ORCID 0009-0003-6634-9045. Adaptation is authorized by the author; upstream's all-rights-reserved license is not replaced by a new license. No extension, network hook, local output expansion, vision stage or additional inference model is involved.

PhoLine removes common filler and auxiliary words, maps known German/English vocabulary to short English channel stems, protects structural spans through a U side channel, and restores those spans exactly. Its extension instructs the hosted model to emit marked compact replies and expands them locally. MAG deliberately uses input compression only: a fixed system instruction explains historical compact stems and requires normal readable answers. Archived turns do not repeat a `[PhO-Compress]` label; on DeepSeek V4 Flash and Qwen3.8 that label is 7 tokens and exceeded the saving on every live short turn.

The PhO-Compress v2 paper (25 October 2025), Stage I, describes a theoretically lossless G2P/U representation with a complete side channel and reversible predictive coding. The deployed PhoLine lexical codec is a lossy proxy, not that lossless codec. The original paper is archived in [the author's website repository](https://github.com/ChristianHohlfeld/christianhohlfeld.com/blob/75e48090b88ecabe8a013d932e20fae8c0d7b5d0/Christian_Heinrich_Hohlfeld_Konstanz_PhO-Compress_v2.pdf); the former public PDF URL now returns 404. The repository, protocol, TypeScript codec/lexicon, extension hooks/HUD, test suite, G2P research sketch and all ten paper pages were inspected before this adaptation.

## Scope and protection

`PhO-Compress` is next to Thinking/GitHub in the + menu, ON by default, persisted in per-bot settings and room defaults. The preference is sampled at a turn boundary. Turning it off never rewrites an already established prompt. The default recent window is two complete turns; `MAG_ROOM_PHO_KEEP_TURNS` can increase it (1–20).

When accepted archives actually saved tokens, the existing menu state shows that saving as a percentage (`−23 %`) and the same text (`PhO −23 %`) is appended inside the queue status line only while that line is already visible. Nothing is added when PhO is off or no tokens were saved. The tooltip lists, per selected bot, the last measured turn and the token-weighted session total as tokens before and after. The chat layout does not grow.

System/bootstrap text, the initial context wrapper, the recent turns, assistant tool calls, tool results, code and GitHub content remain exact. Unknown words and proper names retain spelling/case; negation, modality, conditions and alternatives are preserved rather than inheriting PhoLine's aggressive stop list or `should → must` mapping. U spans protect URLs, emails, signed/scientific numbers, versions, dates, UUIDs, hashes, structured IDs, paths, inline code and fenced code. Code-bearing/GitHub turns are excluded as a whole.

Channel stems in upstream PhoLine were selected for o200k/cl100k; those vocabularies are not assumed for DeepSeek or Qwen. Before accepting an eligible historical turn, MAG calls that bot's actual `/tokenize` endpoint for both original and candidate, using model/slot routing. It stores the candidate only when the measured token count is smaller. Failure or no saving retains the original and seals that decision. Diagnostic lines contain only model/counts/acceptance, never the text or U payload.

## Immutable history and the KV boundary

A completed turn ages out of the recent window at a later turn boundary. It is encoded once, with its preference and tokenizer counts, into the persisted per-bot `prompt-wire-*.json` archive. Stored decisions survive room restarts and cancellation; later boundaries never re-encode a sealed entry.

The system prompt and the tool schema are the stable prefix. They are never rewritten. The first transcript message is also left byte-identical after it has been sent, so a cold first turn (`hi`) still matches the prewarmed prefix. Imported older room history can be compressed inside that first transcript before its first evaluation.

Once a later user or assistant prose line ages out of the recent window, it is sealed in the archive. A warm request does not substitute that seal back into a message the previous request already sent. The entire previous message list stays byte-identical, so prompt-cache reuse keeps working. The room UI keeps the original text. Speaker labels, the `[Kontext]` tail, code, tool calls and tool results stay verbatim, as do the latest user message and the last N completed turns. `MAG_ROOM_PHO_LIVE=1` may compact a new user line, or another bot's new prose, once, at the moment it first enters this bot's wire. After that the stored bytes stay identical. See below.

Short lines (fewer than eight words) and encodes that keep fewer than half the original words are rejected. Greetings therefore stay verbatim instead of collapsing to a stub such as `aus model .`. Longer prose may still be stored as lexical stems. Those stems are substituted only into history that has not been sent yet (an imported transcript before its first evaluation) and into the copy rebuilt at context-budget rollover. That rollover already discards the warm prefix.

Each accepted seal logs `pho-compress scope=history model=… tokens_before=… tokens_after=… saved=…`. `saved` is that line's measured token saving. Diagnostic lines contain only model and counts, never the text. New room input remains append-only, with fixed `id_slot=0` and `cache_prompt=true`. A tokenizer request does not alter an inference slot.

## Live incoming messages (optional)

`MAG_ROOM_PHO_LIVE=1` additionally applies PhoLine Stage I to newly appended room prose on its way to each bot. The flag defaults to off (`0` or unset). The per-bot PhO-Compress switch must also be on; turning that switch off stops further live compression without rewriting a warm wire. The original room/UI message is never changed. The already-deployed stable system prompt explains the compact stems; there is no `¶` reply channel or browser hook.

A conservative gate requires plain unstructured single-line prose of 80–4000 bytes and at least 16 words, with the encoded line keeping at least 60% of those words. ASCII quotes, brackets, newlines, code, tool payloads and structured messages stay verbatim. The active model's `/tokenize` endpoint must then report at least 24 tokens on the original, at least 4 tokens saved, and at least 15% saved. A tokenizer error, a short count, or a weaker saving keeps the original. Counts are not inferred from bytes or from o200k.

The decision is made only when the line first enters that bot's persisted prompt wire: the pending user turn on a cold transcript, and each later unseen user line or other bot's chat line as it is appended. This bot's own assistant wire messages are not reformatted. Already-sent messages are never recoded, so the KV prefix stays valid. Prewarm and the following turn share that stored wire.

The existing cold-import and context-rollover paths continue compressing older history without rewriting a warm prefix. Enabling the flag does not immediately rewrite already-sent warm history. Historical content is compacted when first imported or at the next natural context rollover.

An accepted line logs `pho-compress scope=live model=… tokens_before=… tokens_after=… saved=…`. The log contains only model and counts, never the text.

Roll back with `MAG_ROOM_PHO_LIVE=0` and a service restart. That stops future live compression. It does not rewrite previously transmitted messages. Those stay until a deliberate new prompt-wire session.

Inspired by both upstream implementations, `PhoLine` and `PhO-Token-Wire`. Token-wire's optional compressed response channel is deliberately excluded: MAG needs readable answers and reliable tool operation. Leave the flag off until a measurement against the resident model's `/tokenize` shows a real saving; the lexical stems were chosen for o200k, and each eligible message adds one or two synchronous tokenize calls before the turn.

## Verification

The native codec test covers deterministic mappings, U extraction/restoration (including placeholder collisions), numbers/URLs/emails/code/IDs and semantic guard words, plus the live gate: token savings rather than bytes, fail-closed tokenizer errors, the 15% threshold, and rejection of short, quoted, bracketed, multiline and code input. Integration tests exercise both real room routes against an isolated fake tokenizer/model: default/per-bot/room persistence, sealed archive decisions, a byte-identical warm message list (system prompt, tool schema and every previously sent turn), short utterances left verbatim, aged prose sealed for rollover while the room UI stays original, positive `n_past` on the unchanged prefix, code and tool payloads left verbatim, archive consumption at a real context-budget rollover, recent-turn protection and cache reuse after rollover. With the live flag on, a new prose line is compacted once, the room text stays original, and the next turn resends the previous messages unchanged. Browser CI checks the menu switch and persistence, alongside the existing strict zero-CLS quiet stream regression. CI fake counts prove routing/behavior; live savings and latency depend on the installed model tokenizers and MAG hardware.
