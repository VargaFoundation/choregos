import assert from 'node:assert/strict';
import { test } from 'node:test';

import { slugify } from '../src/slug.js';

test('les accents et la casse disparaissent', () => {
  assert.equal(slugify('Café Éclair'), 'cafe-eclair');
});

test('les séparateurs en trop sont retirés', () => {
  assert.equal(slugify('  --Bonjour, le monde !--  '), 'bonjour-le-monde');
});
