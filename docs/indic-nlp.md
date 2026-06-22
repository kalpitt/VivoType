## Indic Post-Processing Module
This module lives in `core/` and is independent of the ML model.

1. **Filler Word Cleanup:** Removes safe filler words. The list of filler words must be configurable via a JSON/YAML file. Default to conservative words like "um", "uh".
2. **Dictionary Replacement:** Use the same JSON/YAML file to map ASR misrecognitions to correct Indian terms.
   - *Examples to include by default:* "blr" -> "Bengaluru", "100k" -> "1 lakh", "shrivastava" -> "Srivastava" (spelling correction), and local tech jargon.
   - *Currency conversion (USD → ₹):* This should rely on strict, adjacent-word regex patterns (e.g., matching '$[number]k CTC' -> '₹[number] lakh CTC') rather than complex sentence-parsing, to keep the CLI lightweight. The regex must handle: integer amounts (`$10k`), decimal amounts (`$1.5k`), and million notation (`$1M` → `₹[n] crore`). It must NOT match inside URLs, code blocks, or quoted strings.
