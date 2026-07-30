/** Snapchat Memories Sorter — entry point.
 *
 * Each module wires its own part of the interface; we hook them up here, ask
 * the server what state we are in, then hand the screen over to the router.
 */

import { toast } from './dom.js';
import { Boot } from './boot.js';
import { initBrowser } from './browser.js';
import { Home, initHome, refreshHome } from './home.js';
import { initPrompt } from './prompt.js';
import { Router } from './router.js';
import { initSetup, Setup } from './setup.js';
import { initShortcuts } from './shortcuts.js';
import { initSorter, Sorter } from './sorter.js';
import { Sound } from './sound.js';

initPrompt();
initBrowser();
initHome();
initSetup();
initSorter();
initShortcuts();
Sound.watchForUnlock();

try {
  await Boot.refresh();
} catch {
  toast('Cannot reach the local server', true);
}

Home.update(Boot.data);
Setup.init(Boot.data);

// A handler runs when its screen is entered. It may return another screen id to
// redirect — that is how a cold load of /sort without a session lands on home.
// Each one is guarded so navigating from inside the app never re-triggers it.
Router.start({
  home: () => {
    if (Boot.data.autostart && Boot.session) {
      return Boot.session.scan.done ? 'sorter' : 'scanning';
    }
    refreshHome();
  },

  setup: () => {},

  scanning: () => {
    if (Sorter.watching) return;
    if (!Boot.session) return 'home';
    Sorter.watchScan();
  },

  sorter: () => {
    if (Sorter.session) return;
    if (!Boot.session) return 'home';
    if (!Boot.session.scan.done) return 'scanning';
    Sorter.enter(Boot.session);
  },

  done: () => {
    if (Sorter.reportLoaded) return;
    if (!Boot.session) return 'home';
    Sorter.enter(Boot.session).then(() => Sorter.finish());
  },
});
