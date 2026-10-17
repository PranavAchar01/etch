// Editor icons: 18px grid, 1.5 stroke, currentColor.
const P = {
  copy: '<rect x="6" y="6" width="8.5" height="8.5" rx="1.6"/><path d="M12 6V4.6c0-.9-.7-1.6-1.6-1.6H4.6C3.7 3 3 3.7 3 4.6v5.8c0 .9.7 1.6 1.6 1.6H6"/>',
  paste: '<rect x="4" y="3.5" width="10" height="12" rx="1.6"/><path d="M7 3.5V3c0-.6.4-1 1-1h2c.6 0 1 .4 1 1v.5M7 8h4M7 11h3"/>',
  dup: '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="7" y="7" width="8" height="8" rx="1.5"/>',
  del: '<path d="M3.5 5h11M7.5 5V3.5h3V5M5 5l.7 9.2c0 .5.5.8 1 .8h4.6c.5 0 1-.3 1-.8L13 5"/>',
  undo: '<path d="M6 4.5 3 7.5l3 3"/><path d="M3.5 7.5H11a3.5 3.5 0 0 1 0 7H8"/>',
  redo: '<path d="m12 4.5 3 3-3 3"/><path d="M14.5 7.5H7a3.5 3.5 0 0 0 0 7h3"/>',
  eye: '<path d="M1.8 9S4.5 4 9 4s7.2 5 7.2 5-2.7 5-7.2 5-7.2-5-7.2-5z"/><circle cx="9" cy="9" r="2.2"/>',
  eyeOff: '<path d="M3 3l12 12M7.4 4.2A7 7 0 0 1 9 4c4.5 0 7.2 5 7.2 5a12 12 0 0 1-2 2.6M11.1 11.3A2.2 2.2 0 0 1 6.8 9.1M5 5.6A12 12 0 0 0 1.8 9S4.5 14 9 14c1 0 2-.2 2.8-.6"/>',
  lock: '<rect x="4" y="8" width="10" height="7.5" rx="1.6"/><path d="M6 8V6a3 3 0 0 1 6 0v2"/>',
  unlock: '<rect x="4" y="8" width="10" height="7.5" rx="1.6"/><path d="M6 8V6a3 3 0 0 1 5.8-1"/>',
  isolate: '<circle cx="9" cy="9" r="2.6"/><path d="M9 2v2.2M9 13.8V16M2 9h2.2M13.8 9H16"/>',
  ask: '<path d="M3 4.8C3 3.8 3.8 3 4.8 3h8.4c1 0 1.8.8 1.8 1.8v6c0 1-.8 1.8-1.8 1.8H8l-3.4 2.6v-2.6h0c-.9 0-1.6-.8-1.6-1.8z"/>',
  change: '<path d="M11.5 3.5l3 3L7 14H4v-3z"/>',
  chev: '<path d="m7 4.5 4.5 4.5L7 13.5"/>',
  panel: '<rect x="2.5" y="3" width="13" height="12" rx="2.2"/><path d="M11 3v12"/>',
  showAll: '<path d="M1.8 9S4.5 4 9 4s7.2 5 7.2 5-2.7 5-7.2 5-7.2-5-7.2-5z"/><circle cx="9" cy="9" r="2.2"/><path d="M14 1.8v3M12.5 3.3h3"/>',
  close: '<path d="m5 5 8 8M13 5l-8 8"/>',
  search: '<circle cx="8" cy="8" r="4.8"/><path d="m11.6 11.6 3.4 3.4"/>',
};
export const ic = (name, size = 18) => `<svg class="ic" width="${size}" height="${size}" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || ""}</svg>`;
