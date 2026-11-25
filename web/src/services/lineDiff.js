/**
 * Minimal line diff for the config editor's "review before save".
 *
 * Trims the common prefix and suffix, then runs an LCS on what is left, so a
 * small edit to a long file stays cheap. Returns a list of
 * { type: 'same' | 'add' | 'del', text, oldNo, newNo } entries.
 */

const MAX_LCS_CELLS = 4_000_000;

export function lineDiff(oldText, newText) {
  const a = (oldText || '').split('\n');
  const b = (newText || '').split('\n');

  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }

  const out = [];
  for (let i = 0; i < start; i++) {
    out.push({ type: 'same', text: a[i], oldNo: i + 1, newNo: i + 1 });
  }

  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const n = midA.length;
  const m = midB.length;

  if (n * m > MAX_LCS_CELLS) {
    // Too big to align: show it as a block replacement
    midA.forEach((text, i) => out.push({ type: 'del', text, oldNo: start + i + 1 }));
    midB.forEach((text, j) => out.push({ type: 'add', text, newNo: start + j + 1 }));
  } else {
    // lcs[i][j] = LCS length of midA[i:] and midB[j:]
    const lcs = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        lcs[i][j] = midA[i] === midB[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n || j < m) {
      if (i < n && j < m && midA[i] === midB[j]) {
        out.push({ type: 'same', text: midA[i], oldNo: start + i + 1, newNo: start + j + 1 });
        i++;
        j++;
      } else if (j < m && (i >= n || lcs[i][j + 1] >= lcs[i + 1][j])) {
        out.push({ type: 'add', text: midB[j], newNo: start + j + 1 });
        j++;
      } else {
        out.push({ type: 'del', text: midA[i], oldNo: start + i + 1 });
        i++;
      }
    }
  }

  const shift = endB - endA;
  for (let i = endA; i < a.length; i++) {
    out.push({ type: 'same', text: a[i], oldNo: i + 1, newNo: i + 1 + shift });
  }
  return out;
}

/**
 * Collapse runs of unchanged lines, keeping `context` lines around changes.
 * Collapsed runs become { type: 'skip', count }.
 */
export function withContext(diff, context = 3) {
  const keep = new Array(diff.length).fill(false);
  diff.forEach((d, i) => {
    if (d.type !== 'same') {
      for (let k = Math.max(0, i - context); k <= Math.min(diff.length - 1, i + context); k++) {
        keep[k] = true;
      }
    }
  });
  const out = [];
  let skipped = 0;
  diff.forEach((d, i) => {
    if (keep[i]) {
      if (skipped) out.push({ type: 'skip', count: skipped });
      skipped = 0;
      out.push(d);
    } else {
      skipped++;
    }
  });
  if (skipped) out.push({ type: 'skip', count: skipped });
  return out;
}

export function diffStats(diff) {
  return {
    added: diff.filter((d) => d.type === 'add').length,
    removed: diff.filter((d) => d.type === 'del').length
  };
}
