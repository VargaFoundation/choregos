import assert from 'node:assert/strict';
import { test } from 'node:test';

import { slugify } from '../src/slug.js';

test('accents and case disappear', () => {
  assert.equal(slugify('Café Éclair'), 'cafe-eclair'); // lang: fr-ok - accents are the point
});

test('extra separators are removed', () => {
  assert.equal(slugify('  --Bonjour, le monde !--  '), 'bonjour-le-monde');
});
