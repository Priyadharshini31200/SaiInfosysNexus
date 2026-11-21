import assert from 'node:assert/strict';
import test from 'node:test';
import { stageProjectSecret, takeProjectSecret } from '../src/pages/integrations/projectSecretHandoff.js';

test('issued secret is consumed exactly once by the intended project', () => {
  stageProjectSecret('PROJECT1', 'test-secret');
  assert.equal(takeProjectSecret('PROJECT1'), 'test-secret');
  assert.equal(takeProjectSecret('PROJECT1'), '');
});

test('navigation to a different project discards the staged secret', () => {
  stageProjectSecret('PROJECT1', 'test-secret');
  assert.equal(takeProjectSecret('PROJECT2'), '');
  assert.equal(takeProjectSecret('PROJECT1'), '');
});
