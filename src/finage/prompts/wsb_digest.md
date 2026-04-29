Create a Telegram-friendly market momentum digest from this JSON evidence.

Goal:
Identify short-term retail momentum signals based on Reddit activity, then use web search results as supporting context for recent real-world catalysts.

Evidence types:
- Reddit evidence shows retail attention, sentiment, narratives, and repeated discussion patterns.
- Web search evidence may show recent news, earnings, analyst notes, product updates, regulatory events, or other catalysts.
- Do not treat web search results as proof unless the title/highlights clearly support the claim.
- If Reddit and web evidence disagree, call that out briefly.

For each top ticker:
- Ticker + momentum score (High / Medium / Low)
- Why it is trending on Reddit
- Sentiment (Bullish / Bearish / Mixed) with confidence level
- Key Reddit narratives, emphasizing repeated themes over one-off opinions
- Recent web context, if available, including relevant URLs
- Catalysts: separate confirmed/news-based catalysts from Reddit rumors
- Evidence: quote or paraphrase representative Reddit posts/comments and cite web result URLs when used

Rules:
- Prioritize signal over hype; ignore low-effort memes unless dominant
- Weigh repeated Reddit ideas more than isolated comments
- Use web search to ground catalysts, not to replace Reddit momentum analysis
- Do NOT fabricate missing data
- Do NOT claim a web search result says something unless it is present in the provided title, highlights, or text
- Compare against the previous digest when available and highlight any key patterns that emerge. Do this only if it is relevant.
- This is market intelligence, not financial advice.

Output:
- One-line title
- Concise bullets
- Max 5-7 tickers
- Use simple Markdown formatting, especially `**bold**` for ticker names and section headers
- Include URLs only when they support a concrete catalyst or clarification

End with:
A brief "watchlist read" that separates short-term momentum setups from longer-term items to monitor, based only on the provided evidence.

Previous Digest JSON:
{previous_digest_json}

Evidence JSON:
{evidence_json}
