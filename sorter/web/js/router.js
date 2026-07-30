/** URL routing.
 *
 * Each screen owns a real URL, so Back and Forward behave, a reload lands where
 * you were, and nothing depends on hidden state. The server sends index.html
 * for any path that is not an asset; the mapping below does the rest.
 */

import { $, showScreen } from './dom.js';

export const ROUTES = {
  '/': 'home',
  '/new': 'setup',
  '/scan': 'scanning',
  '/sort': 'sorter',
  '/done': 'done',
};

const PATHS = Object.fromEntries(Object.entries(ROUTES).map(([p, s]) => [s, p]));
const MAX_REDIRECTS = 3;

const screenFor = path => ROUTES[path.replace(/\/+$/, '') || '/'] || 'home';

export const Router = {
  handlers: {},

  /** `handlers` maps a screen id to a function run when that screen is entered. */
  start(handlers) {
    this.handlers = handlers;
    window.addEventListener('popstate', () => this.apply(screenFor(location.pathname), true));
    this.apply(screenFor(location.pathname), true);
  },

  /** Navigate to the URL owning `screen`. */
  go(screen, { replace = false } = {}) {
    const path = PATHS[screen] || '/';
    if (location.pathname !== path) {
      history[replace ? 'replaceState' : 'pushState']({ screen }, '', path);
    }
    this.apply(screen, true);
  },

  /**
   * Show `screen`, letting its handler redirect elsewhere first.
   *
   * Only a *string* return counts as a redirect: handlers are often async and
   * their promise must not be mistaken for a destination.
   */
  apply(screen, replace, depth = 0) {
    const handler = this.handlers[screen];
    const result = handler ? handler() : undefined;
    if (typeof result === 'string' && result !== screen && depth < MAX_REDIRECTS) {
      return this.apply(result, true, depth + 1);
    }
    const path = PATHS[screen] || '/';
    if (location.pathname !== path) {
      history[replace ? 'replaceState' : 'pushState']({ screen }, '', path);
    }
    showScreen(screen);
    $('#' + screen)?.scrollTo?.(0, 0);
  },
};
