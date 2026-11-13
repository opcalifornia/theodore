'use strict';

// A small, safe markdown renderer for chat replies: paragraphs, headings,
// bullet and numbered lists, **bold**, *italic*, `code` and fenced code
// blocks. Everything is HTML-escaped FIRST and only then re-wrapped in a
// fixed set of tags, so model output can never inject markup (the panel's CSP
// would also block scripts, but escaping is the real guarantee). Links are
// deliberately rendered as plain text: nothing in a chat reply should navigate
// the panel window.

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderInline(text) {
  let out = escapeHtml(text);
  // Links -> just the label (+ the address, so nothing is hidden from the editor).
  out = out.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '$1 ($2)');
  out = out.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  out = out.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
  return out;
}

function renderMarkdown(source) {
  const lines = String(source).replace(/\r\n/g, '\n').split('\n');
  const html = [];
  let i = 0;

  const isBullet = (l) => /^\s*[-*]\s+/.test(l);
  const isNumbered = (l) => /^\s*\d+[.)]\s+/.test(l);
  const isFence = (l) => /^\s*```/.test(l);
  const isHeading = (l) => /^#{1,4}\s+/.test(l);

  while (i < lines.length) {
    const line = lines[i];

    if (isFence(line)) {
      const code = [];
      i += 1;
      // An unterminated fence is normal mid-stream -- render what's there.
      while (i < lines.length && !isFence(lines[i])) { code.push(lines[i]); i += 1; }
      i += 1;
      html.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`);
      continue;
    }
    if (!line.trim()) { i += 1; continue; }

    if (isHeading(line)) {
      html.push(`<p class="md-heading"><strong>${renderInline(line.replace(/^#{1,4}\s+/, ''))}</strong></p>`);
      i += 1;
      continue;
    }
    if (isBullet(line) || isNumbered(line)) {
      const ordered = isNumbered(line);
      const items = [];
      while (i < lines.length && (ordered ? isNumbered(lines[i]) : isBullet(lines[i]))) {
        items.push(`<li>${renderInline(lines[i].replace(/^\s*(?:[-*]|\d+[.)])\s+/, ''))}</li>`);
        i += 1;
      }
      html.push(`<${ordered ? 'ol' : 'ul'}>${items.join('')}</${ordered ? 'ol' : 'ul'}>`);
      continue;
    }

    const para = [];
    while (i < lines.length && lines[i].trim() && !isFence(lines[i]) && !isHeading(lines[i])
           && !isBullet(lines[i]) && !isNumbered(lines[i])) {
      para.push(lines[i]);
      i += 1;
    }
    html.push(`<p>${para.map(renderInline).join('<br>')}</p>`);
  }
  return html.join('');
}

if (typeof module !== 'undefined' && module.exports) module.exports = { renderMarkdown, escapeHtml };
if (typeof window !== 'undefined') window.renderMarkdown = renderMarkdown;
