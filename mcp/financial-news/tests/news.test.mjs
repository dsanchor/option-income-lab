import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { test } from 'node:test';
import { setTimeout as sleep } from 'node:timers/promises';
import { promisify } from 'node:util';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

test('patched server discovers and calls company/general news using the real MCP SDK', async () => {
  const port = 3197;
  let logs = '';
  const server = spawn(process.execPath, [
    '--import', './fixture-provider.mjs', 'build/index.js',
  ], {
    env: { ...process.env, PORT: String(port), FINNHUB_API_KEY: 'fixture-secret-not-a-real-key' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  server.stdout.on('data', chunk => { logs += chunk; });
  server.stderr.on('data', chunk => { logs += chunk; });
  const client = new Client({ name: 'company-news-test', version: '1.0' });
  const transport = new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${port}/mcp`));
  try {
    let healthy = false;
    for (let attempt = 0; attempt < 50; attempt++) {
      try {
        const response = await fetch(`http://127.0.0.1:${port}/health`);
        healthy = response.ok && (await response.json()).status === 'healthy';
        if (healthy) break;
      } catch {}
      if (server.exitCode !== null) throw new Error(`Server exited: ${logs}`);
      await sleep(100);
    }
    assert.ok(healthy, 'server must become healthy');
    await client.connect(transport);
    const { tools } = await client.listTools();
    const tool = tools.find(item => item.name === 'get_financial_news');
    assert.ok(tool.inputSchema.properties.symbol);
    assert.ok(tool.inputSchema.anyOf);
    const call = args => client.callTool({ name: tool.name, arguments: args });
    const text = result => result.content.map(item => item.text || '').join('\n');

    for (const symbol of ['MSFT', 'NEE']) {
      const result = await call({
        symbol,
        query: `${symbol} why stock is up today November4 2026 unrelated topic words`,
      });
      assert.equal(result.isError, false);
      const output = text(result);
      assert.match(output, /Quarterly earnings announcement/);
      assert.match(output, /Fixture Wire/);
      assert.match(output, /Published:\*\* \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d.000Z/);
      assert.match(output, /https:\/\/example.com\/0/);
      assert.ok(output.indexOf('Quarterly earnings') < output.indexOf('Older company'));
      assert.doesNotMatch(output, /Stale|Future|Wrong company|Invalid timestamp|Unsafe link|Duplicate|Impact:/);
    }
    assert.equal((await call({ symbol: 'msft' })).isError, false);
    const limited = text(await call({ symbol: 'LIMIT' }));
    assert.match(limited, /Total articles: 10/);
    assert.equal((limited.match(/^## \d+\./gm) || []).length, 10);
    assert.ok(limited.length < 19000, 'summaries must be bounded');
    const general = await call({ query: 'Why is the Federal Reserve in the news today?' });
    assert.equal(general.isError, false);
    assert.match(text(general), /General Federal Reserve outlook/);
    assert.doesNotMatch(text(general), /Quarterly earnings/);

    const empty = await call({ symbol: 'EMPTY' });
    assert.equal(empty.isError, false);
    assert.match(text(empty), /No recent articles returned/);
    for (const [symbol, message] of [
      ['AUTH', /HTTP 401/], ['DENY', /HTTP 403/], ['RATE', /HTTP 429/], ['SLOW', /timed out/],
      ['BAD', /request or response failed/],
    ]) {
      const result = await call({ symbol });
      assert.equal(result.isError, true);
      assert.match(text(result), message);
      assert.doesNotMatch(JSON.stringify(result), /fixture-secret|private config/);
    }
    for (const args of [
      {}, { query: '' }, { query: 'x'.repeat(301) },
      { symbol: 'MSFT?token=leak' }, { query: 'why is the stock up today' },
    ]) {
      assert.equal((await call(args)).isError, true);
    }
    const probe = await promisify(execFile)(process.execPath, ['live-smoke.mjs'], {
      env: { ...process.env, PORT: String(port) }, timeout: 10000,
    });
    assert.match(probe.stdout, /FINANCIAL_NEWS_LIVE_SMOKE_OK/);
    assert.match(probe.stdout, /"symbol":"NEE","count":/);
    assert.doesNotMatch(probe.stdout, /Quarterly earnings|fixture-secret|Provider summary/);
    assert.match(logs, /financial_news_completed/);
    assert.match(logs, /"count":0/);
    assert.match(logs, /"status":401/);
    assert.match(logs, /"timedOut":true/);
    assert.doesNotMatch(logs, /fixture-secret|private config|unrelated topic words/);
  } finally {
    await client.close();
    server.kill('SIGTERM');
    await new Promise(resolve => {
      if (server.exitCode !== null) resolve();
      else server.once('exit', resolve);
    });
  }
});
