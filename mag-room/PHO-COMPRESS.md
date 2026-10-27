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

Once a later user or assistant prose line ages out of the recent window, it is sealed in the archive. A warm request does not substitute that seal back into a message the previous request already sent. The entire previous message list stays byte-identical, so prompt-cache reuse keeps working. The room UI keeps the original text. Speaker labels, the `[Kontext]` tail, code, tool calls and tool results stay verbatim, as does the latest user message and the last N completed turns.

Short lines (fewer than eight words) and encodes that keep fewer than half the original words are rejected. Greetings therefore stay verbatim instead of collapsing to a stub such as `aus model .`. Longer prose may still be stored as lexical stems. Those stems are substituted only into history that has not been sent yet (an imported transcript before its first evaluation) and into the copy rebuilt at context-budget rollover. That rollover already discards the warm prefix.

Each accepted seal logs `pho-compress scope=history model=… tokens_before=… tokens_after=… saved=…`. `saved` is that line's measured token saving. Diagnostic lines contain only model and counts, never the text. New room input remains append-only, with fixed `id_slot=0` and `cache_prompt=true`. A tokenizer request does not alter an inference slot.

## Verification

The native codec test covers deterministic mappings, U extraction/restoration (including placeholder collisions), numbers/URLs/emails/code/IDs and semantic guard words. Integration tests exercise both real room routes against an isolated fake tokenizer/model: default/per-bot/room persistence, sealed archive decisions, a byte-identical warm message list (system prompt, tool schema and every previously sent turn), short utterances left verbatim, aged prose sealed for rollover while the room UI stays original, positive `n_past` on the unchanged prefix, code and tool payloads left verbatim, archive consumption at a real context-budget rollover, recent-turn protection and cache reuse after rollover. Browser CI checks the menu switch and persistence, alongside the existing strict zero-CLS quiet stream regression. CI fake counts prove routing/behavior; live savings and latency depend on the installed model tokenizers and MAG hardware.
