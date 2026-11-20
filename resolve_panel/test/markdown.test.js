'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { renderMarkdown } = require('../js/markdown');

test('plain text becomes a paragraph, single newlines become line breaks', () => {
  assert.equal(renderMarkdown('one\ntwo'), '<p>one<br>two</p>');
});

test('blank lines separate paragraphs', () => {
  assert.equal(renderMarkdown('a\n\nb'), '<p>a</p><p>b</p>');
});

test('bold, italic and inline code', () => {
  assert.equal(renderMarkdown('a **b** *c* `d`'), '<p>a <strong>b</strong> <em>c</em> <code>d</code></p>');
});

test('bullet and numbered lists', () => {
  assert.equal(renderMarkdown('- a\n- b'), '<ul><li>a</li><li>b</li></ul>');
  assert.equal(renderMarkdown('1. a\n2. b'), '<ol><li>a</li><li>b</li></ol>');
});

test('fenced code is escaped and kept verbatim', () => {
  assert.equal(renderMarkdown('```\n<b>x</b> **y**\n```'), '<pre><code>&lt;b&gt;x&lt;/b&gt; **y**</code></pre>');
});

test('an unterminated fence mid-stream still renders', () => {
  assert.equal(renderMarkdown('```\nhalf'), '<pre><code>half</code></pre>');
});

test('model output can never inject markup', () => {
  const html = renderMarkdown('<img src=x onerror=alert(1)> <script>bad()</script>');
  assert.ok(!html.includes('<img') && !html.includes('<script'));
  assert.ok(html.includes('&lt;img'));
});

test('links are plain text with the address kept visible, never an anchor', () => {
  const html = renderMarkdown('[docs](https://example.com/x)');
  assert.ok(!html.includes('<a'));
  assert.ok(html.includes('docs (https://example.com/x)'));
});

test('headings render as bold lines', () => {
  assert.equal(renderMarkdown('## Plan'), '<p class="md-heading"><strong>Plan</strong></p>');
});

test('empty input renders nothing', () => {
  assert.equal(renderMarkdown(''), '');
});
