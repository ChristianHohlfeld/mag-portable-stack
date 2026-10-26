# Native PhO-Compress

The room adapts Christian Heinrich Hohlfeld's [PhoLine](https://github.com/ChristianHohlfeld/PhoLine) lexical Stage I proxy (`src/codec.ts`, `src/lexicon.ts`) directly in C++. Attribution: ORCID 0009-0003-6634-9045. Adaptation is authorized by the author; upstream's all-rights-reserved license is not replaced by a new license. No extension, network hook, local output expansion, vision stage or additional inference model is involved.

PhoLine removes common filler and auxiliary words, maps known German/English vocabulary to short English channel stems, protects structural spans through a U side channel, and restores those spans exactly. Its extension instructs the hosted model to emit marked compact replies and expands them locally. MAG deliberately uses input compression only: a fixed system instruction explains historical `[PhO-Compress]` text and requires normal readable answers.

The PhO-Compress v2 paper (25 October 2025), Stage I, describes a theoretically lossless G2P/U representation with a complete side channel and reversible predictive coding. The deployed PhoLine lexical codec is a lossy proxy, not that lossless codec. The original paper is archived in [the author's website repository](https://github.com/ChristianHohlfeld/christianhohlfeld.com/blob/75e48090b88ecabe8a013d932e20fae8c0d7b5d0/Christian_Heinrich_Hohlfeld_Konstanz_PhO-Compress_v2.pdf); the former public PDF URL now returns 404. The repository, protocol, TypeScript codec/lexicon, extension hooks/HUD, test suite, G2P research sketch and all ten paper pages were inspected before this adaptation.

## Scope and protection

`PhO-Compress` is next to Thinking/GitHub in the + menu, ON by default, persisted in per-bot settings and room defaults. The preference is sampled at a turn boundary. Turning it off never rewrites an already established prompt. The default recent window is two complete turns; `MAG_ROOM_PHO_KEEP_TURNS` can increase it (1–20).

System/bootstrap text, the initial context wrapper, the recent turns, assistant tool calls, tool results, code and GitHub content remain exact. Unknown words and proper names retain spelling/case; negation, modality, conditions and alternatives are preserved rather than inheriting PhoLine's aggressive stop list or `should → must` mapping. U spans protect URLs, emails, signed/scientific numbers, versions, dates, UUIDs, hashes, structured IDs, paths, inline code and fenced code. Code-bearing/GitHub turns are excluded as a whole.

Channel stems in upstream PhoLine were selected for o200k/cl100k; those vocabularies are not assumed for DeepSeek or Qwen. Before accepting an eligible historical turn, MAG calls that bot's actual `/tokenize` endpoint for both original and candidate (including the marker), using model/slot routing. It stores the candidate only when the measured token count is smaller. Failure or no saving retains the original and seals that decision. Diagnostic lines contain only model/counts/acceptance, never the text or U payload.

## Immutable history and the KV boundary

A completed turn ages out of the recent window at a later turn boundary. It is encoded once, with its preference and tokenizer counts, into the persisted per-bot `prompt-wire-*.json` archive. Stored decisions survive room restarts and cancellation; later boundaries never re-encode a sealed entry.

Archiving never substitutes tokens in the current wire messages. New room input remains append-only, with fixed `id_slot=0`, `cache_prompt=true` and the existing asynchronous prewarm/checkpoints for both models. A tokenizer request does not alter an inference slot. Imported older room history can be compressed before its first evaluation. For an established session, stored compressed turns replace old raw prose only at the existing infrequent context-budget rollover, before the existing bounded compactor removes old rounds. Last-N turns and structured payloads remain exact. A context rollover legitimately establishes a new KV prefix; subsequent requests reuse that prefix.

It is impossible to remove tokens already in KV and simultaneously keep those KV positions byte-identical. This implementation therefore does not promise immediate token savings in an active short conversation. It avoids periodic cache invalidation just to compress history, which would reintroduce the multi-minute CPU prefills this change is intended to prevent.

## Verification

The native codec test covers deterministic mappings, U extraction/restoration (including placeholder collisions), numbers/URLs/emails/code/IDs and semantic guard words. Integration tests exercise both real room routes against an isolated fake tokenizer/model: default/per-bot/room persistence, sealed archive decisions, byte-identical warm wire prefixes, positive second-turn `n_past`, archive consumption at a real context-budget rollover, recent-turn protection and cache reuse after rollover. Browser CI checks the menu switch and persistence, alongside the existing strict zero-CLS quiet stream regression. CI fake counts prove routing/behavior; live savings and latency depend on the installed model tokenizers and MAG hardware.
