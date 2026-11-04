// Runs inside the app; the credential stays exclusively in its existing process.
// Only public tickers leave the service, and only counts/timestamps are printed.
let session;
let sequence = 0;
const base = `http://127.0.0.1:${process.env.PORT || 3000}`;
async function rpc(method, params) {
  const response = await fetch(`${base}/mcp`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(session ? { 'Mcp-Session-Id': session } : {}),
    },
    body: JSON.stringify({ jsonrpc: '2.0', id: ++sequence, method, params }),
    signal: AbortSignal.timeout(12000),
  });
  if (!response.ok) throw new Error(`MCP HTTP ${response.status}`);
  session = response.headers.get('Mcp-Session-Id') || session;
  const body = await response.json();
  if (body.error) throw new Error('MCP protocol error');
  return body.result;
}

try {
  await rpc('initialize', {
    protocolVersion: '2024-11-05', capabilities: {},
    clientInfo: { name: 'live-news-smoke', version: '1.0' },
  });
  const listed = await rpc('tools/list', {});
  if (!listed.tools.some(tool => tool.name === 'get_financial_news'
      && tool.inputSchema.properties.symbol)) throw new Error('Missing symbol schema');
  for (const symbol of ['MSFT', 'NEE']) {
    const result = await rpc('tools/call', {
      name: 'get_financial_news', arguments: { symbol },
    });
    const text = result.content?.map(item => item.text || '').join('\n') || '';
    const count = Number(text.match(/Total articles: (\d+)/)?.[1] || 0);
    const latest = text.match(/\*\*Published:\*\* ([\dTZ:.-]+)/)?.[1];
    if (result.isError || !count || !latest) {
      const status = text.match(/HTTP (\d{3})/)?.[1];
      console.error(JSON.stringify({
        event: 'financial_news_live_probe_failed', symbol,
        providerHttpStatus: status, isError: Boolean(result.isError), count,
      }));
      process.exitCode = 1;
    } else {
      console.log(JSON.stringify({ event: 'financial_news_live_probe', symbol, count, latest }));
    }
  }
  if (!process.exitCode) console.log('FINANCIAL_NEWS_LIVE_SMOKE_OK');
} catch {
  console.error('Financial news live probe failed (transport/schema); no credentials logged.');
  process.exitCode = 1;
}
