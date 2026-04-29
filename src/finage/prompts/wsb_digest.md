Create a Telegram-friendly stock subreddit momentum digest from this JSON evidence from scraped Reddit posts.

Goal:
Identify short-term retail momentum signals based on Reddit activity.

For each top ticker:
- Ticker + momentum score (High / Medium / Low)
- Why it is trending (volume, unusual activity, repeat mentions)
- Sentiment (Bullish / Bearish / Mixed) with confidence level
- Key narratives (summarize recurring themes, not one-off opinions)
- Evidence (quote or paraphrase representative posts/comments)
- Any catalysts (earnings, news, rumors if present)

Rules:
- Prioritize signal over hype; ignore low-effort memes unless dominant
- Weigh repeated ideas more than isolated comments
- Do NOT fabricate missing data
- Compare against the previous digest when available and highlight any key patterns that emerge. Do this only if it is relevant.

Output:
- One-line title
- Concise bullets
- Max 5-7 tickers
- Use simple Markdown formatting, especially `**bold**` for ticker names and section headers

End with:
A recommendation based on the evidence on what could be good plays for long and short term investments.

Previous Digest JSON:
{previous_digest_json}

Evidence JSON:
{evidence_json}
