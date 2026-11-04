// Test-only Axios adapter. Loaded explicitly by the SDK integration test process.
import axios, { AxiosError } from 'axios';
import assert from 'node:assert/strict';

axios.defaults.adapter = async config => {
  const { symbol, from, to } = config.params;
  assert.equal(config.timeout, 8000);
  assert.equal(config.baseURL, undefined);
  assert.equal(config.params.token, 'fixture-secret-not-a-real-key');
  if (symbol) {
    assert.equal(config.url, 'https://finnhub.io/api/v1/company-news');
    const now = new Date();
    assert.equal(to, now.toISOString().slice(0, 10));
    assert.equal(from, new Date(+now - 7 * 86400000).toISOString().slice(0, 10));
  } else {
    assert.equal(config.url, 'https://finnhub.io/api/v1/news');
    assert.equal(config.params.category, 'general');
  }
  if (['AUTH', 'DENY', 'RATE', 'SLOW'].includes(symbol)) {
    const status = symbol === 'AUTH' ? 401 : symbol === 'DENY' ? 403 : 429;
    throw new AxiosError(
      `private config token=${config.params.token}`,
      symbol === 'SLOW' ? 'ECONNABORTED' : 'ERR_BAD_REQUEST',
      config, undefined, symbol === 'SLOW' ? undefined : { status, config },
    );
  }
  if (symbol === 'BAD') return { data: { error: 'provider error' } };
  if (symbol === 'EMPTY') return { data: [] };
  const datetime = Math.floor(Date.now() / 1000) - 60;
  const article = (headline, offset, extra = {}) => ({
    headline, datetime: datetime - offset, summary: 'Provider summary',
    source: 'Fixture Wire', url: `https://example.com/${offset}`,
    related: symbol, ...extra,
  });
  const data = [
    article('Older company update', 600),
    article('Quarterly earnings announcement', 0),
    article('Duplicate', 10, { url: 'https://example.com/0' }),
    article('Stale', 8 * 86400),
    article('Future', -86400),
    article('Invalid timestamp', 12, { datetime: NaN }),
    article('Wrong company', 20, { related: 'OTHER' }),
    article('Unsafe link', 30, { url: 'javascript:alert(1)' }),
    article('General Federal Reserve outlook', 40, { related: '' }),
    article('Bad summary', 50, { summary: { unexpected: true } }),
  ];
  if (symbol === 'LIMIT') {
    return { data: Array.from({ length: 15 }, (_, index) =>
      article('Bounded result', index, { summary: 'x'.repeat(5000) })) };
  }
  return { data };
};
