const test = require('node:test');
const assert = require('node:assert/strict');
const {webcrypto} = require('node:crypto');
const ensureRandomUUID = require('../../web/h3/uuid.js');
test('LAN HTTP fallback generates unique RFC 4122 version 4 IDs', () => {
  const cryptoApi = {getRandomValues: array => webcrypto.getRandomValues(array)};
  ensureRandomUUID(cryptoApi);
  const ids = new Set(Array.from({length: 1000}, () => cryptoApi.randomUUID()));
  assert.equal(ids.size, 1000);
  for (const id of ids) assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});
test('native UUID generation is preserved and setup is idempotent', () => {
  const native = () => 'native';
  const cryptoApi = {getRandomValues() {}, randomUUID: native};
  ensureRandomUUID(cryptoApi);
  ensureRandomUUID(cryptoApi);
  assert.equal(cryptoApi.randomUUID, native);
});
test('missing secure randomness fails closed', () => {
  assert.throws(() => ensureRandomUUID({}), /secure random/);
});
